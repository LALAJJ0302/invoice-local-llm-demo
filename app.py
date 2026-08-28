import os
import pandas as pd
import plotly.express as px
import streamlit as st

from storage import DEFAULT_DB_PATH, StorageManager, connect

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
    """Loads invoices from the normalised schema. Money is converted to dollars here, at the edge."""
    if not os.path.exists(db_file):
        return pd.DataFrame()

    conn = connect(db_file)
    query = """
        SELECT
            invoice_id AS id,
            run_id,
            file_name,
            validation_status,
            approval_status,
            reviewed_at,
            validation_score,
            total_source,
            invoice_number,
            vendor_name,
            invoice_date,
            total_cents / 100.0 AS total_amount,
            currency,
            archive_path,
            email_id,
            processed_at AS system_processed_at
        FROM invoices
        ORDER BY invoice_id DESC
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
    StorageManager(DB_PATH).resolve_tasks(
        record_id, state="Done" if decision == "Approved" else "Cancelled")


def load_open_tasks() -> pd.DataFrame:
    """The work queue. Written by the pipeline, cleared by a human decision."""
    rows = StorageManager(DB_PATH).open_tasks()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([{
        "Task": r["task_id"],
        "Type": r["task_type"],
        "Invoice": r["invoice_id"],
        "File": r["file_name"],
        "Vendor": r["vendor_name"],
        "Amount": None if r["total_cents"] is None else r["total_cents"] / 100.0,
        "Data Quality": r["validation_status"],
        "Approval": r["approval_status"],
        "Why": r["reason"],
        "Opened": r["created_at"],
    } for r in rows])

# =====================================================================
# 2. Main Dashboard UI
# =====================================================================
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
    "Work the pipeline handed to a person. `Review` means the extraction could not be trusted. "
    "`Approve` means it was read cleanly but the money still needs a signature. A task leaves "
    "the queue when someone approves or rejects the invoice below."
)

if open_tasks_df.empty:
    st.success("No open tasks. Every processed document has been decided.")
else:
    st.dataframe(
        open_tasks_df.style.format({"Amount": "{:,.2f}"}),
        use_container_width=True,
        hide_index=True,
    )

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
    "validation_status",      # the gate's verdict, not the model's and not a person's
    "validation_score",
    "approval_status",        # human decision
    "total_source",
    "vendor_name",
    "invoice_number",
    "invoice_date",           # Document Date
    "total_amount",
    "currency",
    "system_processed_at"     # Ingestion Execution Time
]].copy()

table_display.columns = [
    "ID", "File Name", "Data Quality", "Validation Score", "Approval", "Total Source",
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
selected_id = st.selectbox(
    "Select an ID to inspect or manually approve:",
    options=filtered_df["id"].tolist() if not filtered_df.empty else []
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
        st.markdown(f"**Total Source:** `{row['total_source']}`")

        if row["total_source"] == "fallback":
            st.info("This total was derived from the line items, not read from the document.")

        st.divider()
        st.markdown(f"**Data Quality (pipeline):** `{row['validation_status']}` "
                    f"at score `{row['validation_score']:.2f}`")
        st.markdown(f"**Approval (human):** `{row['approval_status']}`")
        if row["reviewed_at"]:
            st.markdown(f"**Reviewed At:** `{row['reviewed_at']}`")

        if row["validation_status"] == "NeedsReview":
            st.warning("⚠️ The pipeline was not confident about this document. Check it before approving.")

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