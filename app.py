import os
from datetime import datetime, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx

import auth
import task_dispatch
from review_signals import (GATE_THRESHOLD, SCORE_NOTE, SCORE_NOTE_SHORT, risk_detail,
                            risk_signal, verdict_word)
from line_item_check import contradiction
from storage import DEFAULT_DB_PATH, StorageManager, connect

# =====================================================================
# 1. Page Configuration
# =====================================================================
st.set_page_config(
    page_title="Invoice approvals",
    layout="wide",
    initial_sidebar_state="expanded",
)

DB_PATH = DEFAULT_DB_PATH

def load_data(db_file: str = DB_PATH) -> pd.DataFrame:
    """Loads invoices from the normalised schema. Money is converted to dollars here, at the edge.

    Joins email_messages so the Original Source panel can show who actually sent the
    document, instead of just carrying the FK. email_id is nullable (a file dropped
    straight into inbox/ never had an email), so this is a LEFT JOIN: an invoice with no
    email must still appear in the table, just with the source columns empty.
    """
    if not os.path.exists(db_file):
        return pd.DataFrame()

    conn = connect(db_file)
    query = """
        SELECT
            i.invoice_id AS id,
            i.run_id,
            i.file_name,
            i.validation_status,
            i.approval_status,
            i.reviewed_at,
            i.validation_score,
            i.total_source,
            i.vendor_source,
            i.document_type,
            i.reconciliation,
            i.invoice_number,
            i.vendor_name,
            i.invoice_date,
            i.total_cents / 100.0 AS total_amount,
            i.currency,
            i.archive_path,
            i.email_id,
            i.category,
            i.summary,
            i.review_note,
            i.review_note_at,
            i.processed_at AS system_processed_at,
            e.sender AS email_sender,
            e.subject AS email_subject,
            e.received_at AS email_received_at,
            -- Who signed the decision. Luke's migration 013 added invoices.reviewed_by; this
            -- resolves it to a name so History can show a person rather than an integer.
            -- LEFT JOIN because reviewed_by is NULL for everything the system approved on its
            -- own, which is most of the table.
            u.display_name AS reviewer_name
        FROM invoices i
        LEFT JOIN email_messages e ON e.email_id = i.email_id
        LEFT JOIN users u ON u.user_id = i.reviewed_by
        ORDER BY i.invoice_id DESC
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df

def load_line_items(invoice_id: int) -> pd.DataFrame:
    """Line items now come from their own table, not from the raw_json blob."""
    conn = connect(DB_PATH)
    df = pd.read_sql_query(
        """
        SELECT
            line_no AS "#",
            description AS "Description",
            quantity AS "Qty",
            unit_price_cents / 100.0 AS "Unit Price",
            line_total_cents / 100.0 AS "Line Total",
            CASE is_summary_row WHEN 1 THEN 'yes' ELSE '' END AS "Summary Row"
        FROM line_items
        WHERE invoice_id = ?
        ORDER BY line_no
        """,
        conn,
        params=(invoice_id,),
    )
    conn.close()
    return df

def load_action_items(invoice_id: int) -> pd.DataFrame:
    """Follow-up actions email_ai.py found in the document text (see main.py's
    DocumentIntelligenceRunner), with the model's supporting quote kept alongside so a
    person can check the claim without reopening the file."""
    conn = connect(DB_PATH)
    df = pd.read_sql_query(
        """
        SELECT
            action_item_id,
            task AS "Action",
            owner AS "Owner",
            deadline_text AS "Deadline",
            evidence_quote AS "Evidence",
            is_done
        FROM invoice_action_items
        WHERE invoice_id = ?
        ORDER BY line_no
        """,
        conn,
        params=(invoice_id,),
    )
    conn.close()
    return df

def save_review_note(invoice_id: int, note: str):
    """The note an approver leaves for whoever opens this document next. Migration 013."""
    StorageManager(DB_PATH).set_review_note(invoice_id, note)


def set_action_item_done(action_item_id: int, is_done: bool):
    """A person checking off an action item. Does not touch validation_score or
    approval_status -- an action item is a claim about the document, not a workflow gate."""
    StorageManager(DB_PATH).set_action_item_done(action_item_id, is_done)

def record_decision(record_id: int, decision: str):
    """Records a human decision about an invoice, then opens the work that follows it.

    Writes approval_status, reviewed_at and reviewed_by only. It deliberately does NOT touch:

      status            what the pipeline judged about data quality. Overwriting it would
                        destroy the record that the extractor was not confident.
      validation_score  the measurement itself. It used to be overwritten with 1.0, which
                        erased the only audit trail we had.

    A row can therefore read NeedsReview and Approved at the same time. That is correct and
    meaningful: the pipeline was not confident, and a person approved it anyway. The two
    columns are labelled "Data Quality" and "Approval" in the UI so it does not read as a
    contradiction.

    The invoice row itself is written by `StorageManager.record_decision`, which is Luke's and
    arrived with the authentication work. Two branches moved this logic in opposite directions,
    his into storage and ours into the follow-up tasks below, and this keeps both: his method
    refuses a decision from an unknown user id, because a decision with no real reviewer is not
    a decision, and the hand-off underneath it is unchanged.
    """
    reviewer_id = st.session_state.get("user_id")
    if reviewer_id is None:
        # Unreachable through the interface, because require_login() stops the page before any
        # of it renders. It is checked anyway: the alternative is an approval recorded against
        # nobody, which is the one thing reviewed_by exists to prevent.
        raise RuntimeError("no signed-in user: record_decision must not be called before login")
    StorageManager(DB_PATH).record_decision(record_id, decision, int(reviewer_id))

    # The work the task stood for is finished, so it leaves the queue. A rejection cancels
    # rather than completes: the document was not accepted, so nothing downstream should
    # treat it as processed.
    store = StorageManager(DB_PATH)
    store.resolve_tasks(record_id, state="Done" if decision == "Approved" else "Cancelled")

    # The hand-off to post-approval work. Approving opens what the document needs next: an
    # Invoice still has to be paid, a Receipt only has to be filed, and an unclassified
    # document goes back to a person. Rejecting opens nothing, because the document was not
    # accepted. Nothing is sent anywhere; the outbox row stays Pending.
    if decision == "Approved":
        followup = store.open_followup_task(record_id)
        if followup:
            task_dispatch.dispatch_task_to_jira(
                store,
                followup["task_id"],
                record_id,
                followup["task_type"],
                followup.get("reason") or f"Invoice {record_id} approved.",
                approval_path="human",
                # Reported by Luke on 2026-09-28. Without this the issue is labelled
                # human-reviewed and attributed to nobody: _reviewer_for_jira returns an empty
                # reviewer and no reporter account, so the one field that says a person was
                # involved is the one field with no person in it.
                reviewer_user_id=int(reviewer_id),
            )

def load_outbox() -> pd.DataFrame:
    """Every outbound row, in whatever state it reached.

    Renamed from `load_pending_notifications` on 2026-09-20, and it no longer filters on state:
    a row that has been sent or has failed is the interesting one, and hiding it left the tab
    unable to show that anything had ever happened.

    FR-6.2 requires every intended notification to be recorded rather than printed.

    `created_at` is selected because the payload decays. The oldest row still reads "NeedsReview
    at score 0.25" for a document that now reads Validated at 1.00: the message was written when
    it was true and nothing rewrites it. Showing the age turns a stale sentence into the honest
    point, which is that a queue nobody drains stops describing the present.
    """
    conn = connect(DB_PATH)
    df = pd.read_sql_query(
        """
        SELECT o.outbox_id, o.task_id, o.invoice_id, o.channel, o.payload, o.created_at,
               o.state, o.sent_at, o.external_ref, o.error,
               i.vendor_name, i.invoice_number, i.file_name,
               t.state AS task_state
        FROM outbound_messages o
        JOIN invoices i ON i.invoice_id = o.invoice_id
        LEFT JOIN tasks t ON t.task_id = o.task_id
        ORDER BY o.created_at DESC
        """,
        conn,
    )
    conn.close()
    # Withdrawn is derived, not stored. A row whose task was cancelled before it was sent
    # belongs to a decision someone took back, and pushing it would act on that decision.
    # Deriving it keeps outbound_messages' CHECK constraint, which a new state would have
    # cost a table rewrite to change.
    df["withdrawn"] = (df["task_state"] == "Cancelled") & (df["state"] != "Sent")
    return df


def load_history() -> pd.DataFrame:
    """Every decision a person made, newest first, including the ones later taken back.

    Reads `invoice_decisions` (migration 016) rather than the three columns on `invoices`,
    because those hold only the decision in force now. Reopening a document clears them, and a
    History built on them lost the approval being taken back along with the fact that anyone
    took it back.

    This is the only place a rejection is visible. A rejected document is not Pending, so it
    leaves the queue, and not Approved, so it never reaches the auto tab.

    Column names are kept from the version that read `invoices`, so the Overview panel and the
    tests read it unchanged: `approval_status` is this row's decision, which can now also be
    Reopened, and `reviewed_at` is when it was made. `current_status` is where the document
    stands now, and `is_latest` marks the row a Reopen button belongs on.
    """
    conn = connect(DB_PATH)
    df = pd.read_sql_query(
        """
        SELECT d.decision_id, d.invoice_id, i.invoice_number, i.vendor_name, i.document_type,
               i.total_cents / 100.0 AS total_amount, i.currency,
               d.decision AS approval_status, d.decided_at AS reviewed_at,
               i.approval_status AS current_status,
               -- SC-4. The score as it stood when the person decided, copied onto the row at
               -- the time. A re-run of main.py can change the invoice's own score afterwards.
               d.validation_score, d.validation_status,
               -- The name, not the id. Reported by Luke on 2026-09-28: it was being stored
               -- by record_decision and shown nowhere.
               u.display_name AS reviewer_name,
               d.decision_id = (SELECT MAX(decision_id) FROM invoice_decisions
                                WHERE invoice_id = d.invoice_id) AS is_latest,
               t.task_type, t.state AS task_state, t.resolved_at
        FROM invoice_decisions d
        JOIN invoices i ON i.invoice_id = d.invoice_id
        LEFT JOIN users u ON u.user_id = d.decided_by
        -- The task this decision closed: the first Review or Approve task resolved at or after
        -- it. A Reopened row closes the follow-up instead and opens a Review task, which the
        -- screen says in words rather than through this join.
        LEFT JOIN tasks t ON d.decision <> 'Reopened' AND t.task_id = (
            SELECT task_id FROM tasks
            WHERE invoice_id = d.invoice_id
              AND task_type IN ('Review', 'Approve')
              AND resolved_at IS NOT NULL AND resolved_at >= d.decided_at
            ORDER BY resolved_at, task_id
            LIMIT 1
        )
        ORDER BY d.decided_at DESC, d.decision_id DESC
        """,
        conn,
    )
    conn.close()
    df["is_latest"] = df["is_latest"].astype(bool)
    return df


def jira_ready() -> bool:
    """Whether pressing Push would actually reach Jira.

    Read fresh rather than cached: the answer changes the moment someone writes a .env, and a
    button that lies about being able to act is worse than one that explains why it cannot.
    """
    try:
        from jira_client import JiraClient
        return JiraClient().is_configured()
    except Exception:
        return False


# Every timestamp this project writes comes from SQLite's datetime('now'), which is UTC. The
# people reading the screen are in Sydney, and a note saved at 3pm reading "05:00" was reported
# as a defect on 2026-09-29. Storage stays UTC, so rows written on different machines still
# compare; the conversion happens here, at the edge, the same way money does.
try:
    LOCAL_TZ = ZoneInfo("Australia/Sydney")
    LOCAL_TZ_LABEL = "Sydney"
except ZoneInfoNotFoundError:
    # A slim container image can ship without the zone database. Saying UTC is honest; showing
    # UTC unlabelled was the defect.
    LOCAL_TZ, LOCAL_TZ_LABEL = timezone.utc, "UTC"


def local_time(value, *, seconds: bool = False) -> str:
    """A stored UTC timestamp as Sydney wall-clock time, or "-" when there is none."""
    if value is None or (not isinstance(value, str) and pd.isna(value)) or value == "":
        return "-"
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S" if seconds else "%Y-%m-%d %H:%M")


def push_to_jira(row) -> None:
    """Send one outbox row to Jira, through the path that already exists.

    `task_dispatch.dispatch_task_to_jira` looks for an existing Pending or Failed outbox row for
    the same task and reuses it rather than queueing a second one, so retry was designed in from
    the start. This button is that retry, with a person pressing it.

    Restored 2026-09-29. Commit 75a7d8d removed this function when the tabs became the sidebar
    and left its three callers in place. Nothing failed, because every Push button is disabled
    until Jira is configured, so the first person to configure Jira would have been the first to
    see the NameError.

    A withdrawn row is refused. Its task was cancelled when the document was reopened, and
    sending it would open a payment issue for a decision that no longer stands.
    """
    if row.get("withdrawn"):
        return
    store = StorageManager(DB_PATH)
    task = store.task_by_id(int(row["task_id"])) if row["task_id"] else None
    task_dispatch.dispatch_task_to_jira(
        store,
        int(row["task_id"]),
        int(row["invoice_id"]),
        task["task_type"] if task else "Payment",
        (task["reason"] if task else None) or row["payload"],
        approval_path="human",
    )


def reopen_for_review(invoice_id: int) -> None:
    """A person taking a decision back. The storage method does the work and the checks."""
    reviewer_id = st.session_state.get("user_id")
    if reviewer_id is None:
        raise RuntimeError("no signed-in user: reopen_for_review must not be called before login")
    StorageManager(DB_PATH).reopen_for_review(int(invoice_id), int(reviewer_id))


def load_email_for(invoice_row) -> dict:
    """The message that delivered a document, for the dialog's collapsed section."""
    return {
        "sender": invoice_row.get("email_sender"),
        "subject": invoice_row.get("email_subject"),
        "received_at": invoice_row.get("email_received_at"),
        "body": _email_body(invoice_row.get("email_id")),
    }


def _email_body(email_id) -> str:
    if email_id is None or pd.isna(email_id):
        return ""
    conn = connect(DB_PATH)
    row = conn.execute(
        "SELECT body_text FROM email_messages WHERE email_id = ?", (int(email_id),)
    ).fetchone()
    conn.close()
    return (row["body_text"] if row else "") or ""


def follow_up_for(document_type: str) -> str:
    """What approving this document will create in Jira, named before the button is pressed.

    Mirrors storage.open_followup_task. The screen states the consequence because
    record_decision dispatches to Jira immediately, with no confirmation step, and an approver
    pressing a button should know it creates work for someone else in another system.
    """
    return {"Invoice": "Payment", "Receipt": "File"}.get(document_type, "Review")


# =====================================================================
# Presentation
#
# Structure only: this file decides what appears and in what order, never how it looks.
#
# Colour, type, radius and font come from .streamlit/config.toml, not from here. Streamlit
# 1.62.0 exposes 277 theme options and most of the token layer is expressible in them, which
# is a supported interface. The CSS below reaches into Streamlit's own markup and is therefore
# the part that breaks on upgrade, so it carries only what configuration cannot express.
#
# See fe-theme-spec.md, and approval-screen-components.html for the tokens.
# =====================================================================

st.markdown("""
<style>
/* The tokens. config.toml already sets the half-dozen Streamlit itself understands; these
   exist because the cards, strips and chips are built out of st.markdown and need them by
   name. approval-screen-components.html is the source of truth for every value here. */
:root {
  /* The neutrals carry a navy tint rather than no hue at all. #F6F6F7 and #FFFFFF sat 1.08:1
     apart and both read as an absence of colour, which is what made the page look empty. The
     tinted ground reads 1.13:1 against the same white card and points at the accent below.
     fe-theme-v2-spec.md §4 carries the full before-and-after with the measured contrast. */
  --ground:#E8EDF7;  --surface:#FFFFFF;  --surface-sunk:#F7F8FC;
  --border-subtle:#E6E9F2;  --border:#D8DCE8;  --border-hover:#B9C0D1;
  --text:#161A23;  --text-strong:#3A4152;  --text-muted:#5F667A;
  --text-faint:#98A0B3;  --text-disabled:#BBC2D0;
  --accent:#5B5BD6;  --accent-hover:#4F4FC9;  --accent-text:#3E3EA8;  --accent-wash:#ECECFA;
  --navy:#243352;  --navy-wash:#E8ECF5;
  /* Risk is the one hue outside the indigo-to-navy family, and it stays. A caution band in
     blue reads as information rather than as a warning. --caution-wash was #FDFCFB, which is
     99% white: on a tinted ground it would have read as a lighter patch than its surroundings
     and inverted the signal. See fe-theme-v2-spec.md §0.3. */
  --caution:#A65A1E;  --caution-text:#8A4A18;  --caution-wash:#FAF0E2;
  /* --positive fills dots and one SVG stroke, never text, so 3.54:1 is judged against WCAG
     1.4.11's 3:1 for non-text components. --positive-text is the one that carries words. */
  --positive:#3D9A50;  --positive-text:#2B7038;  --positive-wash:#E9F2EC;
  --control-fill:#EBEEF5;  --row-hover:#F7F8FC;
  /* Separation is tone and shadow. A generated interface reaches for a border because a border
     is unambiguous; these give an element an edge without drawing one. See fe-theme-v2-spec.md
     §9, which carries the measurement that prompted them. */
  --lift:0 1px 2px rgba(22,26,35,0.06), 0 8px 20px rgba(22,26,35,0.07);
  --lift-soft:0 1px 2px rgba(22,26,35,0.05), 0 4px 10px rgba(22,26,35,0.05);
  --lift-hover:0 1px 2px rgba(22,26,35,0.07), 0 12px 28px rgba(22,26,35,0.10);
  --fill-subtle:rgba(22,26,35,0.045);
}

/* A column of money that does not line up is a functional defect, not a preference. */
[data-testid="stTable"] td, .amount { font-variant-numeric: tabular-nums; }

/* The risk sentence. --caution-text rather than --caution: the lighter ochre measures 5.12:1
   on white and the darker one 6.67:1, and this is the one sentence on the screen a person is
   meant to stop on. */
.signal { color: var(--caution-text); }

/* The document card. The only selectors here are st-key-* prefixes, which this file chooses
   itself by passing key= to the container. Streamlit's own class names are build hashes and
   its data-testid attributes are internal; both change without notice. */
[class*="st-key-doc-"] {
  padding: 0 !important;
  border-radius: 12px;
  overflow: hidden;
  background: var(--surface);
  border-color: transparent !important;
  box-shadow: var(--lift);
}
[class*="st-key-doc-"]:hover { box-shadow: var(--lift-hover); }
[class*="st-key-doc-"] .stMarkdown p { margin: 0; }

.card-head { display:grid; grid-template-columns:44px 1fr auto; gap:16px; align-items:center; padding:18px 24px 16px; }
.doc-icon { width:44px; height:44px; border-radius:10px; background:var(--control-fill);
            display:flex; align-items:center; justify-content:center; }
.card-id { display:flex; flex-direction:column; gap:7px; min-width:0; }
.card-title { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.vendor { font-size:17px; font-weight:600; letter-spacing:-0.01em; color:var(--text); }
.card-meta { font-family:'IBM Plex Mono',monospace; font-size:12.5px; color:var(--text-muted); }
.card-head .amount { font-family:'IBM Plex Mono',monospace; font-size:22px; font-weight:500;
                     text-align:right; white-space:nowrap; color:var(--text); }
.amount-stack { display:flex; flex-direction:column; align-items:flex-end; gap:12px; }

/* The validation score. fe-score-spec.md. The rail is 4px of track, which is a rule rather
   than a block, so the risk strip stays the only filled band on the card per
   fe-theme-v2-spec.md §0.1. Colour sits on the rail only: the number and the verdict wear ink
   tokens, and the verdict word repeats in text what the colour says, so neither greyscale nor
   colour blindness loses it. --positive and --caution are judged here against WCAG 1.4.11's
   3:1 for non-text components, which is the same basis as the dots. */
.score { display:flex; flex-direction:column; gap:5px; width:138px; }
.score-label { font-size:11px; color:var(--text-muted); }
.score-read { display:flex; align-items:baseline; gap:8px; }
.score-value { font-family:'IBM Plex Mono',monospace; font-size:16px; font-weight:500;
               font-variant-numeric:tabular-nums; color:var(--text-strong); }
.score-verdict { font-size:12px; color:var(--text-muted); }
.score-track { position:relative; display:block; height:4px; border-radius:2px; }
.score-fill { position:absolute; left:0; top:0; bottom:0; border-radius:2px; }
/* The gate mark is a notch cut in the surface colour rather than a line drawn over the rail.
   Drawn in --text-muted it was legible on the unfilled track and almost invisible where it
   mattered most, which is inside the fill: a near miss like 0.85 puts the mark under the green.
   A notch reads against the fill and the track alike, because it is the absence of both. */
.score-tick { position:absolute; top:0; bottom:0; width:2px; background:var(--surface);
              box-shadow:0 0 0 0.5px rgba(31,41,66,.18); }
.score-gate { font-family:'IBM Plex Mono',monospace; font-size:10.5px; color:var(--text-muted); }

.chip { display:inline-flex; align-items:center; gap:7px; font-size:12px; color:var(--text-strong);
        background:var(--fill-subtle); border-radius:7px; padding:4px 10px; margin-right:6px; }
.dot { width:6px; height:6px; border-radius:50%; flex:none; }

/* The sentence comes from review_signals.py. Nothing in this file writes signal copy. */
.signal-strip { display:flex; align-items:center; gap:12px; padding:12px 24px; flex-wrap:wrap;
                border-top:1px solid var(--border-subtle); background:var(--caution-wash); }
.signal-strip.clear { background:var(--surface-sunk); }
.verdict { font-size:12px; font-weight:500; border-radius:20px; padding:3px 10px; flex:none; }
.verdict-look { color:var(--caution-text); background:#F6E8DC; }
.verdict-clear { color:var(--positive-text); background:var(--positive-wash); }
.signal { color: var(--caution-text); font-size:14px; }
.signal-detail { font-size:13px; color:var(--text-muted); }

[class*="st-key-foot-"] { padding:13px 24px !important; border-top:1px solid var(--border-subtle); }
.chips { display:flex; align-items:center; flex-wrap:wrap; row-gap:6px; }
.chips.decided { justify-content:flex-end; font-size:12.5px; color:var(--text-muted); }
/* The action sits at the card's right edge, not at the left of whatever column it landed in. */
[class*="st-key-rev-"] { display:flex; justify-content:flex-end; }
.queue-count { font-size:13px; color:var(--text-muted); }

.empty { display:flex; flex-direction:column; align-items:center; gap:9px; padding:28px 24px;
         background:var(--surface); border-radius:12px; box-shadow:var(--lift); }
.empty-mark { width:42px; height:42px; border-radius:50%; background:var(--positive-wash);
              display:flex; align-items:center; justify-content:center; }
.empty-title { font-size:15px; font-weight:600; color:var(--text); }
.empty-note { font-size:13px; color:var(--text-muted); }

/* Overview. Four tiles, one per tab, built from C3 in approval-screen-components.html, which
   was drawn for the queue screen and rejected there as duplication of the tab labels. */
/* Overview is a page, not a grid of counters. Three tiles across the top, then one section
   per tab in the order a person asks about them, each carrying its own rows. */
.ov-strip { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-bottom:6px; }
/* The tiles were three white boxes on a near-white page. The left rule gives each one an edge
   to sit against, and the sunk surface separates them from the panels below, which are the
   things that actually hold content. */
.ov-tile { background:var(--surface-sunk);
           border-left:3px solid var(--border-hover); border-radius:10px;
           box-shadow:var(--lift-soft); padding:14px 16px;
           display:flex; flex-direction:column; gap:6px; }
.ov-strip > .ov-tile:nth-child(1) { border-left-color:var(--accent); background:var(--surface); }
.ov-strip > .ov-tile:nth-child(2) { border-left-color:var(--positive); }
.ov-label { font-size:12px; color:var(--text-muted); }
.ov-value { display:flex; align-items:baseline; gap:9px; flex-wrap:wrap; }
.ov-number { font-family:'IBM Plex Mono',monospace; font-size:26px; font-weight:500;
             letter-spacing:-0.01em; color:var(--text); }
.ov-note { font-family:'IBM Plex Mono',monospace; font-size:13px; color:var(--text-muted);
           font-variant-numeric:tabular-nums; }

.sec-head { display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin:0;
            line-height:2.2; }
.sec-icon { width:24px; height:24px; border-radius:7px; background:var(--control-fill);
            color:var(--text-muted); display:inline-flex; align-items:center;
            justify-content:center; flex:none; }
/* Each section carries an identity: a 3px rule on the header band and on the panel below it,
   and a tinted icon chip. The hook is the st-key-* class this file chooses itself by passing
   key= to the container, so none of it depends on a Streamlit internal.

   Identity is a rule and a chip, never a fill. Risk is the only thing on this screen that gets
   a filled band, and that is what stops four section colours from competing with it.
   fe-theme-v2-spec.md §4. */
[class*="st-key-sechead-"] { margin:20px 0 10px !important; padding:8px 14px !important;
                             background:var(--navy-wash);
                             border:1px solid var(--border-subtle);
                             border-left:3px solid var(--border-hover);
                             border-radius:6px; }
[class*="st-key-sechead-awaiting"] { border-left-color:var(--accent); }
[class*="st-key-sechead-auto"] { border-left-color:var(--positive); }
[class*="st-key-sechead-awaiting"] .sec-icon { background:var(--accent-wash); color:var(--accent-text); }
[class*="st-key-sechead-auto"] .sec-icon { background:var(--positive-wash); color:var(--positive-text); }

/* The Outbox header is amber only when something actually failed. A permanently amber header
   would claim a problem on the days nothing has, which is most days, and a warning that is
   always on is not a warning. The -failed suffix comes from overview_body, not from CSS.
   These two rules must stay after the plain outbox ones: [class*=] matches both. */
[class*="st-key-sechead-outbox-failed"] { border-left-color:var(--caution); }
[class*="st-key-sechead-outbox-failed"] .sec-icon { background:var(--caution-wash); color:var(--caution-text); }

[class*="st-key-doc-ov-"] { border-left:3px solid var(--accent) !important; }
[class*="st-key-open-"] { display:flex; justify-content:flex-end; }
/* Row actions sit at the panel's right edge, like the one on the document card. */
[class*="st-key-ovauto-"] [data-testid="stColumn"]:last-child,
[class*="st-key-ovout-"] [data-testid="stColumn"]:last-child,
[class*="st-key-ovhist-"] [data-testid="stColumn"]:last-child { display:flex; align-items:flex-end; }
/* The column's own block fills it, so aligning the column moved nothing: the block has to align
   its children, and the markdown beside the button must not add a margin of its own. */
[class*="st-key-ovauto-"] [data-testid="stColumn"]:last-child > [data-testid="stVerticalBlock"],
[class*="st-key-ovout-"] [data-testid="stColumn"]:last-child > [data-testid="stVerticalBlock"],
[class*="st-key-ovhist-"] [data-testid="stColumn"]:last-child > [data-testid="stVerticalBlock"] {
  align-items:flex-end; justify-content:center; }
[class*="st-key-ovauto-"] [data-testid="stMarkdownContainer"],
[class*="st-key-ovout-"] [data-testid="stMarkdownContainer"],
[class*="st-key-ovhist-"] [data-testid="stMarkdownContainer"] { margin:0 !important; }
/* Outbox rows: the Push button belongs at the row's right edge, level with the row, and the
   checkbox in the first column is centred against the same line. */
[class*="st-key-out-"] [data-testid="stColumn"]:last-child > [data-testid="stVerticalBlock"] {
  align-items:flex-end; justify-content:center; }
[class*="st-key-out-"] [data-testid="stColumn"]:first-child > [data-testid="stVerticalBlock"] {
  justify-content:center; }
.sec-title { font-size:14px; font-weight:600; color:var(--text); }
.sec-note { font-size:12.5px; color:var(--text-muted); }

.dense-panel { background:var(--surface); border-radius:12px;
               box-shadow:var(--lift); overflow:hidden; }
.dense { display:grid; grid-template-columns:auto 1fr auto auto auto; align-items:center;
         gap:16px; padding:11px 18px; border-radius:10px; }
[class*="st-key-ovauto-"]:hover .dense, [class*="st-key-ovout-"]:hover .dense,
[class*="st-key-ovhist-"]:hover .dense { background:var(--row-hover); }
.dense:last-child { border-bottom:none; }
.dense-left { font-size:13.5px; color:var(--text); }
.dense-doc { font-family:'IBM Plex Mono',monospace; font-size:12.5px; color:var(--text-muted); }
.dense-amount { font-family:'IBM Plex Mono',monospace; font-size:13px;
                font-variant-numeric:tabular-nums; min-width:112px; text-align:right; color:var(--text); }
/* The score column. Tabular figures and a fixed width, because a column of numbers that does
   not align is a column that cannot be scanned, which is the whole reason it is here. */
.dense-score { font-family:'IBM Plex Mono',monospace; font-size:12.5px;
               font-variant-numeric:tabular-nums; min-width:44px; text-align:right;
               color:var(--text-strong); }
.dense-right { font-family:'IBM Plex Mono',monospace; font-size:12px; color:var(--text-muted);
               min-width:44px; text-align:right; }

.ov-line { display:flex; align-items:center; gap:10px; margin:14px 0 0;
           font-size:13px; color:var(--text-strong); }
.ov-more { font-size:13px; color:var(--text-muted); margin:10px 0 0; }
.ov-foot { font-size:12.5px; color:var(--text-muted); margin:18px 0 0;
           padding:14px 18px; border-radius:12px; background:var(--fill-subtle); }
.ai-find { display:flex; flex-direction:column; gap:3px; padding:10px 13px; margin-bottom:6px;
           background:var(--fill-subtle); border-radius:9px; }
.ai-task { font-size:13.5px; color:var(--text); }
.ai-quote { font-size:12.5px; color:var(--text-muted); font-style:italic; }
.note-when { font-size:12px; color:var(--text-muted); margin:0; }

.tab-note { font-size:13px; line-height:1.6; color:var(--text-muted); max-width:none;
            margin:0 0 14px; padding:13px 16px; background:var(--fill-subtle);
            border-radius:12px; }
.tab-warn { font-size:13px; line-height:1.6; color:var(--caution-text);
            background:var(--caution-wash); border-radius:12px; padding:13px 16px; margin:0 0 14px; }
.tab-note code, .tab-warn code { font-family:'IBM Plex Mono',monospace; font-size:12px; }

/* Outbox rows that can be pushed. The Teams rows stay in a plain table: they have no action,
   because nothing in this codebase has written one since 2026-09-08 and none ever will. */
[class*="st-key-out-"] { padding:13px 18px !important; border-radius:12px;
                         background:var(--surface); box-shadow:var(--lift-soft); }
.out-row { display:flex; flex-direction:column; gap:5px; }
.out-head { display:flex; align-items:center; gap:10px; }
.out-doc { font-family:'IBM Plex Mono',monospace; font-size:12.5px; color:var(--text); }
.out-vendor { font-size:13px; color:var(--text-strong); }
.out-when { font-family:'IBM Plex Mono',monospace; font-size:12px; color:var(--text-muted); margin-left:auto; }
.out-payload { font-size:12.5px; color:var(--text-muted); }
.out-error { font-size:12px; color:var(--caution-text); }
[class*="st-key-push-"] { display:flex; justify-content:flex-end; }

/* History. One row per decision a person made. */
[class*="st-key-hist-"] { padding:12px 18px !important; border-radius:12px;
                          background:var(--surface); box-shadow:var(--lift-soft); }
/* Rows that carry an action. The border comes from the panel they sit in, not from each row. */
[class*="st-key-ovpanel-"] { background:var(--surface); border:none;
  border-radius:12px; box-shadow:var(--lift); overflow:hidden;
  padding:6px !important; }
/* The section's identity rule. These must stay after the shorthand above: both selectors have
   the same specificity, so the later `border` would otherwise repaint this edge. */
[class*="st-key-ovpanel-auto"] { border-left:3px solid var(--positive); }
[class*="st-key-ovpanel-out"] { border-left:3px solid var(--border-hover); }
[class*="st-key-ovpanel-out-failed"] { border-left-color:var(--caution); }
[class*="st-key-ovpanel-hist"] { border-left:3px solid var(--border-hover); }
[class*="st-key-ovauto-"], [class*="st-key-ovout-"], [class*="st-key-ovhist-"] {
  padding:2px 18px 2px 4px !important; border-radius:10px; }
[class*="st-key-ovpanel-"] > div > div:last-child [class*="st-key-ov"] { border-bottom:none; }
[class*="st-key-ovauto-"] .dense, [class*="st-key-ovout-"] .dense,
[class*="st-key-ovhist-"] .dense { border-bottom:none; padding:10px 14px; }
.hist { display:flex; align-items:center; gap:6px 14px; flex-wrap:wrap; }
.hist-decision { font-size:13px; font-weight:500; color:var(--text); min-width:72px; }
.hist-vendor { font-size:13.5px; color:var(--text); }
.hist-doc { font-family:'IBM Plex Mono',monospace; font-size:12.5px; color:var(--text-muted); }
.hist-amount { font-family:'IBM Plex Mono',monospace; font-size:13px; font-variant-numeric:tabular-nums; color:var(--text); }
.hist-score { font-family:'IBM Plex Mono',monospace; font-size:12.5px;
              font-variant-numeric:tabular-nums; color:var(--text-muted); }
.hist-who { font-size:12.5px; color:var(--text); }
.hist-when { font-family:'IBM Plex Mono',monospace; font-size:12px; color:var(--text-muted); margin-left:auto; }
.hist-task { font-size:12px; color:var(--text-muted); }
/* UI consistency pass, 2026-10-07. Row actions keep one size everywhere and never wrap.
   Child widgets inherit their row's key prefix, so a row's card rule (padding, white
   background, shadow) also landed on its own buttons and drew a box round each one. */
[class*="st-key-hist-"][class*="-open"], [class*="st-key-hist-"][class*="-reopen"],
[class*="st-key-ovauto-"][class*="-act"], [class*="st-key-ovout-"][class*="-act"],
[class*="st-key-ovhist-"][class*="-act"] {
  padding:0 !important; background:transparent !important; box-shadow:none !important;
  border-radius:0 !important; width:fit-content !important; flex:0 0 auto !important; }
[class*="st-key-dl-"] button, [class*="st-key-hist-"] button, [class*="st-key-ovauto-"] button, [class*="st-key-ovout-"] button,
[class*="st-key-ovhist-"] button, [class*="st-key-open-"] button, [class*="st-key-out-"] button,
[class*="st-key-rev-"] button, [class*="st-key-push-"] button {
  min-width:92px; height:36px; min-height:36px; padding:0 16px; border-radius:8px;
  white-space:nowrap; }
[class*="st-key-hist-"] button p, [class*="st-key-ovauto-"] button p, [class*="st-key-ovout-"] button p,
[class*="st-key-ovhist-"] button p, [class*="st-key-open-"] button p, [class*="st-key-rev-"] button p,
[class*="st-key-push-"] button p { white-space:nowrap; margin:0; }
/* The facts take the room, the buttons keep their width. A button that does not fit drops
   beneath the facts instead of being squeezed. */
[class*="st-key-histline-"] { gap:10px 16px; align-items:center; padding:2px 0 6px; }
[class*="st-key-footline-"] { gap:10px 12px; align-items:center; }
[class*="st-key-footline-"] > [data-testid="stElementContainer"]:first-child {
  flex:1 1 380px; min-width:0; }
[class*="st-key-histline-"] > [data-testid="stElementContainer"]:first-child {
  flex:1 1 440px; min-width:0; }
.page-eyebrow { font-size:11.5px; font-weight:600; letter-spacing:0.09em; text-transform:uppercase;
                color:var(--text-muted); margin:0 0 -6px; }
.page-sub { font-size:13.5px; line-height:1.55; color:var(--text-muted); margin:-4px 0 14px;
            max-width:760px; }
.hist-note { font-size:12.5px; line-height:1.5; color:var(--text-muted); margin:6px 0 8px;
             padding-left:22px; }
.hist-facts { display:flex; flex-wrap:wrap; gap:4px 18px; margin-top:8px; padding-left:22px;
              font-size:12.5px; color:var(--text-muted); }
.hist-facts b { font-weight:500; color:var(--text); margin-right:4px; }

/* The sidebar. FE-15. Streamlit paints the surface from [theme.sidebar] in config.toml; these
   rules cover the identity block and the saved views, which are markdown and a radio. */
.side-id { display:flex; align-items:center; gap:10px; padding:2px 2px 16px; }
.side-mark { width:26px; height:26px; border-radius:7px; background:var(--navy); flex:none;
             display:flex; align-items:center; justify-content:center; }
.side-name { display:flex; flex-direction:column; line-height:1.25; }
.side-title { font-size:13.5px; font-weight:600; letter-spacing:-0.005em; color:var(--text); }
.side-sub { font-size:11.5px; color:var(--text-muted); }
.side-foot { margin-top:26px; display:flex; align-items:center; gap:10px; padding:8px 9px;
             border-radius:9px; }
.side-avatar { width:26px; height:26px; border-radius:50%; background:var(--border);
               color:var(--text-strong); font-size:11px; font-weight:600; flex:none;
               display:flex; align-items:center; justify-content:center; }
.side-who { font-size:12.5px; font-weight:500; color:var(--text); }

/* The navigation, drawn as approval-screen-design-v3.html draws it: icon, name, count at the
   far edge, the current row filled with the accent wash and its count in a filled pill.

   The selectors come from reading the rendered DOM rather than guessing. Streamlit builds a
   radio option as label[data-testid="stRadioOption"] > div > div > (circle, markdown), and
   carries the selection on data-selected rather than only on the input, which is a more stable
   hook than :has(). The circle is removed; the icon and the count are pseudo elements, because
   a radio label is plain text and this is the only way to get the drawn layout. */
[data-testid="stSidebar"] [role="radiogroup"] { gap:1px; }
[data-testid="stSidebar"] label[data-testid="stRadioOption"] {
  display:flex; align-items:center; gap:10px; border-radius:7px; padding:7px 10px; margin:0;
  font-size:13.5px; color:var(--text-strong); cursor:pointer; position:relative; }
[data-testid="stSidebar"] label[data-testid="stRadioOption"]:hover { background:var(--row-hover); }
/* the circle */
[data-testid="stSidebar"] label[data-testid="stRadioOption"] > div > div > div:first-child {
  display:none; }
[data-testid="stSidebar"] label[data-testid="stRadioOption"] > div,
[data-testid="stSidebar"] label[data-testid="stRadioOption"] > div > div {
  display:flex; align-items:center; gap:0; width:100%; }
[data-testid="stSidebar"] label[data-testid="stRadioOption"] p {
  font-size:13.5px !important; margin:0 !important; }
[data-testid="stSidebar"] label[data-testid="stRadioOption"]::before {
  content:""; width:16px; height:16px; flex:none; margin-right:10px;
  background-repeat:no-repeat; background-position:center; }
[data-testid="stSidebar"] label[data-testid="stRadioOption"]::after {
  margin-left:auto; font-family:'IBM Plex Mono',monospace; font-size:11.5px;
  color:var(--text-muted); }
[data-testid="stSidebar"] label[data-selected="true"] {
  background:var(--accent-wash); color:var(--accent-text); }
[data-testid="stSidebar"] label[data-selected="true"] p { font-weight:500; }
[data-testid="stSidebar"] label[data-selected="true"]::after {
  background:var(--accent); color:#FFFFFF; border-radius:20px; padding:1px 7px; }
/* The list holds two kinds of thing: the five states a document moves through, then the three
   other things there are to look at. The label marks the seam. It hangs off the sixth row's
   text element because that row's own pseudo elements are already the icon and the count. */
[data-testid="stSidebar"] label[data-testid="stRadioOption"]:nth-of-type(6) {
  margin-top:30px; }
[data-testid="stSidebar"] label[data-testid="stRadioOption"]:nth-of-type(6) p::before {
  content:"EXPLORE"; position:absolute; left:10px; top:-22px; font-size:11px;
  font-weight:500; letter-spacing:0.04em; color:var(--text-muted); }

/* The search field carries the icon and the shortcut hint inside it, as the design draws. */
[data-testid="stSidebar"] [data-testid="stTextInputRootElement"] {
  border-radius:8px; position:relative; }
[data-testid="stSidebar"] [data-testid="stTextInputField"] { padding-left:32px; }
[data-testid="stSidebar"] [data-testid="stTextInputRootElement"]::before {
  content:""; position:absolute; left:10px; top:50%; transform:translateY(-50%);
  width:14px; height:14px; pointer-events:none; background-repeat:no-repeat;
  background-image:url("data:image/svg+xml;utf8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 16 16' fill='none'%3E%3Ccircle cx='7' cy='7' r='4.25' stroke='%2398A0B3' stroke-width='1.4'/%3E%3Cpath d='m10.5 10.5 3 3' stroke='%2398A0B3' stroke-width='1.4' stroke-linecap='round'/%3E%3C/svg%3E"); }
[data-testid="stSidebar"] [data-testid="stTextInputRootElement"]::after {
  content:"⌘K"; position:absolute; right:9px; top:50%; transform:translateY(-50%);
  font-family:'IBM Plex Mono',monospace; font-size:10.5px; color:var(--text-muted);
  border:1px solid var(--border); border-radius:4px; padding:1px 4px; pointer-events:none; }

/* The review dialog. FE-10, laid out as review-dialog-design.html.

   Step 1 is pinned to the top and step 6 to the bottom, so what is being decided and the
   decision itself are both on screen while the middle scrolls. Measured before this was built:
   at a 1000px viewport the approve and reject buttons sat below the fold. */
/* `position:sticky` does not work inside `st.dialog`, and this is the record of why, so the
   next person does not spend the afternoon on it.

   Measured, not assumed. The header computed as `position:sticky` and still scrolled from 116
   to -553. Its parent wrapper was exactly its own height, so it had nowhere to travel;
   `display:contents` on that wrapper fixed the travel and changed nothing, because an ancestor
   inside the dialog carries `overflow:hidden` and a sticky element positions against the
   nearest scrollport rather than the element that actually scrolls. Overriding that would mean
   reaching into the dialog's own clipping, which is the kind of `data-testid` dependency
   `fe-theme-spec.md` §1 warns breaks on upgrade.

   Shortening the dialog instead got most of the way and not all of it. The two-column layout
   and a capped, self-scrolling document panel take the overflow at a 1000px viewport from
   "the buttons are nowhere near the fold" to 104px. Measured on open, Approve sits at 1006 and
   needs 41px of scroll. Opening both expanders is a deliberate act and takes it to 669px.

   That last 41px is not fixed and is recorded as FE-31 rather than rounded off. On a real
   laptop the viewport is shorter than 1000px, so it is worse there, not better. */
[class*="st-key-dlg-foot-"] { padding-top:10px !important; }

.dlg-head { display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; }
.dlg-vendor { font-size:18px; font-weight:600; letter-spacing:-0.01em; color:var(--text); }
.dlg-amount { font-family:'IBM Plex Mono',monospace; font-size:20px; font-weight:500;
              margin-left:auto; font-variant-numeric:tabular-nums; color:var(--text); }
.dlg-meta { font-family:'IBM Plex Mono',monospace; font-size:12.5px; color:var(--text-muted);
            margin-top:5px; }
.dlg-score { display:flex; justify-content:flex-end; margin-top:10px; }
/* The risk band is still the only filled band on the screen. */
.dlg-risk { display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; margin-top:14px;
            padding:12px 16px; border-radius:12px; background:var(--caution-wash); }

.panel { background:var(--surface); border-radius:12px; box-shadow:var(--lift);
         padding:16px 18px; }
.panel-label { font-size:12px; color:var(--text-muted); margin-bottom:10px; }
.doc-marked { color:var(--caution-text); }
.doc-text { font-family:'IBM Plex Mono',monospace; font-size:12px; line-height:1.5;
            color:var(--text-strong); max-height:250px; overflow-y:auto; }
.doc-line { padding:1px 0; }
/* A row the model failed to store, marked where it appears. The eye needs somewhere to land on
   both sides of the same fact, which is what makes the panel opposite readable without
   scrolling between the two. */
.doc-missed { display:grid; grid-template-columns:1fr auto auto auto; gap:12px;
              align-items:baseline; margin:3px -8px; padding:6px 8px; border-radius:7px;
              background:var(--caution-wash); box-shadow:inset 3px 0 0 var(--caution); }
.doc-qty { color:#7A7160; }
.doc-sum { font-weight:500; }

.clash { margin-top:12px; padding:14px 16px; border-radius:12px; background:var(--caution-wash);
         box-shadow:inset 3px 0 0 var(--caution); }
.clash-title { font-size:13.5px; font-weight:600; color:var(--caution-text); margin-bottom:6px; }
.clash-note { font-size:13px; color:#6B6152; line-height:1.55; margin-bottom:10px; }
.clash-row { display:flex; justify-content:space-between; gap:12px;
             font-family:'IBM Plex Mono',monospace; font-size:12px; color:#7A7160; }
.clash-sum { display:flex; justify-content:space-between; gap:12px; margin-top:6px;
             padding-top:6px; border-top:1px solid rgba(138,74,24,0.22);
             font-family:'IBM Plex Mono',monospace; font-size:12.5px; font-weight:500;
             color:var(--caution-text); }

.field { display:flex; justify-content:space-between; align-items:baseline; gap:12px;
         padding:7px 0; }
.field-label { font-size:13px; color:var(--text-muted); }
.field-value { font-family:'IBM Plex Mono',monospace; font-size:13px; color:var(--text);
               text-align:right; }
.field-note { font-family:'IBM Plex Mono',monospace; font-size:11.5px; color:var(--text-muted);
              background:var(--fill-subtle); border-radius:5px; padding:1px 6px; margin-left:8px; }
.field-rule { height:1px; background:var(--fill-subtle); margin:8px 0; }
/* The sentence saying what the score measures. It comes from review_signals.py, like every
   other sentence on this screen, and tests/test_app_signal_copy.py stops a copy of it being
   written here. */
.field-foot { font-size:11.5px; line-height:1.55; color:var(--text-muted); margin:6px 0 0; }
.field-miss { color:var(--caution-text); font-weight:500; }

/* Focus has to be visible on every control, not just the ones Streamlit decides to mark.
   :focus-visible rather than :focus so a mouse click does not leave a ring behind. */
:is(button, input, select, textarea, a, [role="tab"], [tabindex]):focus-visible {
  outline: none !important;
  box-shadow: 0 0 0 3px rgba(91,91,214,0.16) !important;
  border-radius: 8px;
}
</style>
""", unsafe_allow_html=True)


def money(amount, currency) -> str:
    if amount is None or pd.isna(amount):
        return "-"
    return f"{currency or ''} {amount:,.2f}".strip()


def document_text(path) -> str:
    """The text the model was given, character for character.

    **Not the rendered page, and three approaches were tried before settling here.** `st.pdf`
    exists in Streamlit 1.62.0 but raises unless the separate `streamlit-pdf` component is
    installed, and version 2.0.1 of that component fails on import against this Streamlit.
    Embedding the file as a `data:` URI renders nothing, because Streamlit sandboxes the iframe
    `st.html` produces. Rasterising the first page would work and costs a binary dependency that
    every teammate would have to install.

    The text is a downgrade for layout and an upgrade for the job: the question an approver is
    answering is whether the model read the document correctly, and this is exactly what it was
    given. The original is one click away for anyone who needs the layout.
    """
    if not path or not os.path.exists(path):
        return ""
    try:
        from pypdf import PdfReader
        return "\n".join((page.extract_text() or "") for page in PdfReader(path).pages)
    except Exception:                                   # noqa: BLE001
        return ""


def document_panel(text: str, missed) -> str:
    """The extracted text, with the priced rows the model failed to store marked in place.

    FE-11. Marking them here is what lets the panel opposite be read without scrolling between
    the two: the same fact has somewhere to land on both sides of the dialog.
    """
    wanted = {row.description for row in missed}
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    out, i = [], 0
    while i < len(lines):
        if lines[i] in wanted and i + 3 < len(lines):
            description, quantity, unit, total = lines[i:i + 4]
            out.append(f"<div class='doc-missed'><span>{description}</span>"
                       f"<span class='doc-qty'>{quantity}</span>"
                       f"<span class='doc-qty'>{unit}</span>"
                       f"<span class='doc-sum'>{total}</span></div>")
            i += 4
        else:
            out.append(f"<div class='doc-line'>{lines[i]}</div>")
            i += 1
    return f"<div class='doc-text'>{''.join(out)}</div>"


def contradiction_panel(clash) -> str:
    """The most useful thing on the screen, said out loud.

    `fe-screen-spec.md` §5 called the contradiction the most useful thing here and it was only
    ever *available*: the document text and `Line items: none` sat side by side with nothing
    connecting them. `line_item_check` closes that. On all three sample documents the priced
    rows sum to exactly the total the pipeline stored, so the model's own total corroborates the
    rows it failed to store, and there is no reading of that except a miss.
    """
    rows = "".join(
        f"<div class='clash-row'><span>{r.description if len(r.description) <= 30 else r.description[:29] + chr(8230)}</span>"
        f"<span>{r.line_total.split()[1]}</span></div>" for r in clash["rows"])
    agreement = ("the exact total the model did read"
                 if clash["matches_stored_total"] else "which the model's total does not match")
    return (f"<div class='clash'>"
            f"<div class='clash-title'>The document shows {clash['count']}</div>"
            f"<div class='clash-note'>They are marked in the panel on the left. They sum to "
            f"<strong>{clash['sum']:,.2f}</strong>, {agreement}. The model stored none of "
            f"them.</div>{rows}"
            f"<div class='clash-sum'><span>sum of the rows</span>"
            f"<span>{clash['sum']:,.2f}</span></div></div>")


def field_row(label, value, note="") -> str:
    tail = f"<span class='field-note'>{note}</span>" if note else ""
    return (f"<div class='field'><span class='field-label'>{label}</span>"
            f"<span class='field-value'>{value}{tail}</span></div>")


SYSTEM_APPROVAL_SECTION = (
    "at a validation score of 1.00 with a verified amount, and nobody asked"
)
SYSTEM_APPROVAL_NOTE = (
    "These were approved at a validation score of 1.00 with a verified amount, "
    "and no person was involved. Every check behind that score reads the document "
    "itself, so a duplicate, an unknown vendor and a well-formatted forgery all "
    "score the same."
)
# Said "Use Reject if you need to reopen review" until PR #15 added a real Reopen. Rejecting
# does not send a document back for review, it rejects it.
HUMAN_APPROVAL_NOTE = (
    "This document was approved by a reviewer while data quality was "
    "NeedsReview. Re-running main.py does not undo that decision. "
    "Use Reopen in History if it needs another look."
)


def human_approval_note(row) -> str | None:
    """The sentence for a person-approved document the gate still calls NeedsReview.

    The same string is shown in the review dialog and on History, so the two cannot
    drift. An automatic approval has no reviewed_at and is a different case: a worse
    re-run returns that one to Pending.
    """
    reviewed = row.get("reviewed_at")
    if row.get("approval_status") != "Approved":
        return None
    if row.get("validation_status") != "NeedsReview":
        return None
    if reviewed is None or pd.isna(reviewed):
        return None
    return HUMAN_APPROVAL_NOTE


@st.dialog("Review document", width="large")
def review_dialog(row):
    """The only place approve and reject exist, laid out as `review-dialog-design.html`.

    Not in the row, deliberately. Objective 4 of this project is to keep a person in the
    approval path, and a person approving from the row decides on exactly the information the
    machine had, which is the decision the machine already makes by itself at a score of 1.00.
    The extra click buys a look at the document, the evidence quotes and the covering email.

    The six questions in order: what am I approving, is there a concern, let me look, where did
    it come from, what happens if I approve, decide. Two of those steps changed on 2026-09-24.

    Step 3 now draws the contradiction rather than merely exposing it, which is FE-11. Step 6
    is pinned to the bottom, because measured in a browser at a 1000px viewport the decision sat
    below the fold: on a screen whose whole argument is that a person should look before
    clicking, the thing they were being asked to click was the one thing they could not see.
    """
    with st.container(key=f"dlg-head-{row['id']}", gap=None):
        st.markdown(
            f"<div class='dlg-head'>"
            f"<span class='dlg-vendor'>{row['vendor_name'] or 'Unknown vendor'}</span>"
            f"{chip(row['document_type'], 'var(--text-faint)')}"
            f"<span class='dlg-amount'>{money(row['total_amount'], row['currency'])}</span>"
            f"</div><div class='dlg-meta'>{dialog_meta(row)}</div>"
            # SC-2. Under the amount and above the risk strip, which is the order the six
            # questions run in: what am I approving, then how far off is it, then what is the
            # concern in words.
            f"<div class='dlg-score'>{score_block(row)}</div>",
            unsafe_allow_html=True)

        signal, detail = risk_signal(row), risk_detail(row)
        if signal:
            st.markdown(
                f"<div class='dlg-risk'><span class='verdict verdict-look'>Worth a careful "
                f"look</span><span class='signal'>{signal}</span>"
                f"<span class='signal-detail'>{detail or ''}</span></div>",
                unsafe_allow_html=True)

    text = document_text(row.get("archive_path"))
    items = load_line_items(int(row["id"]))
    clash = contradiction(text, len(items), row.get("total_amount"))

    left, right = st.columns([1.35, 1], gap="small")
    with left:
        if text.strip():
            marked = clash["rows"] if clash else []
            note = (f" &middot; <span class='doc-marked'>the {clash['count']} rows it missed "
                    f"are marked</span>" if clash else "")
            st.markdown(f"<div class='panel'><div class='panel-label'>What the model read{note}"
                        f"</div>{document_panel(text, marked)}</div>", unsafe_allow_html=True)
        else:
            st.markdown("<div class='panel'><div class='panel-label'>What the model read</div>"
                        "<p class='ov-more'>No text layer. This document would need OCR, which "
                        "the pipeline does not do.</p></div>", unsafe_allow_html=True)
        original = original_for(row)
        if original:
            data, name = original
            st.download_button("Download the original", data=data, file_name=name,
                               mime="application/pdf", key=f"dlgdl-{row['id']}")

    with right:
        stored = "none" if items.empty else str(len(items))
        st.markdown(
            "<div class='panel'><div class='panel-label'>What the model stored</div>"
            + field_row("Date", row.get("invoice_date") or "-")
            + field_row("Currency", row.get("currency") or "-")
            + field_row("Total", money(row["total_amount"], row["currency"]),
                        row.get("total_source") or "")
            + field_row("Vendor", "read from the document"
                        if row.get("vendor_source") == "model" else "recovered by our code")
            # SC-3. The panel lists what the model stored, so it should also list what our own
            # check made of it. This is the one place with room for the full sentence, which is
            # why the note is visible here and only a tooltip on the card.
            + field_row("Validation score",
                        "-" if pd.isna(row.get("validation_score"))
                        else f"{float(row['validation_score']):.2f}",
                        verdict_word(row))
            + f"<p class='field-foot'>{SCORE_NOTE_SHORT}</p>"
            + "<div class='field-rule'></div>"
            + field_row("Line items",
                        f"<span class='field-miss'>{stored}</span>" if clash else stored)
            + (contradiction_panel(clash) if clash else "")
            + "</div>", unsafe_allow_html=True)

    email = load_email_for(row)
    if email["sender"]:
        with st.expander(f"Covering email · {email['sender']}"):
            st.markdown(f"**Subject** {email['subject'] or '-'}")
            st.markdown(f"**Received** `{email['received_at'] or '-'}`")
            if email["body"]:
                st.text(email["body"])

    actions = load_action_items(int(row["id"]))
    if not actions.empty:
        with st.expander(f"What the AI found · {len(actions)} actions"):
            st.caption(
                "Each action is shown above the sentence it was read from. The quote is what "
                "separates a real action from an invented one, and it is worth reading: the "
                "model tends to produce generic actions and then attach whatever sentence it "
                "happened to be near."
            )
            # These were checkboxes until 2026-09-20. Ticking one wrote
            # invoice_action_items.is_done, which nothing in this codebase reads: not the
            # validation score, not the approval, not the Jira task. It was a control whose
            # only effect was to be ticked. The actions are still worth showing, because the
            # quote beside each one is what exposes how generic they are.
            for _, item in actions.iterrows():
                st.markdown(
                    f"<div class='ai-find'><span class='ai-task'>{item['Action']}</span>"
                    f"<span class='ai-quote'>“{item['Evidence']}”</span></div>",
                    unsafe_allow_html=True)

    # What the checkbox should have been. A sentence is worth more to the next person than a
    # tick, and unlike `is_done` this is read: it is shown to whoever opens the document after.
    existing = row.get("review_note")
    note = st.text_area(
        "A note for whoever opens this next",
        value="" if not existing or pd.isna(existing) else existing,
        key=f"note-{row['id']}", height=80,
        placeholder="Chased the vendor about the missing line items.")
    saved, save = st.columns([5, 1.2], vertical_alignment="center")
    if row.get("review_note_at") and not pd.isna(row.get("review_note_at")):
        saved.markdown(f"<p class='note-when'>Last saved "
                       f"{local_time(row['review_note_at'], seconds=True)} {LOCAL_TZ_LABEL}</p>",
                       unsafe_allow_html=True)
    if save.button("Save note", key=f"savenote-{row['id']}", width="stretch"):
        save_review_note(int(row["id"]), note)
        st.rerun()

    with st.container(key=f"dlg-foot-{row['id']}", gap=None):
        held = human_approval_note(row)
        if held:
            st.info(held)
        # FE-12, corrected 2026-09-20. This said "Approving creates a Jira task Payment
        # immediately", which is false whenever Jira is unconfigured, and it is unconfigured
        # now. `record_decision` always writes a local follow-up task and queues an outbox row;
        # `dispatch_task_to_jira` then returns without contacting anything unless JIRA_ENABLED
        # and the .env are set. A screen that claims more than the system does is the exact
        # failure this project exists to catch, and it was doing it on the one line that exists
        # to warn a person before they act.
        #
        # The title is built by the dispatcher's own function rather than re-spelled here, so
        # the preview cannot drift from the issue. `_build_summary` is private and lives in
        # Luke's file; it is called read-only and pinned by tests/test_app_jira_preview.py.
        task_title = task_dispatch._build_summary(
            follow_up_for(row["document_type"]), row["file_name"], row["vendor_name"])
        if jira_ready():
            st.markdown(f"Approving creates a Jira issue immediately, titled `{task_title}`.")
        else:
            st.markdown(
                f"Approving opens a task titled `{task_title}` and queues it for Jira. "
                f"**Jira is not configured, so nothing is sent**: the row waits in the Outbox "
                f"until `JIRA_ENABLED` is set.")
        # Two identical full-width buttons made Reject look as inviting as Approve. Approve is
        # filled and Reject is quiet, both at the right edge under the line that says what
        # approving will do.
        _spacer, reject, approve = st.columns([4, 1.1, 1.2], vertical_alignment="center")
        if reject.button("Reject", key=f"reject-{row['id']}", width="stretch"):
            record_decision(int(row["id"]), "Rejected")
            st.rerun()
        if approve.button("Approve", type="primary", key=f"approve-{row['id']}",
                          width="stretch"):
            record_decision(int(row["id"]), "Approved")
            st.rerun()


def dialog_meta(row) -> str:
    parts = [row["invoice_number"] or "-"]
    if row.get("invoice_date") and not pd.isna(row["invoice_date"]):
        parts.append(f"issued {row['invoice_date']}")
    waited = waiting_days(row)
    if waited is not None:
        parts.append(f"waiting {waited}d")
    return " &middot; ".join(parts)


DOC_ICON = (
    "<svg width='20' height='20' viewBox='0 0 16 16' fill='none'>"
    "<path d='M4 2.2h5l3 3v8.6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V3.2a1 1 0 0 1 1-1Z' "
    "stroke='var(--text-strong)' stroke-width='1.3' stroke-linejoin='round'/>"
    "<path d='M8.7 2.2v3.2H12' stroke='var(--text-strong)' stroke-width='1.3' "
    "stroke-linejoin='round'/><path d='M5.4 8.6h5.2M5.4 10.8h3.4' stroke='var(--text-muted)' "
    "stroke-width='1.3' stroke-linecap='round'/></svg>"
)


def waiting_days(row) -> int | None:
    """Days since the document arrived, measured from the covering email.

    Not from `invoice_date`: an invoice issued on the 10th and emailed on the 7th of the next
    month has been waiting for us since the 7th, not since the 10th. Falls back to the time the
    pipeline read the file, for a document dropped straight into `inbox/` with no email.
    """
    arrived = row.get("email_received_at") or row.get("system_processed_at")
    if not arrived or pd.isna(arrived):
        return None
    try:
        return max((pd.Timestamp.today().normalize() - pd.Timestamp(arrived).normalize()).days, 0)
    except (ValueError, TypeError):
        return None


@st.cache_data(show_spinner=False)
def original_bytes(path: str, _mtime: float) -> bytes:
    """The archived PDF, cached on its path and mtime.

    Streamlit re-executes every tab body on every rerun, and Overview and the queue tab both
    draw the same cards, so without this the same file is read from disk several times a click.
    """
    with open(path, "rb") as handle:
        return handle.read()


def original_for(row):
    """The bytes and name of the document behind a row, or None when the file is not there.

    `archive_path` can point at a file that has been moved or was never archived, and a download
    button that produces nothing is worse than one that says why it cannot.
    """
    path = row.get("archive_path")
    if not path or pd.isna(path) or not os.path.exists(path):
        return None
    return original_bytes(path, os.path.getmtime(path)), os.path.basename(path)


def chip(text: str, dot: str | None) -> str:
    mark = f"<span class='dot' style='background:{dot}'></span>" if dot else ""
    return f"<span class='chip'>{mark}{text}</span>"


def score_block(row) -> str:
    """The gate's own number, as a meter against the threshold it is judged by. SC-1.

    `fe-screen-spec.md` §3 excluded the raw score from the card and §4 argued the reason: a
    person reading `0.85` cannot act on it, while "no line items to check the total against"
    tells them to open the document. That argument is kept and this does not contradict it. The
    sentence stays where it is, at the same weight, and this is added beside it. They answer
    different questions: the sentence says what to do, the number says how far off it is. Two
    documents carrying the same sentence can be 0.85 and 0.40 apart and the screen could not
    tell them apart until now. See fe-score-spec.md §2.

    A meter rather than a bare figure because a ratio against a limit is what this is, and the
    limit is the whole point: `0.85` alone means nothing, `0.85` with the gate mark just behind
    it reads as a near miss before the digits are parsed.

    The rail is a rail, not a band. `fe-theme-v2-spec.md` §0.1 keeps one constraint from the
    original design system, that risk is the only thing on the screen with a filled background,
    and the risk strip below this keeps it. 4px of track is a rule, not a block.

    Colour carries state and never carries it alone: the verdict word beside the number says
    the same thing in text, so the meaning survives greyscale and colour blindness. The track is
    a lighter step of the fill's own ramp rather than neutral grey, so the state reads across
    the whole bar. The digits and the word wear ink tokens, not the state colour: only the rail
    is coloured, which is what keeps this quiet enough to sit under the amount.
    """
    score = row.get("validation_score")
    verdict = verdict_word(row)
    label = "<span class='score-label'>Validation score</span>"

    # Nullable in the schema, see database-spec.md. A row that was never scored draws the
    # label and a dash, and no rail: an empty track would read as a score of zero.
    if score is None or pd.isna(score):
        return (f"<div class='score' title='{SCORE_NOTE}'>{label}"
                f"<span class='score-read'><span class='score-value'>-</span>"
                f"<span class='score-verdict'>not scored</span></span></div>")

    passed = row.get("validation_status") == "Validated"
    fill = "var(--positive)" if passed else "var(--caution)"
    track = "var(--positive-wash)" if passed else "var(--caution-wash)"
    width = max(0.0, min(1.0, float(score))) * 100

    return (
        f"<div class='score' title='{SCORE_NOTE}'>{label}"
        f"<span class='score-read'><span class='score-value'>{float(score):.2f}</span>"
        f"<span class='score-verdict'>{verdict}</span></span>"
        f"<span class='score-track' style='background:{track}'>"
        f"<span class='score-fill' style='width:{width:.1f}%;background:{fill}'></span>"
        f"<span class='score-tick' style='left:{GATE_THRESHOLD * 100:.1f}%'></span></span>"
        f"<span class='score-gate'>gate {GATE_THRESHOLD:.2f}</span></div>"
    )


def provenance(row) -> str:
    """Where the vendor name and the total came from, as two chips.

    `fallback` means our own code recovered the value after the model failed to. That is worth
    saying on the card rather than only inside the dialog, because it changes how much the
    extracted fields are worth.
    """
    out = []
    for column, read, inferred in (
        ("vendor_source", "Vendor read from the document", "Vendor name was inferred"),
        ("total_source", "Total read from the document", "Total was recovered by our code"),
    ):
        from_model = row.get(column) == "model"
        out.append(chip(read if from_model else inferred,
                        "var(--positive)" if from_model else "var(--caution)"))
    sender = row.get("email_sender")
    if sender and not pd.isna(sender):
        out.append(chip(f"Covering email · {sender}", None))
    return "".join(out)


def document_card(row, *, actionable: bool, context: str = "queue"):
    """One card per document, replacing the six-column row.

    A card rather than a row because the real queue holds one document, and one row in a wide
    table reads as a loading error. See approval-screen-components.html C4.

    The action stays an `st.button` rather than markup inside the card: a string cannot carry a
    widget. The card is markdown, the action is a widget, and the CSS makes the seam invisible.
    """
    waited = waiting_days(row)
    meta = [row["invoice_number"] or "-"]
    if row.get("invoice_date") and not pd.isna(row["invoice_date"]):
        meta.append(f"issued {row['invoice_date']}")
    if waited is not None:
        meta.append(f"waiting {waited}d")

    signal = risk_signal(row)
    detail = risk_detail(row)
    # The verdict leads, then what was found, then why. A person reading the old strip got the
    # finding and had to work out for themselves whether it meant open the document or not.
    #
    # It says "our check", not "the AI". The sentence is computed by review_signals.py from the
    # invoice's stored columns, deterministically, and it is a judgement about the model's
    # output rather than anything the model said. Calling it the AI's words would be the exact
    # overclaim this project exists to avoid.
    signal_html = (
        f"<div class='signal-strip'>"
        f"<span class='verdict verdict-look'>Worth a careful look</span>"
        f"<span class='signal'>{signal}</span>"
        f"<span class='signal-detail'>{detail or ''}</span></div>"
        if signal else
        f"<div class='signal-strip clear'>"
        f"<span class='verdict verdict-clear'>Nothing flagged</span>"
        f"<span class='signal-detail'>Our checks read the document itself, so this says the "
        f"numbers hang together, not that the document is genuine.</span></div>"
    )

    with st.container(border=True, key=f"doc-{context}-{row['id']}", gap=None):
        st.markdown(
            f"<div class='card-head'>"
            f"<div class='doc-icon'>{DOC_ICON}</div>"
            f"<div class='card-id'>"
            f"<div class='card-title'><span class='vendor'>{row['vendor_name'] or '-'}</span>"
            f"{chip(row['document_type'], 'var(--text-faint)')}</div>"
            f"<div class='card-meta'>{' · '.join(meta)}</div>"
            f"</div>"
            f"<div class='amount-stack'>"
            f"<div class='amount'>{money(row['total_amount'], row['currency'])}</div>"
            f"{score_block(row)}</div>"
            f"</div>{signal_html}",
            unsafe_allow_html=True,
        )
        with st.container(key=f"foot-{context}-{row['id']}", gap=None):
            # One horizontal line, like History: the chips take the room and the two buttons keep
            # their width, dropping underneath together when the window is narrow. Three columns
            # gave the chips a fixed share, so the last chip wrapped while the buttons floated.
            line = st.container(horizontal=True, vertical_alignment="center",
                                key=f"footline-{context}-{row['id']}")
            line.markdown(f"<div class='chips'>{provenance(row)}</div>", unsafe_allow_html=True)

            original = original_for(row)
            if original:
                data, name = original
                line.download_button("Download the original", data=data, file_name=name,
                                     mime="application/pdf",
                                     key=f"dl-{context}-{row['id']}")
            else:
                line.button("Download the original", disabled=True,
                            key=f"dl-{context}-{row['id']}",
                            help="The archived file is not on disk.")

            # OV-6. A document the system approved without asking can still be opened. The
            # dialog is the same one, so a person can look at what was decided for them and
            # reject it if they disagree, which is the oversight §2 of fe-screen-spec.md says
            # the second tab exists to make possible.
            label = "Review document" if actionable else "Open the document"
            if line.button(label, type="primary" if actionable else "secondary",
                           key=f"rev-{context}-{row['id']}"):
                review_dialog(row)


def queue_controls(frame):
    """Vendor filter and sort order. Returns the frame the cards are built from.

    Sort by amount groups by currency first and orders within each group. Ordering USD against
    AUD by magnitude is the same arithmetic this project watches the model for, and it would be
    invisible at the size of the current queue.
    """
    count, vendor_col, order_col, _ = st.columns([1.1, 1.7, 1.7, 4], vertical_alignment="center")
    count.markdown(
        f"<div class='queue-count'>{len(frame)} document{'' if len(frame) == 1 else 's'}</div>",
        unsafe_allow_html=True)
    vendors = ["All vendors"] + sorted(v for v in frame["vendor_name"].dropna().unique())
    chosen = vendor_col.selectbox("Vendor", vendors, label_visibility="collapsed")
    order = order_col.selectbox("Order", ["Oldest first", "Largest amount first"],
                                label_visibility="collapsed")

    if chosen != "All vendors":
        frame = frame[frame["vendor_name"] == chosen]
    if order == "Oldest first":
        return frame.sort_values("email_received_at", na_position="last")
    return frame.sort_values(["currency", "total_amount"], ascending=[True, False])


def empty_queue():
    """C8. Every sentence here is a query result, not a constant.

    `reviewed_at` is null on every row in this database, so there is no last decision by a
    person to report. The line says what the system did instead, which is the true statement.
    """
    decided = load_data()
    decided = decided[(decided["approval_status"] == "Approved") & (decided["reviewed_at"].isna())]
    latest = decided["system_processed_at"].max() if not decided.empty else None
    when = f" The last {len(decided)} it cleared on its own at {local_time(latest)[11:16]}." if latest else ""
    st.markdown(
        f"<div class='empty'>"
        f"<div class='empty-mark'><svg width='20' height='20' viewBox='0 0 16 16' fill='none'>"
        f"<path d='m3.6 8.3 2.9 2.9 5.9-6.1' stroke='var(--positive)' stroke-width='1.6' "
        f"stroke-linecap='round' stroke-linejoin='round'/></svg></div>"
        f"<div class='empty-title'>Nothing is waiting for you</div>"
        f"<div class='empty-note'>Everything the pipeline read today has been decided.{when}</div>"
        f"</div>", unsafe_allow_html=True)


def document_rows(frame, *, actionable: bool, context: str = "queue",
                  controls: bool = True, limit: int | None = None):
    """The queue, as cards.

    Deliberately excluded from the card: file name, ingestion time and run_id. All are
    available and none of them changes a decision.

    The validation score was on that list until 2026-09-24 and is now on the card. The reason
    it was excluded, that a bare number cannot be acted on, is answered by showing it against
    the gate's threshold rather than by hiding it. See fe-score-spec.md §2.
    """
    if frame.empty:
        # Under a filter the queue being empty says nothing about the work. Claiming otherwise
        # would be the empty state lying, which is worse than the blank screen it replaced.
        if filtered:
            st.markdown("<p class='ov-more'>No documents match the current view.</p>",
                        unsafe_allow_html=True)
        elif actionable:
            empty_queue()
        else:
            st.caption("Nothing here.")
        return

    if actionable and controls:
        frame = queue_controls(frame)
    shown = frame if limit is None else frame.head(limit)
    for _, row in shown.iterrows():
        document_card(row, actionable=actionable, context=context)
    if limit is not None and len(frame) > limit:
        st.markdown(f"<p class='ov-more'>{len(frame) - limit} more in the tab above.</p>",
                    unsafe_allow_html=True)


# =====================================================================
# The sidebar. FE-15.
# =====================================================================
#
# The returned design drew five destinations, Approvals, Documents, Vendors, Notifications and
# Pipeline runs, plus two saved views. Two of the five exist and three are navigation to screens
# nobody has specified, so the decision on 2026-09-19 was to carry only what resolves.
#
# What is left after that trim is not navigation at all, because this application is one page
# with five tabs. It is identity, a search that filters, and two saved views that are real
# queries. Every control here does something; a control that does not is the defect this
# project already removed once, in a5864d5.


ICONS = {
    # Inner SVG markup, not a single path, because a clock needs a circle as well as a line.
    # `currentColor` is not usable here: these are background images on a pseudo element, so the
    # colour is baked in and the selected state gets its own copy.
    "overview": "<path d='M2.8 2.8h4.2v4.2H2.8zM9 2.8h4.2v4.2H9zM2.8 9h4.2v4.2H2.8zM9 9h4.2v4.2H9z'"
                " stroke='{c}' stroke-width='1.4' stroke-linejoin='round'/>",
    "awaiting": "<path d='M2.5 9.5h3l1 1.75h3l1-1.75h3M3.6 3.2h8.8l1.1 6.3v2.8a1 1 0 0 1-1 1H3.5"
                "a1 1 0 0 1-1-1V9.5l1.1-6.3Z' stroke='{c}' stroke-width='1.4' "
                "stroke-linecap='round' stroke-linejoin='round'/>",
    "auto": "<path d='m3.4 8.2 2.9 2.9 6.1-6.3' stroke='{c}' stroke-width='1.6' "
            "stroke-linecap='round' stroke-linejoin='round'/>",
    "outbox": "<path d='M2.6 9h3.2l1 1.8h2.4l1-1.8h3.2M8 9.6V3.2M5.6 5.6 8 3.2l2.4 2.4' "
              "stroke='{c}' stroke-width='1.4' stroke-linecap='round' stroke-linejoin='round'/>",
    "history": "<circle cx='8' cy='8' r='5.4' stroke='{c}' stroke-width='1.4'/>"
               "<path d='M8 4.8V8l2.2 1.4' stroke='{c}' stroke-width='1.4' "
               "stroke-linecap='round' stroke-linejoin='round'/>",
    "documents": "<path d='M4 2.5h5l3 3v8a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-10a1 1 0 0 1 1-1Z"
                 "M8.6 2.5v3.4H12M5.5 9h5M5.5 11.2h3.2' stroke='{c}' stroke-width='1.4' "
                 "stroke-linecap='round' stroke-linejoin='round'/>",
    "vendors": "<path d='M2.6 3.2h10.8v9.6H2.6zM2.6 6.4h10.8M6.2 6.4v6.4' stroke='{c}' "
               "stroke-width='1.4' stroke-linejoin='round'/>",
    "runs": "<path d='M8 2.6v2.1M8 11.3v2.1M13.4 8h-2.1M4.7 8H2.6M11.8 4.2 10.3 5.7"
            "M5.7 10.3l-1.5 1.5M11.8 11.8l-1.5-1.5M5.7 5.7 4.2 4.2' stroke='{c}' "
            "stroke-width='1.4' stroke-linecap='round'/><circle cx='8' cy='8' r='2.1' "
            "stroke='{c}' stroke-width='1.4'/>",
}


def icon_uri(name: str, colour: str) -> str:
    inner = ICONS[name].replace("{c}", colour)
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' "
           f"viewBox='0 0 16 16' fill='none'>{inner}</svg>")
    return "data:image/svg+xml;utf8," + quote(svg)


def destinations(frame, pending, auto, outbox, history) -> dict:
    """Everything the page can show, in one list.

    The tab bar was removed on 2026-09-20 and this took over, measured rather than argued. The
    sidebar and the tabs had been two navigation systems over the same data, and two of the
    sidebar's rows returned exactly the rows of two tabs:

        Flagged by the model  -> {1}      Awaiting approval tab      -> {1}
        Cleared this week     -> {2,3}    Approved by the system tab -> {2,3}

    They could only have diverged on a pending document the gate did not flag, or an
    auto-approved one older than seven days, and there were none of either. So the two saved
    views are gone: what they filtered to, a destination already shows.

    The five states of a document and the three other things to look at are one list, split by
    a label, because a person choosing where to go should not have to know which of two controls
    owns which half of the application.
    """
    with connect(DB_PATH) as conn:
        runs = conn.execute("SELECT COUNT(*) FROM processing_runs").fetchone()[0]
    return {
        "overview":  {"label": "Overview", "icon": "overview", "count": None},
        "awaiting":  {"label": "Awaiting approval", "icon": "awaiting", "count": len(pending)},
        "auto":      {"label": "Approved by the system", "icon": "auto", "count": len(auto)},
        "outbox":    {"label": "Outbox", "icon": "outbox", "count": len(outbox)},
        "history":   {"label": "History", "icon": "history", "count": len(history)},
        "documents": {"label": "Documents", "icon": "documents", "count": len(frame)},
        "vendors":   {"label": "Vendors", "icon": "vendors",
                      "count": frame["vendor_name"].nunique()},
        "runs":      {"label": "Pipeline runs", "icon": "runs", "count": runs},
    }


def matches(frame, query: str):
    """Substring over the three fields a person would actually type."""
    if not query.strip():
        return frame
    needle = query.strip()
    hit = False
    for column in ("vendor_name", "invoice_number", "file_name"):
        found = frame[column].astype("string").str.contains(needle, case=False, na=False)
        hit = found if hit is False else (hit | found)
    return frame[hit]


def sidebar_css(dest: dict) -> str:
    """The icons, the counts and the group label, written from the data they describe.

    A radio label is plain text, so the icon and the count are pseudo elements and their values
    have to be generated. Generating them from `dest` is also what stops a count disagreeing
    with the destination it sits on.
    """
    rules = []
    for i, item in enumerate(dest.values(), start=1):
        rules.append(
            f'[data-testid="stSidebar"] label[data-testid="stRadioOption"]:nth-of-type({i})'
            f'::before{{background-image:url("{icon_uri(item["icon"], "#5F667A")}");}}')
        rules.append(
            f'[data-testid="stSidebar"] label[data-selected="true"]:nth-of-type({i})'
            f'::before{{background-image:url("{icon_uri(item["icon"], "#5B5BD6")}");}}')
        if item["count"] is not None:
            rules.append(
                f'[data-testid="stSidebar"] label[data-testid="stRadioOption"]:nth-of-type({i})'
                f'::after{{content:"{item["count"]}";}}')
    return "<style>" + "".join(rules) + "</style>"


def sidebar(frame, pending, auto, outbox, history):
    """Draws the sidebar. Returns the search text and the chosen destination."""
    dest = destinations(frame, pending, auto, outbox, history)
    # A destination requested by a section header on the previous run. Applied here because this
    # is the last moment before the radio exists, after which its key is not writable.
    requested = st.session_state.pop("goto", None)
    if requested in dest:
        st.session_state["view"] = requested
    with st.sidebar:
        st.markdown(
            "<div class='side-id'><div class='side-mark'>"
            "<svg width='14' height='14' viewBox='0 0 16 16' fill='none'>"
            "<path d='M3 2.5h7l3 3v8a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1v-10a1 1 0 0 1 1-1Z' "
            "stroke='#FFFFFF' stroke-width='1.3' stroke-linejoin='round'/>"
            "<path d='M9.5 2.5v3.5H13' stroke='#FFFFFF' stroke-width='1.3' "
            "stroke-linejoin='round'/></svg></div>"
            "<div class='side-name'><span class='side-title'>Payables</span>"
            "<span class='side-sub'>Finance operations</span></div>"
            "<svg width='12' height='12' viewBox='0 0 16 16' fill='none' "
            "style='margin-left:auto; flex:none;'><path d='M4 6.5 8 10l4-3.5' "
            "stroke='var(--text-faint)' stroke-width='1.4' stroke-linecap='round' "
            "stroke-linejoin='round'/></svg></div>",
            unsafe_allow_html=True)

        query = st.text_input("Search documents", key="search",
                              placeholder="Search documents",
                              label_visibility="collapsed")

        st.markdown(sidebar_css(dest), unsafe_allow_html=True)
        chosen = st.radio("Destinations", list(dest), key="view",
                          label_visibility="collapsed",
                          format_func=lambda key: dest[key]["label"])

        # Was "Local session / No sign-in on this machine", which stopped being true the
        # moment require_login() landed. A footer that denies the login the user just passed
        # through is worse than no footer.
        who = st.session_state.get("display_name") or "Unknown"
        initials = "".join(part[0] for part in who.split()[:2]).upper() or "?"
        st.markdown(
            f"<div class='side-foot'><span class='side-avatar'>{initials}</span>"
            f"<span class='side-name'><span class='side-who'>{who}</span>"
            f"<span class='side-sub'>Signed in on this machine</span></span></div>",
            unsafe_allow_html=True)
        if st.button("Log out", key="logout", width="stretch"):
            st.session_state.clear()
            st.rerun()

    return chosen, query


# =====================================================================
# Authentication. Luke's, arriving with migrations 013 and 014.
# =====================================================================
#
# The two titles were left as Luke wrote them through the merge, naming the dashboard this
# branch replaces, and raised on the pull request rather than decided for him. He answered on
# 2026-09-28: use Invoice approvals on both, with no lightning bolt, and leave the Payables
# sidebar, the design HTML and the briefing alone. Done here and nowhere else.
#
# Both functions end in st.stop(), which is what makes them a gate rather than a suggestion.
# That is also why every test rendering this file seeds st.session_state before running it:
# without a session there is nothing to assert against but a login form.

def require_password_change(store: StorageManager) -> None:
    """Stops the page until a first-time user replaces the temporary password."""
    st.title("Invoice approvals")
    st.caption(
        f"Signed in as **{st.session_state['display_name']}**. "
        "This is the first login for this account. Choose a new password to continue."
    )
    with st.form("change_password"):
        new_password = st.text_input("New password", type="password")
        confirm = st.text_input("Confirm new password", type="password")
        submitted = st.form_submit_button("Save password")
    if submitted:
        if not new_password or new_password != confirm:
            st.error("Enter the new password twice, and make sure both match.")
        else:
            try:
                auth.change_password(store, int(st.session_state["user_id"]), new_password)
            except ValueError as error:
                st.error(str(error))
            else:
                st.session_state["must_change_password"] = False
                st.rerun()
    if st.button("Log out"):
        st.session_state.clear()
        st.rerun()
    st.stop()


def require_login() -> None:
    """Stops the page until a configured user signs in, then until they set a password.

    Returns immediately when there is no script run context, which means the module was
    imported rather than served. Four test modules do `import app` to reach its helpers, and
    without this guard the import draws a login form into no page at all: `st.stop()` is a
    no-op outside a run, so execution falls through it and Streamlit is left holding an open
    form. The next AppTest run then fails with "st.button() can't be used in an st.form()",
    which names neither the cause nor the file.

    AppTest does provide a context, so this does not weaken the gate under test. Nobody is
    being authenticated here; there is simply no page to gate.
    """
    if get_script_run_ctx() is None:
        return
    store = StorageManager(DB_PATH)
    auth.ensure_default_users(store)
    if st.session_state.get("user_id"):
        if st.session_state.get("must_change_password"):
            require_password_change(store)
        return

    st.title("Invoice approvals")
    st.caption("Sign in to review documents. Your name is recorded on each decision.")
    with st.form("login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")
    if submitted:
        user = auth.authenticate(store, username, password)
        if user:
            st.session_state["user_id"] = user["user_id"]
            st.session_state["display_name"] = user["display_name"]
            st.session_state["must_change_password"] = user["must_change_password"]
            st.rerun()
        st.error("Unknown username or password.")
    st.stop()


require_login()

df = load_data()
if df.empty:
    st.title("Invoice approvals")
    st.write("No documents have been processed yet. Run `main.py` over a document in `inbox/`.")
    st.stop()

# The search runs before the counts, so what the sidebar says and what the body shows are the
# same set. outbox and history are separate queries, so they are narrowed by membership.
query_text = st.session_state.get("search", "")
df = matches(df, query_text)
filtered = bool(query_text.strip())
keep = set(df["id"])
pending = df[df["approval_status"] == "Pending"]
auto = df[(df["approval_status"] == "Approved") & (df["reviewed_at"].isna())]
outbox = load_outbox()
history = load_history()
if filtered:
    outbox = outbox[outbox["invoice_id"].isin(keep)]
    history = history[history["invoice_id"].isin(keep)]

view, query = sidebar(df, pending, auto, outbox, history)

def single_currency_total(frame) -> str | None:
    """The summed amount, or None when summing would be dishonest.

    Until 2026-09-19 the Awaiting approval tab label read `{count} · {sum}` unconditionally.
    With one document pending that was correct and invisible; with three it showed `3 · 6,500`,
    which is USD 1,500 plus USD 2,350 plus AUD 2,650 added as though they were the same unit.
    That is the arithmetic this project exists to catch a model doing, and the screen was doing
    it.

    So an amount is returned only while every row shares a currency, and it carries that
    currency's code. Every total on the screen goes through here.
    """
    if frame.empty:
        return None
    currencies = set(frame["currency"].dropna())
    if len(currencies) != 1:
        return None
    return f"{currencies.pop()} {frame['total_amount'].sum():,.0f}"


def outbox_body(frame):
    """Two groups, because the rows are two different things.

    Jira rows have a transport and can be pushed. Teams rows never will: nothing in this
    codebase has written one since 2026-09-08, `queue_outbound` is called from one place and it
    passes Jira. They stay because they are the evidence that FR-6.2 was implemented while
    FR-6.3 was not, which is a point the report makes.
    """
    if frame.empty:
        st.caption("Nothing has ever been queued.")
        return

    jira_rows = frame[frame["channel"] == "Jira"]
    other = frame[frame["channel"] != "Jira"]
    pushable = jira_rows[jira_rows["state"].isin(["Pending", "Failed"]) & ~jira_rows["withdrawn"]]
    withdrawn = jira_rows[jira_rows["withdrawn"]]
    ready = jira_ready()

    st.markdown(
        f"<p class='tab-note'>{len(pushable)} can be pushed to Jira. "
        f"{len(other)} were recorded for Teams, which has no transport and is not planned, so "
        f"they stay here as a record. {int((frame['state'] == 'Sent').sum())} have been sent.</p>",
        unsafe_allow_html=True)

    if not ready and not pushable.empty:
        st.markdown(
            "<p class='tab-warn'>Jira is not configured, so Push is disabled. "
            "Set <code>JIRA_ENABLED</code> and the rest in <code>.env</code> to enable it.</p>",
            unsafe_allow_html=True)

    # OV-8. Selection lives here rather than on Overview: a checkbox asks a person to do bulk
    # work, and Overview is designed for a glance. Pushing a queued message is a batch operation
    # with no judgment in it, which is why this is the one bulk action that was built.
    # `Approve selected` was raised and declined: fe-screen-spec.md §5 keeps approval in the
    # dialog so that a person sees the document before deciding.
    chosen = [int(r["outbox_id"]) for _, r in pushable.iterrows()
              if st.session_state.get(f"sel-{r['outbox_id']}")]
    if not pushable.empty:
        bulk, _spacer = st.columns([2, 5], vertical_alignment="center")
        if bulk.button(f"Push selected to Jira ({len(chosen)})", type="primary",
                       disabled=not ready or not chosen, key="push-selected"):
            for _, row in pushable[pushable["outbox_id"].isin(chosen)].iterrows():
                push_to_jira(row)
            st.rerun()

    for _, row in pushable.iterrows():
        with st.container(border=True, key=f"out-{row['outbox_id']}", gap=None):
            pick, body, action = st.columns([0.35, 5, 1.3], vertical_alignment="center")
            pick.checkbox("Select", key=f"sel-{row['outbox_id']}", label_visibility="collapsed")
            state_dot = "var(--caution)" if row["state"] == "Failed" else "var(--text-faint)"
            failure = f"<div class='out-error'>{row['error']}</div>" if row["error"] else ""
            body.markdown(
                f"<div class='out-row'><div class='out-head'>"
                f"<span class='dot' style='background:{state_dot}'></span>"
                f"<span class='out-doc'>{row['invoice_number'] or '-'}</span>"
                f"<span class='out-vendor'>{row['vendor_name'] or '-'}</span>"
                f"<span class='out-when'>{local_time(row['created_at'])}</span></div>"
                f"<div class='out-payload'>{row['payload']}</div>{failure}</div>",
                unsafe_allow_html=True)
            if action.button("Push to Jira", key=f"push-{row['outbox_id']}",
                             type="primary", disabled=not ready):
                push_to_jira(row)
                st.rerun()

    if not withdrawn.empty:
        st.markdown(
            "<p class='tab-note'>Withdrawn. The document was reopened for review before these "
            "were sent, so they will not be. Approving it again queues a new one.</p>",
            unsafe_allow_html=True)
        st.dataframe(withdrawn.assign(created_at=withdrawn["created_at"].map(local_time))[
                         ["created_at", "invoice_number", "vendor_name", "payload"]],
                     hide_index=True, width="stretch",
                     column_config={"created_at": "Queued", "invoice_number": "Document",
                                    "vendor_name": "Vendor", "payload": "Message"})

    sent = jira_rows[jira_rows["state"] == "Sent"]
    sent = sent.assign(created_at=sent["created_at"].map(local_time),
                       sent_at=sent["sent_at"].map(local_time))
    if not sent.empty:
        st.markdown("<p class='tab-note'>Sent</p>", unsafe_allow_html=True)
        st.dataframe(sent[["created_at", "sent_at", "invoice_number", "external_ref", "payload"]],
                     hide_index=True, width="stretch",
                     column_config={"created_at": "Queued", "sent_at": "Sent",
                                    "invoice_number": "Document", "external_ref": "Jira issue",
                                    "payload": "Message"})

    if not other.empty:
        st.markdown(
            "<p class='tab-note'>Recorded for Teams, never sent. The text was written when it "
            "was true and nothing rewrites it, which is why the age matters: the oldest still "
            "say <code>NeedsReview at score 0.25</code> for documents that now read Validated."
            "</p>", unsafe_allow_html=True)
        other = other.assign(created_at=other["created_at"].map(local_time))
        st.dataframe(other[["created_at", "channel", "vendor_name", "invoice_number", "payload"]],
                     hide_index=True, width="stretch",
                     column_config={"created_at": "Queued", "channel": "Channel",
                                    "vendor_name": "Vendor", "invoice_number": "Document",
                                    "payload": "Message"})


def history_body(frame):
    """What a person decided, and what happened to the work that followed."""
    if frame.empty:
        st.markdown(
            "<div class='empty'><div class='empty-title'>Nobody has decided anything yet</div>"
            "<div class='empty-note'>Every document here so far was cleared by the system. "
            "A decision made in the review dialog appears in this tab.</div></div>",
            unsafe_allow_html=True)
        return

    st.markdown(
        "<p class='tab-note'>This is the only place a rejection is visible: a rejected document "
        "is not Pending, so it leaves the queue, and not Approved, so it never reaches the system "
        "tab.</p>", unsafe_allow_html=True)
    decided = load_data()
    store = StorageManager(DB_PATH)
    for _, row in frame.iterrows():
        decision = row["approval_status"]
        score = row.get("validation_score")
        scored = "-" if score is None or pd.isna(score) else f"{float(score):.2f}"
        # A decision with no name against it is the state reviewed_by exists to prevent, so
        # say so rather than rendering an empty span. Rows decided before the users table
        # existed legitimately have none.
        who = row.get("reviewer_name")
        decided_by = "no name recorded" if not who or pd.isna(who) else who
        # Reported 2026-09-29: eight unlabelled values in a row read as a string of words. Each
        # one now says what it is, and the second line carries what followed the decision.
        headline = {"Approved": "Approved", "Rejected": "Rejected",
                    "Reopened": "Reopened for review"}.get(decision, decision)
        dot = {"Rejected": "var(--caution)", "Reopened": "var(--text-muted)"}.get(
            decision, "var(--positive)")
        if decision == "Reopened":
            followed = "Follow-up task cancelled, document back in Awaiting approval"
        elif row.get("task_state") and not pd.isna(row.get("task_state")):
            followed = f"{row['task_type']} task {row['task_state'].lower()}"
        else:
            followed = "No task recorded"
        if not row["is_latest"]:
            followed += " &middot; <em>superseded by a later decision</em>"
        match = decided[decided["id"] == row["invoice_id"]]
        full = match.iloc[0] if not match.empty else None
        key = f"hist-{int(row['decision_id'])}"
        held = human_approval_note(row) if row["is_latest"] else None
        held_html = f"<div class='hist-note'>{held}</div>" if held else ""
        with st.container(border=True, key=key, gap=None):
            # One horizontal line: the facts take the room, the buttons keep their width and drop
            # underneath when the window is too narrow. Nested st.columns shrank each button to a
            # few pixels at about 900px and wrapped "Open" one letter per line.
            line = st.container(horizontal=True, vertical_alignment="center",
                                key=f"histline-{int(row['decision_id'])}")
            line.markdown(
                f"<div class='hist'>"
                f"<span class='dot' style='background:{dot}'></span>"
                f"<span class='hist-decision'>{headline}</span>"
                f"<span class='hist-vendor'>{row['vendor_name'] or '-'}</span>"
                f"<span class='hist-doc'>{row['invoice_number'] or '-'}</span>"
                f"<span class='hist-amount'>{money(row['total_amount'], row['currency'])}</span>"
                f"</div>"
                f"<div class='hist-facts'>"
                f"<span><b>By</b> {decided_by}</span>"
                f"<span><b>When</b> {local_time(row['reviewed_at'])}</span>"
                f"<span title='{SCORE_NOTE}'><b>Validation score then</b> {scored} "
                f"({verdict_word(row)})</span>"
                f"<span><b>What followed</b> {followed}</span>"
                f"</div>{held_html}", unsafe_allow_html=True)
            if line.button("Open", key=f"{key}-open", disabled=full is None):
                review_dialog(full)
            # One Reopen per document, on its latest decision. Reopening an older row would
            # take back a decision that is no longer the one in force.
            if row["is_latest"] and row["current_status"] != "Pending":
                blocker = store.reopen_blocker(int(row["invoice_id"]))
                if line.button("Reopen", key=f"{key}-reopen", disabled=bool(blocker),
                               help=blocker or "Take this decision back and return the "
                               "document to Awaiting approval. The decision stays here."):
                    reopen_for_review(int(row["invoice_id"]))
                    st.rerun()


def tile(label, value, note) -> str:
    return (f"<div class='ov-tile'><span class='ov-label'>{label}</span>"
            f"<div class='ov-value'><span class='ov-number'>{value}</span>"
            f"<span class='ov-note'>{note}</span></div></div>")


SECTION_ICONS = {
    "awaiting": "<path d='M2.5 9.5h3l1 1.75h3l1-1.75h3' stroke='currentColor' stroke-width='1.4' "
                "stroke-linecap='round' stroke-linejoin='round'/><path d='M3.6 3.2h8.8l1.1 6.3v2.8"
                "a1 1 0 0 1-1 1H3.5a1 1 0 0 1-1-1V9.5l1.1-6.3Z' stroke='currentColor' "
                "stroke-width='1.4' stroke-linejoin='round'/>",
    "auto": "<path d='m3.6 8.3 2.9 2.9 5.9-6.1' stroke='currentColor' stroke-width='1.6' "
            "stroke-linecap='round' stroke-linejoin='round'/>",
    "outbox": "<path d='M2.6 8h3.2l1 1.8h2.4l1-1.8h3.2' stroke='currentColor' stroke-width='1.4' "
              "stroke-linecap='round' stroke-linejoin='round'/><path d='M8 2.6v6.2M5.6 6.4 8 8.8l"
              "2.4-2.4' stroke='currentColor' stroke-width='1.4' stroke-linecap='round' "
              "stroke-linejoin='round'/>",
    "history": "<circle cx='8' cy='8' r='5.4' stroke='currentColor' stroke-width='1.4'/>"
               "<path d='M8 4.8V8l2.2 1.4' stroke='currentColor' stroke-width='1.4' "
               "stroke-linecap='round' stroke-linejoin='round'/>",
}


def section_head(title, note, *, opens: str | None = None, key: str = "") -> None:
    """A section heading, and the control that opens the destination it summarises.

    OV-4, built and working as of 2026-09-20, after being built, shipped and removed once.

    It never worked on `st.tabs`. That widget takes a `key` and a `default` and neither selects
    a tab from code: the key records what the user picked, and writing it moves the session
    value while the frontend keeps its own selection. Measured in a browser, `aria-selected`
    stayed on Overview with both tried. A radio does not have that problem, so removing the tab
    bar in favour of the sidebar is what made this buildable, rather than any change here.

    Plausible puts the same control at the top right of every panel. A section that summarises
    something and cannot open it is a dead end.
    """
    # The rule under a heading belongs to the whole row, not to the column the text sits in,
    # so it goes on the container rather than on the markdown inside it.
    with st.container(key=f"sechead-{key}"):
        head, action = st.columns([8, 1], vertical_alignment="center")
        # "outbox-failed" is still the outbox icon. The suffix exists to drive the
        # header colour from CSS, not to name a different section.
        icon = SECTION_ICONS.get(key.split("-")[0], "")
        mark = (f"<span class='sec-icon'><svg width='15' height='15' viewBox='0 0 16 16' "
                f"fill='none'>{icon}</svg></span>" if icon else "")
        head.markdown(f"<div class='sec-head'>{mark}<span class='sec-title'>{title}</span>"
                      f"<span class='sec-note'>{note}</span></div>", unsafe_allow_html=True)
        if opens and action.button("Open", key=f"open-{key}", help=f"Go to {title}"):
            # Not `st.session_state["view"] = opens`. Streamlit refuses a write to a widget's
            # key once that widget exists in the run, and the sidebar is drawn before this
            # button. The request is parked instead and `sidebar` applies it on the next run,
            # before the radio is created. Measured: the direct write raises and the radio does
            # not move, which is the same shape of failure that killed this control on tabs.
            st.session_state["goto"] = opens
            st.rerun()


def record_row(*, key, dot, left, doc, value, right, action, on_action,
               disabled=False, help=None):
    """One record with one action.

    Overview used to render these sections as a block of markdown, which read well and could not
    be acted on. Three of the four sections were lists of things a person might want to do
    something about, with nothing to press. OV-5 to OV-7.
    """
    with st.container(key=key, gap=None):
        body, act = st.columns([5, 1.3], vertical_alignment="center")
        body.markdown(
            f"<div class='dense'><span class='dot' style='background:{dot}'></span>"
            f"<span class='dense-left'>{left}</span>"
            f"<span class='dense-doc'>{doc}</span>"
            f"<span class='dense-amount'>{value}</span>"
            f"<span class='dense-right'>{right}</span></div>", unsafe_allow_html=True)
        if act.button(action, key=f"{key}-act", disabled=disabled, help=help):
            on_action()


def dense_rows(rows) -> str:
    """C7. One line per record, for the sections that summarise rather than ask for a decision.

    Each row carries its own currency symbol and no total is drawn beneath them, because the
    documents in these sections do not share one.

    Rows are five items, or six where the panel carries a validation score. SC-6 needed a
    column here and the two other callers, vendors and pipeline runs, have no score to show:
    a vendor is a group of documents and a run is not scored at all. An optional sixth item
    was the change that left both of them untouched.
    """
    out = []
    for row in rows:
        dot, left, doc, amount, right = row[:5]
        score = f"<span class='dense-score'>{row[5]}</span>" if len(row) > 5 else ""
        out.append(f"<div class='dense'><span class='dot' style='background:{dot}'></span>"
                   f"<span class='dense-left'>{left}</span>"
                   f"<span class='dense-doc'>{doc}</span>"
                   f"<span class='dense-amount'>{amount}</span>{score}"
                   f"<span class='dense-right'>{right}</span></div>")
    return f"<div class='dense-panel'>{''.join(out)}</div>"


def overview_body(pending, auto, outbox, history):
    """The whole screen on one page, in the order a person asks about it.

    Built as a page rather than a grid of counters. A count alone answers nothing: the questions
    are what is waiting, what the system decided without asking, what is stuck on its way out,
    and what has been decided. Each section carries its own rows.

    The summary strip belongs here rather than on the queue tab, where it was cut on 2026-09-19
    for repeating the tab labels directly above it. On this page it repeats nothing.
    """
    waiting_total = single_currency_total(pending)
    oldest_days, oldest_since = None, ""
    if not pending.empty:
        oldest = pending.sort_values("email_received_at", na_position="last").iloc[0]
        oldest_days = waiting_days(oldest)
        arrived = oldest.get("email_received_at")
        if arrived and not pd.isna(arrived):
            oldest_since = f"since {pd.Timestamp(arrived).strftime('%d %b').lstrip('0')}"

    st.markdown(
        "<div class='ov-strip'>"
        + tile("Waiting for you", len(pending), waiting_total or
               ("more than one currency" if len(pending) > 1 else "nothing waiting"))
        + tile("Approved by the system", len(auto), "no person involved")
        + tile("Oldest wait", f"{oldest_days}d" if oldest_days is not None else "none",
               oldest_since or "the queue is empty")
        + "</div>", unsafe_allow_html=True)

    section_head("Awaiting approval", "a person has to decide on each of these",
                 opens="awaiting", key="awaiting")
    document_rows(pending, actionable=True, context="ov", controls=False, limit=3)

    if not pending.empty or not auto.empty:
        cleared = "" if auto.empty else f" {len(auto)} were cleared by the system on its own."
        st.markdown(
            f"<div class='ov-line'><span class='dot' style='background:var(--positive)'></span>"
            f"That is everything waiting.{cleared}</div>", unsafe_allow_html=True)

    if not auto.empty:
        section_head("Approved by the system",
                     SYSTEM_APPROVAL_SECTION,
                     opens="auto", key="auto")
        with st.container(key="ovpanel-auto", gap=None):
          for _, r in auto.iterrows():
            # The score belongs on the row. This section exists to show what was approved
            # without a person, and the number behind that decision was only in the caption.
            #
            # SC-5, 2026-09-24: it read "score 1.00", which does not say which score. Every
            # other surface now names it, and a number the reader has to guess the meaning of
            # is the misreading fe-score-spec.md §3 exists to prevent.
            score = "-" if pd.isna(r["validation_score"]) else f"{r['validation_score']:.2f}"
            record_row(key=f"ovauto-{r['id']}", dot="var(--positive)",
                       left=r["vendor_name"] or "-", doc=r["invoice_number"] or "-",
                       value=money(r["total_amount"], r["currency"]),
                       right=f"validation score {score} · {local_time(r['system_processed_at'])[11:16]}",
                       action="Open", on_action=lambda row=r: review_dialog(row))

    if outbox.empty:
        pushable, frozen, sent = outbox, 0, 0
    else:
        jira_rows = outbox[outbox["channel"] == "Jira"]
        pushable = jira_rows[jira_rows["state"].isin(["Pending", "Failed"])
                             & ~jira_rows["withdrawn"]]
        frozen = len(outbox[outbox["channel"] != "Jira"])
        sent = int((outbox["state"] == "Sent").sum())
    # The section turns amber only when a row has actually failed, not merely because the
    # Outbox has rows in it. See fe-theme-v2-spec.md §4.
    any_failed = not pushable.empty and bool((pushable["state"] == "Failed").any())
    section_head("Outbox", f"{len(pushable)} ready to push, {frozen} with no transport, "
                           f"{sent} sent", opens="outbox",
                 key="outbox-failed" if any_failed else "outbox")
    if pushable.empty:
        st.markdown("<p class='ov-more'>Nothing is waiting to go out.</p>", unsafe_allow_html=True)
    else:
        ready = jira_ready()
        with st.container(key="ovpanel-out-failed" if any_failed else "ovpanel-out", gap=None):
         for _, r in pushable.head(5).iterrows():
            # The channel is Jira on every row in this list, so the slot that holds an amount
            # elsewhere holds the state instead: Pending or Failed is the thing worth reading.
            record_row(key=f"ovout-{r['outbox_id']}",
                       dot="var(--caution)" if r["state"] == "Failed" else "var(--text-muted)",
                       left=r["vendor_name"] or "-", doc=r["invoice_number"] or "-",
                       value=r["state"], right=local_time(r["created_at"]),
                       action="Push to Jira", disabled=not ready,
                       help=None if ready else "Jira is not configured. Set JIRA_ENABLED in .env",
                       on_action=lambda row=r: (push_to_jira(row), st.rerun()))

    section_head("History", "decisions a person made, including rejections",
                 opens="history", key="history")
    if history.empty:
        st.markdown("<p class='ov-more'>Nobody has decided anything yet.</p>",
                    unsafe_allow_html=True)
    else:
        decided = load_data()
        with st.container(key="ovpanel-hist", gap=None):
         for _, r in history.head(5).iterrows():
            # Not set_index("id"): review_dialog reads row["id"], and an index is not a column.
            match = decided[decided["id"] == r["invoice_id"]]
            full = match.iloc[0] if not match.empty else None
            # Keyed on the decision, not the invoice: a reopened document has several rows.
            record_row(key=f"ovhist-{int(r['decision_id'])}",
                       dot={"Rejected": "var(--caution)", "Reopened": "var(--text-muted)"}.get(
                           r["approval_status"], "var(--positive)"),
                       left=f"{r['approval_status']} · {r['vendor_name'] or '-'}",
                       doc=r["invoice_number"] or "-",
                       value=money(r["total_amount"], r["currency"]),
                       right=local_time(r["reviewed_at"]),
                       action="Open", disabled=full is None,
                       on_action=lambda row=full: review_dialog(row))

    st.markdown(
        "<p class='ov-foot'>Approving opens a Jira task for someone else, so the decision is "
        "made inside the document, not from this page.</p>", unsafe_allow_html=True)


# Reported by JJ on 2026-10-07: the heading read "Invoice approvals" on every page, so the page a
# person was on was only told by the sidebar. The product name stays, small, above the page's own
# name. It is Luke's choice of 2026-09-28 and the sign-in screens still carry it as their title.
PAGE_HEADINGS = {
    "overview": ("Overview", "What is waiting for a person, what the system cleared on its own, "
                             "and what has happened since."),
    "awaiting": ("Awaiting approval", "Documents a person has to decide on. Open one to approve "
                                      "or reject it."),
    "auto": ("Approved by the system", "Documents the system approved without asking anyone. A "
                                       "person can open and overturn any of them."),
    "outbox": ("Outbox", "Work handed to other systems, such as Jira. Nothing here has been "
                         "sent unless it says Sent."),
    "history": ("History", "Every decision a person made, newest first, including ones later "
                           f"taken back. Times are {LOCAL_TZ_LABEL} time."),
    "documents": ("Documents", "Every document the pipeline has stored."),
    "vendors": ("Vendors", "Every vendor across the documents, grouped by currency."),
    "runs": ("Pipeline runs", "Each time the pipeline ran, and the model that read the documents."),
}
_heading, _subtitle = PAGE_HEADINGS.get(view, ("Invoice approvals", ""))
st.markdown("<div class='page-eyebrow'>Invoice approvals</div>", unsafe_allow_html=True)
st.title(_heading)
if _subtitle:
    st.markdown(f"<p class='page-sub'>{_subtitle}</p>", unsafe_allow_html=True)

# Overview leads and is the default. fe-screen-spec.md §2 made Awaiting approval the default on
# the grounds that the screen exists to decide on documents, and amended it on 2026-09-20: the
# queue holds nothing most days, so opening onto it says nothing about what happened.


def vendors_panel(frame):
    """What `Vendors` in the sidebar resolves to.

    FE-15 removed this row on the grounds that it went to a screen nobody had specified. It did
    not need a screen. Three vendors appear across the documents and the question a person has
    about a vendor on this page is how much of it is waiting, so that is what it answers.
    """
    grouped = (frame.groupby(["vendor_name", "currency"], dropna=False)
               .agg(documents=("id", "count"), total=("total_amount", "sum"),
                    waiting=("approval_status", lambda c: int((c == "Pending").sum())))
               .reset_index().sort_values("total", ascending=False))
    st.markdown("<p class='tab-note'>Every vendor that appears across the documents, grouped "
                "with its own currency. Totals are never summed across currencies: that is the "
                "arithmetic this project watches the model for.</p>", unsafe_allow_html=True)
    rows = [("var(--caution)" if r["waiting"] else "var(--positive)",
             r["vendor_name"] or "Unknown vendor",
             f"{int(r['documents'])} document{'' if r['documents'] == 1 else 's'}",
             money(r["total"], r["currency"]),
             f"{int(r['waiting'])} waiting" if r["waiting"] else "all decided")
            for _, r in grouped.iterrows()]
    st.markdown(dense_rows(rows), unsafe_allow_html=True)


def runs_panel():
    """What `Pipeline runs` resolves to. `processing_runs` has been recording these all along.

    The columns are the ones the table actually has. An earlier draft of this function asked for
    `documents_seen` and `documents_stored`, which do not exist; the schema carries `doc_count`,
    `model_name` and `threshold` instead, and the model name is the useful one, because it is
    the only place on any screen that says which model read the documents.
    """
    with connect(DB_PATH) as conn:
        runs = pd.read_sql_query(
            "SELECT run_id, started_at, finished_at, model_name, threshold, doc_count, run_kind "
            "FROM processing_runs ORDER BY run_id DESC LIMIT 12", conn)
    st.markdown("<p class='tab-note'>The last twelve times the pipeline ran, newest first. This "
                "is the only place in the interface that names the model the documents were "
                "read by, which is worth knowing when a result is being compared against an "
                "older one.</p>", unsafe_allow_html=True)
    rows = [("var(--caution)" if not r["finished_at"] else "var(--positive)",
             f"run {int(r['run_id'])} &middot; {r['run_kind'] or 'unknown kind'}",
             r["model_name"] or "-",
             f"{int(r['doc_count'] or 0)} document{'' if r['doc_count'] == 1 else 's'}",
             local_time(r["started_at"]))
            for _, r in runs.iterrows()]
    st.markdown(dense_rows(rows), unsafe_allow_html=True)


def documents_panel(frame):
    """What `Documents` resolves to: every document, with the fields the card leaves out."""
    st.markdown("<p class='tab-note'>Every document the pipeline has stored. The approval card "
                "leaves these fields out because none of them changes a decision, which is not "
                "the same as them being worth hiding.</p>", unsafe_allow_html=True)
    # SC-6. This is the one panel that shows every document at once, so it is the only place
    # the spread of scores is visible: a column of 1.00, 1.00, 0.85 says something no single
    # card can, which is that the gate clears almost everything it is given.
    rows = [("var(--caution)" if r["approval_status"] == "Pending" else "var(--positive)",
             r["vendor_name"] or "Unknown vendor",
             r["file_name"] or "-",
             money(r["total_amount"], r["currency"]),
             f"run {int(r['run_id'])} &middot; {local_time(r['system_processed_at'])}",
             "-" if pd.isna(r["validation_score"]) else f"{r['validation_score']:.2f}")
            for _, r in frame.sort_values("id").iterrows()]
    st.markdown(dense_rows(rows), unsafe_allow_html=True)


if view == "documents":
    documents_panel(df)
elif view == "vendors":
    vendors_panel(df)
elif view == "runs":
    runs_panel()
elif view == "awaiting":
    document_rows(pending, actionable=True)
elif view == "auto":
    st.markdown(
        f"<p class='tab-note'>{SYSTEM_APPROVAL_NOTE}</p>",
        unsafe_allow_html=True)
    document_rows(auto, actionable=False)
elif view == "outbox":
    outbox_body(outbox)
elif view == "history":
    history_body(history)
else:
    overview_body(pending, auto, outbox, history)
