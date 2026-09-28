import os
import pandas as pd
import plotly.express as px
import streamlit as st

import auth
import task_dispatch
from storage import APPROVAL_TASK_TYPES, DEFAULT_DB_PATH, StorageManager, connect

# =====================================================================
# 1. Page Configuration
# =====================================================================
st.set_page_config(
    page_title="AI Workflow Automation Platform",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
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
            u.display_name AS reviewer_name,
            i.validation_score,
            i.total_source,
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

def set_action_item_done(action_item_id: int, is_done: bool):
    """A person checking off an action item. Does not touch validation_score or
    approval_status -- an action item is a claim about the document, not a workflow gate."""
    StorageManager(DB_PATH).set_action_item_done(action_item_id, is_done)

def assign_task(task_id: int, assignee: str):
    """A person claiming (or clearing, with an empty string) a task from the queue."""
    store = StorageManager(DB_PATH)
    store.assign_task(task_id, assignee or None)
    task_dispatch.sync_jira_assignee(store, task_id, assignee or None)

def record_decision(record_id: int, decision: str):
    """Records a human decision about an invoice.

    Writes approval_status, reviewed_at and reviewed_by (the signed-in user). It deliberately does NOT touch:

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
    reviewer_id = int(st.session_state["user_id"])

    # The work the task stood for is finished, so it leaves the queue. A rejection cancels
    # rather than completes: the document was not accepted, so nothing downstream should
    # treat it as processed.
    store = StorageManager(DB_PATH)
    store.record_decision(record_id, decision, reviewer_id)
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
                reviewer_user_id=reviewer_id,
            )


def load_open_tasks() -> pd.DataFrame:
    """The approval queue: Review and Approve tasks still waiting for a person.

    Payment/File follow-ups are created after approval (including auto-approval) and
    dispatched to Jira; they are not shown here.
    """
    rows = StorageManager(DB_PATH).open_tasks(task_types=APPROVAL_TASK_TYPES)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([{
        "Task": r["task_id"],
        "Type": r["task_type"],
        "Doc Type": r["document_type"],
        "Invoice": r["invoice_id"],
        "File": r["file_name"],
        "Vendor": r["vendor_name"],
        "Amount": None if r["total_cents"] is None else r["total_cents"] / 100.0,
        "Data Quality": r["validation_status"],
        "Approval": r["approval_status"],
        "Assignee": r["assignee"] or "",
        "Jira": r["external_ref"] or "",
        "Why": r["reason"],
        "Opened": r["created_at"],
    } for r in rows])

# =====================================================================
# 2. Main Dashboard UI
# =====================================================================
def require_password_change(store: StorageManager) -> None:
    """Stops the page until a first-time user replaces the temporary password."""
    st.title("⚡ Enterprise AI Workflow Automation Dashboard")
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
    """Stops the page until a configured user signs in, then until they set a password."""
    store = StorageManager(DB_PATH)
    auth.ensure_default_users(store)
    if st.session_state.get("user_id"):
        if st.session_state.get("must_change_password"):
            require_password_change(store)
        return

    st.title("⚡ Enterprise AI Workflow Automation Dashboard")
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
st.sidebar.caption(f"Signed in as **{st.session_state['display_name']}**")
if st.sidebar.button("Log out"):
    st.session_state.clear()
    st.rerun()

st.title("⚡ Enterprise AI Workflow Automation Dashboard")
st.caption("End-to-end Document Ingestion, Ollama Local Extraction & Real-time Analytics")

df = load_data()

if df.empty:
    st.warning("⚠️ No records found in SQLite database. Run `email_listener.py` or `main.py` first.")
    st.stop()

# =====================================================================
# 3. KPI Metrics
# =====================================================================
total_docs = len(df)
validated_docs = len(df[df["validation_status"] == "Validated"])
needs_review_docs = len(df[df["validation_status"] == "NeedsReview"])
auto_rate = (validated_docs / total_docs) * 100 if total_docs > 0 else 0
total_spend = df[df["total_amount"] > 0]["total_amount"].sum()
avg_validation = df["validation_score"].mean()
derived_totals = int((df["total_source"] == "fallback").sum())
approved_docs = len(df[df["approval_status"] == "Approved"])
pending_docs = len(df[df["approval_status"] == "Pending"])
open_tasks_df = load_open_tasks()

col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    st.metric(label="📥 Total Documents", value=total_docs)
with col2:
    st.metric(label="✅ Automation Pass Rate", value=f"{auto_rate:.1f}%", delta=f"{validated_docs} Validated")
with col3:
    st.metric(label="💰 Total Tracked Spend", value=f"${total_spend:,.2f}")
with col4:
    st.metric(label="🎯 Avg Validation Score", value=f"{avg_validation:.2f}")
with col5:
    st.metric(label="📌 Open Tasks", value=len(open_tasks_df),
              delta=f"{approved_docs} approved, {pending_docs} pending", delta_color="off")

st.caption(
    "**Data Quality** is what the pipeline judged. **Approval** is what a person decided. "
    "They are independent: a document can be `NeedsReview` and `Approved` at once, meaning "
    "the extractor was not confident and a reviewer accepted it anyway."
)

if derived_totals:
    st.caption(
        f"⚠️ {derived_totals} of {total_docs} totals were derived from line items rather than "
        "extracted by the model. They are marked `fallback` in the Source column."
    )

st.divider()

# =====================================================================
# 4. Analytics & Visualizations
# =====================================================================
chart_col1, chart_col2 = st.columns(2)

with chart_col1:
    st.subheader("📊 Validation Status Breakdown")
    status_summary = df["validation_status"].value_counts().reset_index()
    status_summary.columns = ["Status", "Count"]
    fig_status = px.pie(
        status_summary, 
        names="Status", 
        values="Count", 
        color="Status",
        color_discrete_map={"Validated": "#10B981", "NeedsReview": "#F59E0B"},
        hole=0.45
    )
    fig_status.update_layout(margin=dict(t=10, b=10, l=10, r=10))
    st.plotly_chart(fig_status, use_container_width=True)

with chart_col2:
    st.subheader("🏢 Spend by Vendor")
    vendor_df = df[df["total_amount"] > 0].groupby("vendor_name")["total_amount"].sum().reset_index()
    vendor_df = vendor_df.sort_values(by="total_amount", ascending=True)
    if not vendor_df.empty:
        fig_vendor = px.bar(
            vendor_df,
            x="total_amount",
            y="vendor_name",
            orientation="h",
            color="total_amount",
            color_continuous_scale="Blues"
        )
        fig_vendor.update_layout(margin=dict(t=10, b=10, l=10, r=10), showlegend=False)
        st.plotly_chart(fig_vendor, use_container_width=True)
    else:
        st.info("No spend data available yet.")

st.divider()

# =====================================================================
# 4b. Task Queue
# =====================================================================
st.subheader("📌 Task Queue")
st.caption(
    "Work still waiting for a person. `Review` means the extraction could not be trusted. "
    "`Approve` means it was read cleanly but the money still needs a signature. A score of "
    "1.00 with a verified amount is auto-approved and skips this queue; its Payment or File "
    "follow-up goes to Jira. A task leaves the queue when someone approves or rejects the "
    "invoice below."
)

if open_tasks_df.empty:
    st.success("No open tasks. Every processed document has been decided.")
else:
    st.caption("Edit the **Assignee** column and press Enter to claim or reassign a task.")
    edited_tasks_df = st.data_editor(
        open_tasks_df,
        use_container_width=True,
        hide_index=True,
        disabled=[c for c in open_tasks_df.columns if c not in ("Assignee",)],
        column_config={
            "Amount": st.column_config.NumberColumn(format="%.2f"),
        },
        key="task_queue_editor",
    )
    # Only the rows a person actually touched are written back, so an unrelated edit
    # elsewhere in the grid cannot silently reassign every other task.
    changed = edited_tasks_df[edited_tasks_df["Assignee"] != open_tasks_df["Assignee"]]
    if not changed.empty:
        for _, row in changed.iterrows():
            assign_task(int(row["Task"]), row["Assignee"])
        st.rerun()

    st.caption("Open a task in the Detail Inspector below:")
    inspect_cols = st.columns(min(len(open_tasks_df), 4))
    for index, task_row in enumerate(open_tasks_df.itertuples(index=False)):
        with inspect_cols[index % len(inspect_cols)]:
            label = f"Invoice {task_row.Invoice} ({task_row.Type})"
            if st.button(label, key=f"inspect_task_{task_row.Task}", use_container_width=True):
                st.session_state["inspect_invoice_id"] = int(task_row.Invoice)
                st.rerun()

st.divider()

# =====================================================================
# 5. Explorer Table (Differentiating Dates clearly)
# =====================================================================
st.subheader("📋 Invoices & Receipts Explorer")

st.sidebar.header("Filters")
status_filter = st.sidebar.multiselect(
    "Filter Status:", 
    options=["Validated", "NeedsReview"], 
    default=["Validated", "NeedsReview"]
)
search_text = st.sidebar.text_input("Search Vendor / Invoice # / File:")

filtered_df = df[df["validation_status"].isin(status_filter)]
if search_text:
    filtered_df = filtered_df[
        filtered_df["vendor_name"].str.contains(search_text, case=False, na=False) |
        filtered_df["invoice_number"].str.contains(search_text, case=False, na=False) |
        filtered_df["file_name"].str.contains(search_text, case=False, na=False)
    ]

# Display Table with renamed, unambiguous columns
table_display = filtered_df[[
    "id",
    "file_name",
    "document_type",
    "category",                # email_ai.py's business category, distinct from Doc Type
    "validation_status",      # the gate's verdict, not the model's and not a person's
    "validation_score",
    "approval_status",        # human decision
    "total_source",
    "reconciliation",
    "vendor_name",
    "invoice_number",
    "invoice_date",           # Document Date
    "total_amount",
    "currency",
    "system_processed_at"     # Ingestion Execution Time
]].copy()

table_display.columns = [
    "ID", "File Name", "Doc Type", "Category", "Data Quality", "Validation Score", "Approval",
    "Total Source", "Reconciliation",
    "Vendor Name", "Invoice #", "Invoice Date (Doc)",
    "Total Amount", "Currency", "Processed At (System)"
]

st.dataframe(
    table_display.style.format({
        "Validation Score": "{:.2f}",
        "Total Amount": "{:,.2f}"
    }),
    use_container_width=True,
    hide_index=True
)

# =====================================================================
# 6. Detail Inspector & Human-in-the-loop Approval
# =====================================================================
st.subheader("🔍 Document Detail Inspector")
_id_options = filtered_df["id"].tolist() if not filtered_df.empty else []
_default_index = 0
_pref = st.session_state.get("inspect_invoice_id")
if _pref is not None and _pref in _id_options:
    _default_index = _id_options.index(_pref)
elif not open_tasks_df.empty:
    for _oid in open_tasks_df["Invoice"].tolist():
        if _oid in _id_options:
            _default_index = _id_options.index(_oid)
            break
selected_id = st.selectbox(
    "Select an ID to inspect or manually approve:",
    options=_id_options,
    index=_default_index if _id_options else None,
)

if selected_id:
    row = df[df["id"] == selected_id].iloc[0]
    col_left, col_right = st.columns([1, 1])

    with col_left:
        st.markdown(f"**File Name:** `{row['file_name']}`")
        st.markdown(f"**Vendor:** `{row['vendor_name']}`")
        st.markdown(f"**Invoice #:** `{row['invoice_number']}`")
        st.markdown(f"**Invoice Date (on Document):** `{row['invoice_date']}`")
        st.markdown(f"**System Ingestion Time:** `{row['system_processed_at']}`")
        total = row["total_amount"]
        total_text = "not extracted" if pd.isna(total) else f"{row['currency']} {total:,.2f}"
        st.markdown(f"**Total Amount:** `{total_text}`")
        st.markdown(f"**Document Type:** `{row['document_type']}`")
        category_text = row["category"] if pd.notna(row["category"]) else "not categorised"
        st.markdown(f"**Category:** `{category_text}`")
        st.markdown(f"**Total Source:** `{row['total_source']}`")
        st.markdown(f"**Reconciliation:** `{row['reconciliation']}`")

        if row["reconciliation"] == "short":
            st.error("The stated total is **less** than the line items add up to. Tax and "
                     "shipping can only increase a total, so one of the two numbers is wrong.")
        elif row["reconciliation"] == "plausible":
            st.info("The total is higher than the line items, which tax or shipping would "
                    "explain. Normal on a real invoice.")
        elif row["reconciliation"] == "unknown":
            st.warning("No line items to check the total against.")

        if row["total_source"] == "fallback":
            st.info("This total was derived from the line items, not read from the document.")

        st.divider()
        st.markdown("**Summary:**")
        if pd.notna(row["summary"]) and row["summary"]:
            st.markdown(f"> {row['summary']}")
        else:
            st.caption("Not summarised (email_ai.py's document-intelligence pass did not "
                       "run or did not return one; the invoice itself is unaffected).")

        with st.expander("📧 Original Source"):
            if pd.notna(row["email_id"]):
                st.markdown(f"**From:** `{row['email_sender']}`")
                st.markdown(f"**Subject:** `{row['email_subject']}`")
                st.markdown(f"**Received:** `{row['email_received_at']}`")
            else:
                st.caption("Not delivered by email: this file was dropped straight into "
                           "inbox/, which is the documented way to test without Gmail.")
            if pd.notna(row["archive_path"]) and row["archive_path"]:
                st.markdown(f"**Archived file:** `{row['archive_path']}`")
                if os.path.exists(row["archive_path"]):
                    with open(row["archive_path"], "rb") as f:
                        st.download_button(
                            "Download original file", data=f.read(),
                            file_name=row["file_name"], key=f"download_{selected_id}")
                else:
                    st.caption("The archived file is no longer on disk.")

        st.divider()
        st.markdown(f"**Data Quality (pipeline):** `{row['validation_status']}` "
                    f"at score `{row['validation_score']:.2f}`")
        st.markdown(f"**Approval (human):** `{row['approval_status']}`")
        if row["reviewed_at"]:
            reviewer = row["reviewer_name"] or "unknown"
            st.markdown(f"**Reviewed By:** `{reviewer}`")
            st.markdown(f"**Reviewed At:** `{row['reviewed_at']}`")
        elif row["approval_status"] == "Approved":
            st.caption("✅ Approved automatically — validation score was 1.00 with a verified "
                       "amount; no human review was required.")

        if row["validation_status"] == "NeedsReview":
            if row["approval_status"] == "Approved" and row["reviewed_at"]:
                st.info("This document was **approved by a reviewer** while data quality was "
                        "NeedsReview. Re-running `main.py` does not undo that decision. "
                        "Use **Reject** if you need to reopen review.")
            else:
                st.warning("⚠️ The pipeline was not confident about this document. "
                           "Check it before approving.")

        decision_col1, decision_col2 = st.columns(2)
        with decision_col1:
            if st.button("✅ Approve", use_container_width=True,
                         disabled=row["approval_status"] == "Approved"):
                record_decision(selected_id, "Approved")
                st.rerun()
        with decision_col2:
            if st.button("❌ Reject", use_container_width=True,
                         disabled=row["approval_status"] == "Rejected"):
                record_decision(selected_id, "Rejected")
                st.rerun()

        st.caption("Approving records a human decision. It does not change the pipeline's "
                   "Data Quality verdict or the validation score, which stay as evidence.")

    with col_right:
        items_df = load_line_items(int(selected_id))
        if items_df.empty:
            st.info("No line items extracted.")
        else:
            st.markdown("**Extracted Line Items:**")
            st.dataframe(
                items_df.style.format({
                    "Qty": "{:g}",
                    "Unit Price": "{:,.2f}",
                    "Line Total": "{:,.2f}",
                }),
                use_container_width=True,
                hide_index=True,
            )
            st.caption("Rows marked as a summary row are excluded from any line item total.")

    st.divider()
    st.markdown("**✅ Action Items**")
    st.caption(
        "Follow-up actions email_ai.py found in the document text, each with the exact "
        "quote it was read from -- check the quote before trusting the action. Checking a "
        "box here is a personal to-do; it does not change Data Quality or Approval above."
    )
    action_items_df = load_action_items(int(selected_id))
    if action_items_df.empty:
        st.info("No action items extracted.")
    else:
        for _, item in action_items_df.iterrows():
            label = item["Action"]
            if pd.notna(item["Owner"]) and item["Owner"]:
                label += f" — owner: {item['Owner']}"
            if pd.notna(item["Deadline"]) and item["Deadline"]:
                label += f" ({item['Deadline']})"
            checked = st.checkbox(
                label, value=bool(item["is_done"]),
                key=f"action_item_{item['action_item_id']}")
            if checked != bool(item["is_done"]):
                set_action_item_done(int(item["action_item_id"]), checked)
                st.rerun()
            if pd.notna(item["Evidence"]) and item["Evidence"]:
                st.caption(f"↳ “{item['Evidence']}”")