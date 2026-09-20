import os

import pandas as pd
import streamlit as st

import task_dispatch
from review_signals import risk_detail, risk_signal
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
            e.received_at AS email_received_at
        FROM invoices i
        LEFT JOIN email_messages e ON e.email_id = i.email_id
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
    """Records a human decision about an invoice.

    Writes approval_status and reviewed_at only. It deliberately does NOT touch:

      status            what the pipeline judged about data quality. Overwriting it would
                        destroy the record that the extractor was not confident.
      validation_score  the measurement itself. It used to be overwritten with 1.0, which
                        erased the only audit trail we had.

    A row can therefore read NeedsReview and Approved at the same time. That is correct and
    meaningful: the pipeline was not confident, and a person approved it anyway. The two
    columns are labelled "Data Quality" and "Approval" in the UI so it does not read as a
    contradiction.
    """
    if decision not in ("Approved", "Rejected", "Pending"):
        raise ValueError(f"unknown decision {decision!r}")
    conn = connect(DB_PATH)
    conn.execute(
        "UPDATE invoices SET approval_status = ?, reviewed_at = datetime('now') "
        "WHERE invoice_id = ?",
        (decision, record_id),
    )
    conn.commit()
    conn.close()

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
               i.vendor_name, i.invoice_number, i.file_name
        FROM outbound_messages o
        JOIN invoices i ON i.invoice_id = o.invoice_id
        ORDER BY o.created_at DESC
        """,
        conn,
    )
    conn.close()
    return df


def load_history() -> pd.DataFrame:
    """Documents a person decided on, and what happened to the work that followed.

    `reviewed_at` is the column that separates a person's decision from the system's, because
    `record_decision()` writes it and the auto-approval path never does. That is already how
    "Approved by the system" is defined, so History needs no new column and no new table.

    This is the only place a rejection is visible. A rejected document is not Pending, so it
    leaves the queue, and not Approved, so it never reaches the auto tab. Without this tab the
    decision is recorded and then cannot be seen anywhere.
    """
    conn = connect(DB_PATH)
    df = pd.read_sql_query(
        """
        SELECT i.invoice_id, i.invoice_number, i.vendor_name, i.document_type,
               i.total_cents / 100.0 AS total_amount, i.currency,
               i.approval_status, i.reviewed_at,
               t.task_type, t.state AS task_state, t.resolved_at
        FROM invoices i
        -- The task the decision actually resolved, which is the most recently resolved one.
        -- Joining on every matching task and grouping let SQLite pick an arbitrary row: an
        -- approved document showed "task Cancelled" because invoice 1 carries a Review task
        -- cancelled on 2026-08-28 alongside the Approve task completed on 2026-09-19.
        LEFT JOIN tasks t ON t.task_id = (
            SELECT task_id FROM tasks
            WHERE invoice_id = i.invoice_id
              AND task_type IN ('Review', 'Approve')
              AND resolved_at IS NOT NULL
            ORDER BY resolved_at DESC, task_id DESC
            LIMIT 1
        )
        WHERE i.reviewed_at IS NOT NULL
        ORDER BY i.reviewed_at DESC
        """,
        conn,
    )
    conn.close()
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

.card-head { display:grid; grid-template-columns:44px 1fr auto; gap:16px; align-items:start; padding:22px 24px 20px; }
.doc-icon { width:44px; height:44px; border-radius:10px; background:var(--control-fill);
            display:flex; align-items:center; justify-content:center; }
.card-id { display:flex; flex-direction:column; gap:7px; min-width:0; }
.card-title { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.vendor { font-size:17px; font-weight:600; letter-spacing:-0.01em; color:var(--text); }
.card-meta { font-family:'IBM Plex Mono',monospace; font-size:12.5px; color:var(--text-muted); }
.card-head .amount { font-family:'IBM Plex Mono',monospace; font-size:22px; font-weight:500;
                     text-align:right; white-space:nowrap; color:var(--text); }

.chip { display:inline-flex; align-items:center; gap:7px; font-size:12px; color:var(--text-strong);
        background:var(--fill-subtle); border-radius:7px; padding:4px 10px; margin-right:8px; }
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
[class*="st-key-ovhist-"] [data-testid="stColumn"]:last-child { display:flex; justify-content:flex-end; }
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

.tab-note { font-size:13px; line-height:1.6; color:var(--text-muted); max-width:860px;
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
  padding:2px 10px 2px 4px !important; border-radius:10px; }
[class*="st-key-ovpanel-"] > div > div:last-child [class*="st-key-ov"] { border-bottom:none; }
[class*="st-key-ovauto-"] .dense, [class*="st-key-ovout-"] .dense,
[class*="st-key-ovhist-"] .dense { border-bottom:none; padding:10px 14px; }
.hist { display:flex; align-items:center; gap:14px; flex-wrap:wrap; }
.hist-decision { font-size:13px; font-weight:500; color:var(--text); min-width:72px; }
.hist-vendor { font-size:13.5px; color:var(--text); }
.hist-doc { font-family:'IBM Plex Mono',monospace; font-size:12.5px; color:var(--text-muted); }
.hist-amount { font-family:'IBM Plex Mono',monospace; font-size:13px; font-variant-numeric:tabular-nums; color:var(--text); }
.hist-when { font-family:'IBM Plex Mono',monospace; font-size:12px; color:var(--text-muted); margin-left:auto; }
.hist-task { font-size:12px; color:var(--text-muted); }

/* The sidebar. FE-15. Streamlit paints the surface from [theme.sidebar] in config.toml; these
   rules cover the identity block and the saved views, which are markdown and a radio. */
.side-id { display:flex; align-items:center; gap:10px; padding:2px 2px 16px; }
.side-mark { width:26px; height:26px; border-radius:7px; background:var(--navy); flex:none;
             display:flex; align-items:center; justify-content:center; }
.side-name { display:flex; flex-direction:column; line-height:1.25; }
.side-title { font-size:13.5px; font-weight:600; letter-spacing:-0.005em; color:var(--text); }
.side-sub { font-size:11.5px; color:var(--text-muted); }
.side-label { font-size:11px; font-weight:500; letter-spacing:0.04em; text-transform:uppercase;
              color:var(--text-muted); margin:18px 0 6px; }
.side-foot { margin-top:22px; padding:11px 13px; border-radius:10px;
             background:var(--fill-subtle); line-height:1.5; }
/* The selected saved view reads as a filled row rather than a dot, which is how every
   reference product marks the thing you are currently looking at. */
[data-testid="stSidebar"] [role="radiogroup"] label { border-radius:8px; padding:5px 9px;
                                                      margin:0 0 1px; }
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {
  background:var(--accent-wash); color:var(--accent-text); }

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


def render_document(path, height: int = 420):
    """Show the text the model actually read, and offer the file itself.

    **Not the rendered page, and three approaches were tried before settling here.**
    `st.pdf` exists in Streamlit 1.62.0 but raises unless the separate `streamlit-pdf` component
    is installed, and version 2.0.1 of that component fails on import against this Streamlit.
    Embedding the file as a `data:` URI renders nothing, because Streamlit sandboxes the iframe
    `st.html` produces. Rasterising the first page would work and costs a binary dependency that
    every teammate would have to install.

    So the panel shows the extracted text. That is a downgrade for layout and an upgrade for the
    job: the question an approver is answering is whether the model read the document correctly,
    and this is character for character what the model was given. A rendered page would show
    what the document looks like; this shows what the pipeline saw.

    The original is one click away for anyone who needs the layout.
    """
    if not path or not os.path.exists(path):
        st.caption("The archived file is no longer on disk.")
        return

    with open(path, "rb") as handle:
        data = handle.read()

    try:
        from pypdf import PdfReader
        text = "\n".join((page.extract_text() or "") for page in PdfReader(path).pages)
    except Exception:                                   # noqa: BLE001
        text = ""

    st.caption("What the model read")
    if text.strip():
        st.text_area(
            "document text", value=text, height=height,
            label_visibility="collapsed", disabled=True,
        )
    else:
        st.caption("No text layer. This document would need OCR, which the pipeline does not do.")

    st.download_button(
        "Download the original", data=data,
        file_name=os.path.basename(path), mime="application/pdf",
    )


@st.dialog("Review document", width="large")
def review_dialog(row):
    """The only place approve and reject exist.

    Not in the row, deliberately. Objective 4 of this project is to keep a person in the
    approval path, and a person approving from the row decides on exactly the information the
    machine had, which is the decision the machine already makes by itself at a score of 1.00.
    The extra click buys a look at the document, the evidence quotes and the covering email.

    Ordered by the questions a person asks: what am I approving, is there a concern, let me
    look, where did it come from, what happens if I approve.
    """
    st.subheader(row["vendor_name"] or "Unknown vendor")
    st.markdown(
        f"<span class='amount'><strong>{money(row['total_amount'], row['currency'])}</strong>"
        f"</span> &nbsp; {row['invoice_number'] or '-'} &middot; {row['document_type']}",
        unsafe_allow_html=True,
    )

    signal = risk_signal(row)
    if signal:
        st.markdown(f"<p class='signal'>{signal}</p>", unsafe_allow_html=True)

    left, right = st.columns([3, 2])
    with left:
        render_document(row.get("archive_path"))
    with right:
        for label, value in (
            ("Date", row.get("invoice_date")),
            ("Currency", row.get("currency")),
            ("Total source", row.get("total_source")),
            ("Vendor source", row.get("vendor_source")),
        ):
            st.markdown(f"**{label}** &nbsp; `{value or '-'}`")
        items = load_line_items(int(row["id"]))
        st.markdown(f"**Line items** &nbsp; `{len(items) or 'none'}`")
        if not items.empty:
            st.dataframe(items, hide_index=True, use_container_width=True)

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
            st.caption(
                "**Tick one when you have dealt with it.** It saves immediately and is a note "
                "to whoever opens this document next. Nothing downstream reads it: it does not "
                "affect the validation score, the approval, or the Jira task."
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
    left, right = st.columns([5, 1.2], vertical_alignment="center")
    if row.get("review_note_at") and not pd.isna(row.get("review_note_at")):
        left.markdown(f"<p class='note-when'>Last saved {row['review_note_at']}</p>",
                      unsafe_allow_html=True)
    if right.button("Save note", key=f"savenote-{row['id']}", width="stretch"):
        save_review_note(int(row["id"]), note)
        st.rerun()

    st.divider()
    # FE-12, corrected 2026-09-20. This said "Approving creates a Jira task Payment
    # immediately", which is false whenever Jira is unconfigured, and it is unconfigured now.
    # `record_decision` always writes a local follow-up task and queues an outbox row;
    # `dispatch_task_to_jira` then returns without contacting anything unless JIRA_ENABLED and
    # the .env are set. A screen that claims more than the system does is the exact failure
    # this project exists to catch, and it was doing it on the one line that exists to warn a
    # person before they act.
    #
    # The title is built by the dispatcher's own function rather than re-spelled here, so the
    # preview cannot drift from the issue. `_build_summary` is private and lives in Luke's
    # file; it is called read-only and pinned by tests/test_app_jira_preview.py. Making it
    # public is a one-line PR whenever that is worth doing.
    task_title = task_dispatch._build_summary(
        follow_up_for(row["document_type"]), row["file_name"], row["vendor_name"])
    if jira_ready():
        st.markdown(f"Approving creates a Jira issue immediately, titled `{task_title}`.")
    else:
        st.markdown(
            f"Approving opens a task titled `{task_title}` and queues it for Jira. "
            f"**Jira is not configured, so nothing is sent**: the row waits in the Outbox "
            f"until `JIRA_ENABLED` is set.")
    # Two identical full-width buttons made Reject look as inviting as Approve, and both of
    # them look like the third button on the screen rather than the decision the dialog exists
    # for. approval-screen-design-v2.html fills Approve and leaves Reject quiet, both sitting at
    # the right edge under the line that says what approving will do.
    _spacer, reject, approve = st.columns([4, 1.1, 1.2], vertical_alignment="center")
    if reject.button("Reject", key=f"reject-{row['id']}", width="stretch"):
        record_decision(int(row["id"]), "Rejected")
        st.rerun()
    if approve.button("Approve", type="primary", key=f"approve-{row['id']}", width="stretch"):
        record_decision(int(row["id"]), "Approved")
        st.rerun()


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
            f"<div class='amount'>{money(row['total_amount'], row['currency'])}</div>"
            f"</div>{signal_html}",
            unsafe_allow_html=True,
        )
        with st.container(key=f"foot-{context}-{row['id']}", gap=None):
            chips, download, action = st.columns([4, 1.4, 1.3], vertical_alignment="center")
            chips.markdown(f"<div class='chips'>{provenance(row)}</div>", unsafe_allow_html=True)

            original = original_for(row)
            if original:
                data, name = original
                download.download_button("Download the original", data=data, file_name=name,
                                         mime="application/pdf",
                                         key=f"dl-{context}-{row['id']}")
            else:
                download.button("Download the original", disabled=True,
                                key=f"dl-{context}-{row['id']}",
                                help="The archived file is not on disk.")

            # OV-6. A document the system approved without asking can still be opened. The
            # dialog is the same one, so a person can look at what was decided for them and
            # reject it if they disagree, which is the oversight §2 of fe-screen-spec.md says
            # the second tab exists to make possible.
            label = "Review document" if actionable else "Open the document"
            if action.button(label, type="primary" if actionable else "secondary",
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
    when = f" The last {len(decided)} it cleared on its own at {str(latest)[11:16]}." if latest else ""
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

    Deliberately excluded from the card: file name, ingestion time, run_id and the raw
    validation score. All are available and none of them changes a decision.
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


def flagged(frame) -> pd.Series:
    """Rows the gate put a sentence against. `review_signals` owns that call, not this file."""
    from review_signals import has_signal
    if frame.empty:
        return pd.Series(dtype=bool)
    return frame.apply(has_signal, axis=1)


def saved_views(frame) -> dict:
    """The two views, as the queries they claim to be.

    `fe-backlog.md` defined `Cleared this week` as `approval_status = 'Approved' AND reviewed_at
    IS NULL`, which is every document the system ever cleared and has no week in it at all. A
    label that promises a week and returns all time is the same class of overstatement as the
    approve line fixed in 8750a8c, so the week is real here: seven days back from now, measured
    on `system_processed_at`.
    """
    pending_rows = frame[frame["approval_status"] == "Pending"]
    marked = pending_rows[flagged(pending_rows)] if not pending_rows.empty else pending_rows

    cleared = frame[(frame["approval_status"] == "Approved") & (frame["reviewed_at"].isna())]
    if not cleared.empty:
        seen = pd.to_datetime(cleared["system_processed_at"], errors="coerce")
        cleared = cleared[seen >= pd.Timestamp.now() - pd.Timedelta(days=7)]

    return {
        "all": ("All documents", frame, None),
        "flagged": ("Flagged by the model", marked, "var(--caution)"),
        "cleared": ("Cleared this week", cleared, "var(--positive)"),
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


def sidebar(frame):
    """Draws the sidebar and returns the frame the whole page is built from."""
    views = saved_views(frame)
    with st.sidebar:
        st.markdown(
            "<div class='side-id'><div class='side-mark'>"
            "<svg width='14' height='14' viewBox='0 0 16 16' fill='none'>"
            "<path d='M3 2.5h7l3 3v8a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1v-10a1 1 0 0 1 1-1Z' "
            "stroke='#FFFFFF' stroke-width='1.3' stroke-linejoin='round'/>"
            "<path d='M9.5 2.5v3.5H13' stroke='#FFFFFF' stroke-width='1.3' "
            "stroke-linejoin='round'/></svg></div>"
            "<div class='side-name'><span class='side-title'>Payables</span>"
            "<span class='side-sub'>Finance operations</span></div></div>",
            unsafe_allow_html=True)

        query = st.text_input("Search documents", key="search",
                              placeholder="Vendor, invoice number or file",
                              label_visibility="collapsed")

        st.markdown("<div class='side-label'>Saved views</div>", unsafe_allow_html=True)
        chosen = st.radio(
            "Saved views", list(views), key="view", label_visibility="collapsed",
            format_func=lambda key: f"{views[key][0]}  ·  {len(views[key][1])}")

        # The account block is honest about what it is. There is no sign-in anywhere in this
        # system, so naming an approver would be a claim the software cannot support.
        st.markdown(
            f"<div class='side-foot'><span class='side-sub'>Running locally on this machine."
            f" No sign-in, so every decision is recorded without an author.</span></div>",
            unsafe_allow_html=True)

    return matches(views[chosen][1], query), chosen, query


df = load_data()
if df.empty:
    st.title("Invoice approvals")
    st.write("No documents have been processed yet. Run `main.py` over a document in `inbox/`.")
    st.stop()

df, view, query = sidebar(df)
# A filter has to reach the whole page or the counts on the tabs contradict the rows beneath
# them. outbox and history are separate queries, so they are narrowed by membership.
filtered = view != "all" or bool(query.strip())
keep = set(df["id"])
pending = df[df["approval_status"] == "Pending"]
auto = df[(df["approval_status"] == "Approved") & (df["reviewed_at"].isna())]
outbox = load_outbox()
history = load_history()
if filtered:
    outbox = outbox[outbox["invoice_id"].isin(keep)]
    history = history[history["invoice_id"].isin(keep)]

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


def pending_label(frame) -> str:
    total = single_currency_total(frame)
    return f"Awaiting approval  {len(frame)}" + (f" · {total}" if total else "")

def push_to_jira(row) -> None:
    """Send one outbox row to Jira, through the path that already exists.

    `task_dispatch.dispatch_task_to_jira` looks for an existing Pending or Failed outbox row for
    the same task and reuses it rather than queueing a second one, so retry was designed in from
    the start. This button is that retry, with a person pressing it.
    """
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
    pushable = jira_rows[jira_rows["state"].isin(["Pending", "Failed"])]
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
            pick, body, action = st.columns([0.5, 5, 1.3], vertical_alignment="center")
            pick.checkbox("Select", key=f"sel-{row['outbox_id']}", label_visibility="collapsed")
            state_dot = "var(--caution)" if row["state"] == "Failed" else "var(--text-faint)"
            failure = f"<div class='out-error'>{row['error']}</div>" if row["error"] else ""
            body.markdown(
                f"<div class='out-row'><div class='out-head'>"
                f"<span class='dot' style='background:{state_dot}'></span>"
                f"<span class='out-doc'>{row['invoice_number'] or '-'}</span>"
                f"<span class='out-vendor'>{row['vendor_name'] or '-'}</span>"
                f"<span class='out-when'>{row['created_at']}</span></div>"
                f"<div class='out-payload'>{row['payload']}</div>{failure}</div>",
                unsafe_allow_html=True)
            if action.button("Push to Jira", key=f"push-{row['outbox_id']}",
                             type="primary", disabled=not ready):
                push_to_jira(row)
                st.rerun()

    sent = jira_rows[jira_rows["state"] == "Sent"]
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
        "<p class='tab-note'>Decisions made by a person. This is the only place a rejection is "
        "visible: a rejected document is not Pending, so it leaves the queue, and not Approved, "
        "so it never reaches the system tab.</p>", unsafe_allow_html=True)
    for _, row in frame.iterrows():
        rejected = row["approval_status"] == "Rejected"
        with st.container(border=True, key=f"hist-{row['invoice_id']}", gap=None):
            st.markdown(
                f"<div class='hist'>"
                f"<span class='dot' style='background:"
                f"{'var(--caution)' if rejected else 'var(--positive)'}'></span>"
                f"<span class='hist-decision'>{row['approval_status']}</span>"
                f"<span class='hist-vendor'>{row['vendor_name'] or '-'}</span>"
                f"<span class='hist-doc'>{row['invoice_number'] or '-'}</span>"
                f"<span class='hist-amount'>{money(row['total_amount'], row['currency'])}</span>"
                f"<span class='hist-when'>{row['reviewed_at']}</span>"
                f"<span class='hist-task'>task {row['task_state'] or 'none'}</span>"
                f"</div>", unsafe_allow_html=True)


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
    """A section heading, and the control that opens the tab it summarises.

    Streamlit 1.62.0 takes `key` on `st.tabs`, so writing that key and rerunning selects a tab.
    Measured before this was built: the key raises KeyError until a tab is chosen, and setting
    it to a label lands on that tab with no exception. Plausible puts the same control at the
    top right of every panel; a section that cannot be opened is a dead end.
    """
    # The rule under a heading belongs to the whole row, not to the column the text sits in,
    # so it goes on the container rather than on the markdown inside it.
    with st.container(key=f"sechead-{key}"):
        head, action = st.columns([8, 1], vertical_alignment="center")
        del opens  # OV-4 is not buildable on st.tabs. See fe-backlog.md.
        # "outbox-failed" is still the outbox icon. The suffix exists to drive the
        # header colour from CSS, not to name a different section.
        icon = SECTION_ICONS.get(key.split("-")[0], "")
        mark = (f"<span class='sec-icon'><svg width='15' height='15' viewBox='0 0 16 16' "
                f"fill='none'>{icon}</svg></span>" if icon else "")
        head.markdown(f"<div class='sec-head'>{mark}<span class='sec-title'>{title}</span>"
                      f"<span class='sec-note'>{note}</span></div>", unsafe_allow_html=True)
        del action


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
    """
    out = []
    for dot, left, doc, amount, right in rows:
        out.append(f"<div class='dense'><span class='dot' style='background:{dot}'></span>"
                   f"<span class='dense-left'>{left}</span>"
                   f"<span class='dense-doc'>{doc}</span>"
                   f"<span class='dense-amount'>{amount}</span>"
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
                 opens=TAB_AWAITING, key="awaiting")
    document_rows(pending, actionable=True, context="ov", controls=False, limit=3)

    if not pending.empty or not auto.empty:
        cleared = "" if auto.empty else f" {len(auto)} were cleared by the system on its own."
        st.markdown(
            f"<div class='ov-line'><span class='dot' style='background:var(--positive)'></span>"
            f"That is everything waiting.{cleared}</div>", unsafe_allow_html=True)

    if not auto.empty:
        section_head("Approved by the system",
                     "at a validation score of 1.00, with nobody asked",
                     opens=TAB_AUTO, key="auto")
        with st.container(key="ovpanel-auto", gap=None):
          for _, r in auto.iterrows():
            # The score belongs on the row. This section exists to show what was approved
            # without a person, and the number behind that decision was only in the caption.
            score = "-" if pd.isna(r["validation_score"]) else f"{r['validation_score']:.2f}"
            record_row(key=f"ovauto-{r['id']}", dot="var(--positive)",
                       left=r["vendor_name"] or "-", doc=r["invoice_number"] or "-",
                       value=money(r["total_amount"], r["currency"]),
                       right=f"score {score} · {str(r['system_processed_at'])[11:16]}",
                       action="Open", on_action=lambda row=r: review_dialog(row))

    if outbox.empty:
        pushable, frozen, sent = outbox, 0, 0
    else:
        jira_rows = outbox[outbox["channel"] == "Jira"]
        pushable = jira_rows[jira_rows["state"].isin(["Pending", "Failed"])]
        frozen = len(outbox[outbox["channel"] != "Jira"])
        sent = int((outbox["state"] == "Sent").sum())
    # The section turns amber only when a row has actually failed, not merely because the
    # Outbox has rows in it. See fe-theme-v2-spec.md §4.
    any_failed = not pushable.empty and bool((pushable["state"] == "Failed").any())
    section_head("Outbox", f"{len(pushable)} ready to push, {frozen} with no transport, "
                           f"{sent} sent", opens=TAB_OUTBOX,
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
                       value=r["state"], right=str(r["created_at"])[:16],
                       action="Push to Jira", disabled=not ready,
                       help=None if ready else "Jira is not configured. Set JIRA_ENABLED in .env",
                       on_action=lambda row=r: (push_to_jira(row), st.rerun()))

    section_head("History", "decisions a person made, including rejections",
                 opens=TAB_HISTORY, key="history")
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
            record_row(key=f"ovhist-{r['invoice_id']}",
                       dot="var(--caution)" if r["approval_status"] == "Rejected"
                           else "var(--positive)",
                       left=f"{r['approval_status']} · {r['vendor_name'] or '-'}",
                       doc=r["invoice_number"] or "-",
                       value=money(r["total_amount"], r["currency"]),
                       right=str(r["reviewed_at"])[:16],
                       action="Open", disabled=full is None,
                       on_action=lambda row=full: review_dialog(row))

    st.markdown(
        "<p class='ov-foot'>Approving opens a Jira task for someone else, so the decision is "
        "made inside the document, not from this page.</p>", unsafe_allow_html=True)


st.title("Invoice approvals")

# Overview leads and is the default. fe-screen-spec.md §2 made Awaiting approval the default on
# the grounds that the screen exists to decide on documents, and amended it on 2026-09-20: the
# queue holds nothing most days, so opening onto it says nothing about what happened.
TAB_AWAITING = pending_label(pending)
TAB_AUTO = f"Approved by the system  {len(auto)}"
TAB_OUTBOX = f"Outbox  {len(outbox)}"
TAB_HISTORY = f"History  {len(history)}"

overview, awaiting_tab, auto_tab, outbox_tab, history_tab = st.tabs(
    ["Overview", TAB_AWAITING, TAB_AUTO, TAB_OUTBOX, TAB_HISTORY], key="nav")

with overview:
    overview_body(pending, auto, outbox, history)

with awaiting_tab:
    document_rows(pending, actionable=True)

with auto_tab:
    st.markdown(
        "<p class='tab-note'>These were approved at a validation score of 1.00 with no person "
        "involved. Every check behind that score reads the document itself, so a duplicate, an "
        "unknown vendor and a well-formatted forgery all score the same.</p>",
        unsafe_allow_html=True)
    document_rows(auto, actionable=False)

with outbox_tab:
    outbox_body(outbox)

with history_tab:
    history_body(history)
