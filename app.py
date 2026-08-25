import json
import os
import sqlite3
import pandas as pd
import plotly.express as px
import streamlit as st

# =====================================================================
# 1. Page Configuration
# =====================================================================
st.set_page_config(
    page_title="AI Workflow Automation Platform",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

DB_PATH = "workflow_platform.db"

def load_data(db_file: str = DB_PATH) -> pd.DataFrame:
    """Loads records from SQLite."""
    if not os.path.exists(db_file):
        return pd.DataFrame()
    
    conn = sqlite3.connect(db_file)
    query = """
        SELECT 
            id,
            file_name,
            status,
            confidence_score,
            invoice_number,
            vendor_name,
            date AS invoice_date,
            total_amount,
            currency,
            archive_path,
            raw_json,
            processed_at AS system_processed_at
        FROM workflow_records
        ORDER BY id DESC
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df

def update_status_to_validated(record_id: int):
    """Updates record status to Validated manually."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE workflow_records SET status = 'Validated', confidence_score = 1.0 WHERE id = ?", (record_id,))
    conn.commit()
    conn.close()

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
validated_docs = len(df[df["status"] == "Validated"])
needs_review_docs = len(df[df["status"] == "NeedsReview"])
auto_rate = (validated_docs / total_docs) * 100 if total_docs > 0 else 0
total_spend = df[df["total_amount"] > 0]["total_amount"].sum()
avg_confidence = df["confidence_score"].mean()

col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric(label="📥 Total Documents", value=total_docs)
with col2:
    st.metric(label="✅ Automation Pass Rate", value=f"{auto_rate:.1f}%", delta=f"{validated_docs} Validated")
with col3:
    st.metric(label="💰 Total Tracked Spend", value=f"${total_spend:,.2f}")
with col4:
    st.metric(label="🎯 Avg Confidence Score", value=f"{avg_confidence:.2f}")

st.divider()

# =====================================================================
# 4. Analytics & Visualizations
# =====================================================================
chart_col1, chart_col2 = st.columns(2)

with chart_col1:
    st.subheader("📊 Validation Status Breakdown")
    status_summary = df["status"].value_counts().reset_index()
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

filtered_df = df[df["status"].isin(status_filter)]
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
    "status", 
    "confidence_score", 
    "vendor_name", 
    "invoice_number", 
    "invoice_date",           # Document Date
    "total_amount", 
    "currency", 
    "system_processed_at"     # Ingestion Execution Time
]].copy()

table_display.columns = [
    "ID", "File Name", "Status", "Confidence", 
    "Vendor Name", "Invoice #", "Invoice Date (Doc)", 
    "Total Amount", "Currency", "Processed At (System)"
]

st.dataframe(
    table_display.style.format({
        "Confidence": "{:.2f}",
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
    options=filtered_df["ID"].tolist() if not filtered_df.empty else []
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
        st.markdown(f"**Total Amount:** `{row['currency']} {row['total_amount']:,.2f}`")

        if row["status"] == "NeedsReview":
            st.warning("⚠️ This document requires human review.")
            if st.button("✅ Approve Document (Mark as Validated)"):
                update_status_to_validated(selected_id)
                st.success(f"Invoice ID {selected_id} marked as Validated!")
                st.rerun()

    with col_right:
        try:
            raw_data = json.loads(row["raw_json"])
            items = raw_data.get("items", [])
            if items:
                st.markdown("**Extracted Line Items:**")
                st.table(pd.DataFrame(items))
            else:
                st.info("No line items extracted.")
        except Exception:
            st.info("No additional line item details.")