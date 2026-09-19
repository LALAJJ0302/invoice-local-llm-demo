import os

import pandas as pd
import streamlit as st

import task_dispatch
from review_signals import risk_signal
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

def load_pending_notifications() -> pd.DataFrame:
    """The outbox: what the pipeline would have sent, had anything been wired to send it.

    FR-6.2 requires every intended notification to be recorded rather than printed. 17 rows have
    been recorded since 30 August and no interface has ever displayed one.

    `created_at` is selected because the payload decays. The oldest row still reads "NeedsReview
    at score 0.25" for a document that now reads Validated at 1.00: the message was written when
    it was true and nothing rewrites it. Showing the age turns a stale sentence into the honest
    point, which is that a queue nobody drains stops describing the present.
    """
    conn = connect(DB_PATH)
    df = pd.read_sql_query(
        """
        SELECT o.outbox_id, o.channel, o.payload, o.created_at,
               i.vendor_name, i.invoice_number
        FROM outbound_messages o
        JOIN invoices i ON i.invoice_id = o.invoice_id
        WHERE o.state = 'Pending'
        ORDER BY o.created_at DESC
        """,
        conn,
    )
    conn.close()
    return df


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


def document_rows(frame, *, actionable: bool):
    """One row per document. Six columns, not eleven.

    Deliberately excluded: file name, ingestion time, run_id, the raw validation score and the
    invoice date. All are available and none of them changes a decision.
    """
    if frame.empty:
        st.caption("Nothing here." if not actionable else "Nothing is waiting for you.")
        return

    header = st.columns([3, 2, 2, 1.5, 4, 1.5])
    for column, label in zip(header, ("Vendor", "Amount", "Document", "Type", "", "")):
        column.caption(label)

    for _, row in frame.iterrows():
        vendor, amount, number, doc_type, signal_cell, action = st.columns([3, 2, 2, 1.5, 4, 1.5])
        vendor.write(row["vendor_name"] or "-")
        amount.markdown(
            f"<div class='amount' style='text-align:right'>"
            f"{money(row['total_amount'], row['currency'])}</div>",
            unsafe_allow_html=True,
        )
        number.write(row["invoice_number"] or "-")
        doc_type.write(row["document_type"])
        signal = risk_signal(row)
        if signal:
            signal_cell.markdown(f"<span class='signal'>{signal}</span>", unsafe_allow_html=True)
        if action.button("Open", key=f"open-{row['id']}", use_container_width=True):
            review_dialog(row)


df = load_data()
if df.empty:
    st.title("Invoice approvals")
    st.write("No documents have been processed yet. Run `main.py` over a document in `inbox/`.")
    st.stop()

pending = df[df["approval_status"] == "Pending"]
auto = df[(df["approval_status"] == "Approved") & (df["reviewed_at"].isna())]
notifications = load_pending_notifications()

pending_value = pending["total_amount"].sum() if not pending.empty else 0

st.title("Invoice approvals")

# The counts live on the tab labels. Every number is therefore visible without a click, and
# there is no separate metric strip duplicating them.
awaiting_tab, auto_tab, outbox_tab = st.tabs([
    f"Awaiting approval  {len(pending)} · {pending_value:,.0f}",
    f"Approved by the system  {len(auto)}",
    f"Notifications  {len(notifications)}",
])

with awaiting_tab:
    document_rows(pending, actionable=True)

with auto_tab:
    st.caption(
        "These were approved at a validation score of 1.00 with no person involved. Every check "
        "behind that score reads the document itself, so a duplicate, an unknown vendor and a "
        "well-formatted forgery all score the same."
    )
    document_rows(auto, actionable=False)

with outbox_tab:
    if notifications.empty:
        st.caption("Nothing queued.")
    else:
        st.caption(
            "Recorded, never sent. Nothing in this project is wired to Teams or Jira for these, "
            "so the queue is real and the sending is not. The text was written when it was true "
            "and nothing rewrites it, which is why the age matters."
        )
        st.dataframe(
            notifications[["created_at", "channel", "vendor_name", "invoice_number", "payload"]],
            hide_index=True, use_container_width=True,
            column_config={
                "created_at": "Queued",
                "channel": "Channel",
                "vendor_name": "Vendor",
                "invoice_number": "Document",
                "payload": "Message",
            },
        )
