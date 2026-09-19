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
    initial_sidebar_state="collapsed",
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
  --ground:#F6F6F7;  --surface:#FFFFFF;  --surface-sunk:#FBFBFC;
  --border-subtle:#EDEDEF;  --border:#E1E1E4;  --border-hover:#C9C9CF;
  --text:#1C1C1F;  --text-strong:#3F3F46;  --text-muted:#6E6E76;
  --text-faint:#A1A1A8;  --text-disabled:#C2C2C8;
  --accent:#5B5BD6;  --accent-hover:#4F4FC9;  --accent-text:#3E3EA8;  --accent-wash:#EEEEFB;
  --caution:#A65A1E;  --caution-text:#8A4A18;  --caution-wash:#FDFCFB;
  --positive:#3D9A50;  --positive-wash:#EAF5EC;
  --control-fill:#F1F1F3;  --row-hover:#FAFAFB;
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
  box-shadow: 0 1px 2px rgba(24,24,28,0.05);
}
[class*="st-key-doc-"]:hover { border-color: var(--border-hover); box-shadow: 0 2px 6px rgba(24,24,28,0.07); }
[class*="st-key-doc-"] .stMarkdown p { margin: 0; }

.card-head { display:grid; grid-template-columns:44px 1fr auto; gap:16px; align-items:start; padding:22px 24px 20px; }
.doc-icon { width:44px; height:44px; border-radius:10px; background:var(--control-fill);
            border:1px solid var(--border-subtle); display:flex; align-items:center; justify-content:center; }
.card-id { display:flex; flex-direction:column; gap:7px; min-width:0; }
.card-title { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.vendor { font-size:17px; font-weight:600; letter-spacing:-0.01em; color:var(--text); }
.card-meta { font-family:'IBM Plex Mono',monospace; font-size:12.5px; color:var(--text-muted); }
.card-head .amount { font-family:'IBM Plex Mono',monospace; font-size:22px; font-weight:500;
                     text-align:right; white-space:nowrap; color:var(--text); }

.chip { display:inline-flex; align-items:center; gap:7px; font-size:12px; color:var(--text-strong);
        background:var(--surface); border:1px solid var(--border); border-radius:7px; padding:3px 9px; margin-right:8px; }
.dot { width:6px; height:6px; border-radius:50%; flex:none; }

/* The sentence comes from review_signals.py. Nothing in this file writes signal copy. */
.signal-strip { display:flex; align-items:center; gap:10px; padding:13px 24px;
                border-top:1px solid var(--border-subtle); background:var(--caution-wash); }
.signal-strip .dot { width:8px; height:8px; }
.signal { color: var(--caution-text); font-size:14px; }
.signal-detail { font-size:13px; color:var(--text-muted); }

[class*="st-key-foot-"] { padding:13px 24px !important; border-top:1px solid var(--border-subtle); }
.chips { display:flex; align-items:center; flex-wrap:wrap; row-gap:6px; }
.chips.decided { justify-content:flex-end; font-size:12.5px; color:var(--text-muted); }
/* The action sits at the card's right edge, not at the left of whatever column it landed in. */
[class*="st-key-rev-"] { display:flex; justify-content:flex-end; }
.queue-count { font-size:13px; color:var(--text-muted); }

.empty { display:flex; flex-direction:column; align-items:center; gap:10px; padding:44px 28px;
         background:var(--surface); border:1px solid var(--border); border-radius:12px; }
.empty-mark { width:42px; height:42px; border-radius:50%; background:var(--positive-wash);
              display:flex; align-items:center; justify-content:center; }
.empty-title { font-size:15px; font-weight:600; color:var(--text); }
.empty-note { font-size:13px; color:var(--text-muted); }

/* Overview. Four tiles, one per tab, built from C3 in approval-screen-components.html, which
   was drawn for the queue screen and rejected there as duplication of the tab labels. */
/* Overview is a page, not a grid of counters. Three tiles across the top, then one section
   per tab in the order a person asks about them, each carrying its own rows. */
.ov-strip { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin-bottom:26px; }
.ov-tile { background:var(--surface); border:1px solid var(--border); border-radius:10px;
           box-shadow:0 1px 2px rgba(24,24,28,0.04); padding:16px 18px;
           display:flex; flex-direction:column; gap:7px; }
.ov-label { font-size:12px; color:var(--text-muted); }
.ov-value { display:flex; align-items:baseline; gap:9px; flex-wrap:wrap; }
.ov-number { font-family:'IBM Plex Mono',monospace; font-size:26px; font-weight:500;
             letter-spacing:-0.01em; color:var(--text); }
.ov-note { font-family:'IBM Plex Mono',monospace; font-size:13px; color:var(--text-muted);
           font-variant-numeric:tabular-nums; }

.sec-head { display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; margin:0;
            line-height:2.4; }
[class*="st-key-sechead-"] { margin:26px 0 12px !important; padding-bottom:6px !important;
                             border-bottom:1px solid var(--border); }
[class*="st-key-open-"] { display:flex; justify-content:flex-end; }
.sec-title { font-size:14px; font-weight:600; color:var(--text); }
.sec-note { font-size:12.5px; color:var(--text-muted); }

.dense-panel { background:var(--surface); border:1px solid var(--border); border-radius:12px;
               box-shadow:0 1px 2px rgba(24,24,28,0.04); overflow:hidden; }
.dense { display:grid; grid-template-columns:auto 1fr auto auto auto; align-items:center;
         gap:16px; padding:13px 18px; border-bottom:1px solid var(--border-subtle); }
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
.ov-foot { font-size:12.5px; color:var(--text-muted); margin:26px 0 0;
           padding-top:14px; border-top:1px solid var(--border-subtle); }

.tab-note { font-size:13px; line-height:1.6; color:var(--text-muted); max-width:860px; margin:0 0 14px; }
.tab-warn { font-size:13px; line-height:1.6; color:var(--caution-text); background:var(--caution-wash);
            border:1px solid var(--border-subtle); border-radius:10px; padding:11px 14px; margin:0 0 14px; }
.tab-note code, .tab-warn code { font-family:'IBM Plex Mono',monospace; font-size:12px; }

/* Outbox rows that can be pushed. The Teams rows stay in a plain table: they have no action,
   because nothing in this codebase has written one since 2026-09-08 and none ever will. */
[class*="st-key-out-"] { padding:13px 18px !important; border-radius:10px; background:var(--surface); }
.out-row { display:flex; flex-direction:column; gap:5px; }
.out-head { display:flex; align-items:center; gap:10px; }
.out-doc { font-family:'IBM Plex Mono',monospace; font-size:12.5px; color:var(--text); }
.out-vendor { font-size:13px; color:var(--text-strong); }
.out-when { font-family:'IBM Plex Mono',monospace; font-size:12px; color:var(--text-muted); margin-left:auto; }
.out-payload { font-size:12.5px; color:var(--text-muted); }
.out-error { font-size:12px; color:var(--caution-text); }
[class*="st-key-push-"] { display:flex; justify-content:flex-end; }

/* History. One row per decision a person made. */
[class*="st-key-hist-"] { padding:12px 18px !important; border-radius:10px; background:var(--surface); }
/* Rows that carry an action. The border comes from the panel they sit in, not from each row. */
[class*="st-key-ovpanel-"] { background:var(--surface); border:1px solid var(--border);
  border-radius:12px; box-shadow:0 1px 2px rgba(24,24,28,0.04); overflow:hidden;
  padding:0 !important; }
[class*="st-key-ovauto-"], [class*="st-key-ovout-"], [class*="st-key-ovhist-"] {
  padding:4px 10px 4px 4px !important; border-bottom:1px solid var(--border-subtle); }
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
                "separates a real action from an invented one."
            )
            for _, item in actions.iterrows():
                st.checkbox(
                    item["Action"], value=bool(item["is_done"]),
                    key=f"act-{item['action_item_id']}",
                    on_change=set_action_item_done,
                    args=(int(item["action_item_id"]), not bool(item["is_done"])),
                )
                st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;*“{item['Evidence']}”*")

    st.divider()
    st.markdown(
        f"Approving creates a Jira task **{follow_up_for(row['document_type'])}** immediately."
    )
    approve, reject = st.columns(2)
    if approve.button("Approve", use_container_width=True):
        record_decision(int(row["id"]), "Approved")
        st.rerun()
    if reject.button("Reject", use_container_width=True):
        record_decision(int(row["id"]), "Rejected")
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
    signal_html = (
        f"<div class='signal-strip'><span class='dot' style='background:var(--caution)'></span>"
        f"<span class='signal'>{signal}</span>"
        f"<span class='signal-detail'>{detail or ''}</span></div>" if signal else ""
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
        if actionable:
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


df = load_data()
if df.empty:
    st.title("Invoice approvals")
    st.write("No documents have been processed yet. Run `main.py` over a document in `inbox/`.")
    st.stop()

pending = df[df["approval_status"] == "Pending"]
auto = df[(df["approval_status"] == "Approved") & (df["reviewed_at"].isna())]
outbox = load_outbox()
history = load_history()

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
        head, action = st.columns([6, 1], vertical_alignment="center")
        head.markdown(f"<div class='sec-head'><span class='sec-title'>{title}</span>"
                      f"<span class='sec-note'>{note}</span></div>", unsafe_allow_html=True)
        if opens and action.button("Open", key=f"open-{key}", type="tertiary"):
            st.session_state["nav"] = opens
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
            record_row(key=f"ovauto-{r['id']}", dot="var(--positive)",
                       left=r["vendor_name"] or "-", doc=r["invoice_number"] or "-",
                       value=money(r["total_amount"], r["currency"]),
                       right=str(r["system_processed_at"])[11:16],
                       action="Open", on_action=lambda row=r: review_dialog(row))

    if outbox.empty:
        pushable, frozen, sent = outbox, 0, 0
    else:
        jira_rows = outbox[outbox["channel"] == "Jira"]
        pushable = jira_rows[jira_rows["state"].isin(["Pending", "Failed"])]
        frozen = len(outbox[outbox["channel"] != "Jira"])
        sent = int((outbox["state"] == "Sent").sum())
    section_head("Outbox", f"{len(pushable)} ready to push, {frozen} with no transport, "
                           f"{sent} sent", opens=TAB_OUTBOX, key="outbox")
    if pushable.empty:
        st.markdown("<p class='ov-more'>Nothing is waiting to go out.</p>", unsafe_allow_html=True)
    else:
        ready = jira_ready()
        with st.container(key="ovpanel-out", gap=None):
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
        decided = load_data().set_index("id")
        with st.container(key="ovpanel-hist", gap=None):
         for _, r in history.head(5).iterrows():
            full = decided.loc[r["invoice_id"]] if r["invoice_id"] in decided.index else None
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
