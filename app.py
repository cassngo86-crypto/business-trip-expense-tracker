import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from database.db_manager import init_db, insert_expense, fetch_all_expenses, delete_expense_record
from pipeline.ocr_engine import extract_receipt_data, get_exchange_rate
import io
import zipfile

init_db()
st.set_page_config(page_title="Corporate Expense Claims System", layout="wide")
st.title("💼 Business Trip Claims & Expense Analytics")

# --- SIDEBAR: DATA INPUT ---
st.sidebar.header("Add New Expense")

with st.sidebar.expander("Manual Input Form"):
    with st.form("manual_form", clear_on_submit=True):
        date = st.date_input("Date")
        org = st.text_input("Merchant/Vendor")
        orig_amt = st.number_input("Amount", min_value=0.0, step=0.01)
        curr = st.selectbox("Currency", ["SGD", "USD", "EUR", "MYR", "JPY"])
        category = st.selectbox("Category", ["Dining", "Grocery", "Transport", "Utilities", "Entertainment", "Healthcare", "Miscellaneous"])
        
        if st.form_submit_button("Save Expense"):
            rate = get_exchange_rate(curr, "SGD")
            converted_amt = round(orig_amt * rate, 2)
            insert_expense(str(date), org, converted_amt, category, curr, orig_amt, receipt_file=None)
            st.success("Saved entry!")
            st.rerun()

with st.sidebar.expander("Upload Receipt"):
    uploaded_file = st.file_uploader("Choose receipt image...", type=["jpg", "jpeg", "png"])
    if uploaded_file is not None:
        st.image(uploaded_file, width=250)
        if st.button("Process Receipt"):
            with st.spinner("Analyzing data..."):
                try:
                    file_bytes = uploaded_file.read()
                    data = extract_receipt_data(file_bytes, mime_type=uploaded_file.type)
                    insert_expense(
                        data['date'], data['organization'], data['total_amount'], 
                        data['category'], data['currency'], data['original_amount'], 
                        receipt_file=file_bytes
                    )
                    st.success(f"Logged: {data['organization']} ({data['currency']} {data['original_amount']})")
                    st.rerun()
                except Exception as e:
                    st.error(f"Processing error: {e}")

# --- MAIN DASHBOARD VIEW ---
df = fetch_all_expenses()

if df.empty:
    st.info("No expense data found. Add expenses or upload a receipt to generate your trip reports!")
else:
    # --- DATA FILTERING ENGINE (TRIP SPECIFIC) ---
    st.markdown("### 🔍 Trip Claims Filter Suite")
    
    # Establish default range bounds matching actual record parameters safely
    min_date = df['date'].min().date()
    max_date = df['date'].max().date()
    
    f_col1, f_col2 = st.columns(2)
    with f_col1:
        start_date = st.date_input("Trip Start Date", min_date)
    with f_col2:
        end_date = st.date_input("Trip End Date", max_date)
        
    # Apply date filters safely to dataframe copies
    filtered_df = df[(df['date'].dt.date >= start_date) & (df['date'].dt.date <= end_date)].copy()
    
    if filtered_df.empty:
        st.warning("No records found within the selected date range filter. Adjust your dates above.")
    else:
        # High Level Metrics Row for filtered data
        m1, m2 = st.columns(2)
        m1.metric("Trip Total Spending (SGD)", f"${filtered_df['amount'].sum():,.2f}")
        m2.metric("Filtered Claims Count", len(filtered_df))
        
        st.markdown("---")
        
        # --- HISTORICAL SPENDING TRENDS ---
        st.subheader("📉 Historical Spending Trend")
        trend_view = st.radio("Group Trend Line By:", ["Weekly", "Monthly"], horizontal=True)
        
        trend_df = filtered_df.copy()
        if trend_view == "Weekly":
            trend_df['Period'] = trend_df['date'].dt.to_period('W').astype(str)
        else:
            trend_df['Period'] = trend_df['date'].dt.to_period('M').astype(str)
            
        timeline_data = trend_df.groupby('Period')['amount'].sum().reset_index().sort_values(by='Period')
        
        fig_trend = go.Figure()
        fig_trend.add_trace(go.Bar(x=timeline_data['Period'], y=timeline_data['amount'], name="Total Spending", marker_color='#3498db', opacity=0.7))
        fig_trend.add_trace(go.Scatter(x=timeline_data['Period'], y=timeline_data['amount'], name="Trend Line", line=dict(color='#e74c3c', width=3, shape='spline'), mode='lines+markers'))
        fig_trend.update_layout(xaxis_title="Time Period", yaxis_title="Amount Spent (SGD)", hovermode="x unified", margin=dict(t=10, b=10, l=10, r=10))
        st.plotly_chart(fig_trend, use_container_width=True)
        
        st.markdown("---")
        
        # Split Data presentation layout
        col3, col4 = st.columns(2)
        with col3:
            st.subheader("Spending Category Breakdown")
            cat_df = filtered_df.groupby('category')['amount'].sum().reset_index()
            fig_pie = px.pie(cat_df, values='amount', names='category', hole=0.4, color_discrete_sequence=px.colors.qualitative.Safe)
            st.plotly_chart(fig_pie, use_container_width=True)
            
        with col4:
            st.subheader("Filtered Claims Summary Table")
            display_df = filtered_df[['id', 'date', 'organization', 'original_amount', 'currency', 'amount', 'category']].copy()
            display_df['date'] = display_df['date'].dt.strftime('%Y-%m-%d')
            display_df.columns = ['ID', 'Date', 'Merchant', 'Orig Amt', 'Curr', 'SGD Total', 'Category']
            
            st.dataframe(
                display_df.sort_values(by='ID', ascending=False), 
                use_container_width=True, 
                hide_index=True,
                column_config={
                    "ID": st.column_config.NumberColumn(width="small"),
                    "Orig Amt": st.column_config.NumberColumn(format="$%.2f"),
                    "SGD Total": st.column_config.NumberColumn(format="$%.2f"),
                }
            )

        # --- NEW: TRIP DOWNLOAD & CLAIMS EXPORT SUITE ---
        st.markdown("---")
        st.subheader("📥 Export Trip Business Report & Claims")
        
        exp_col1, exp_col2 = st.columns(2)
        
        with exp_col1:
            st.write("📄 **Download Claims Summary (CSV)**")
            st.caption("Generates a structured ledger sheet perfect for audit review attachments.")
            
            # Prepare clean report output buffer without administrative column data
            report_df = display_df.copy()
            csv_data = report_df.to_csv(index=False).encode('utf-8')
            
            st.download_button(
                label="📥 Download CSV Report",
                data=csv_data,
                file_name=f"Business_Trip_Report_{start_date}_to_{end_date}.csv",
                mime="text/csv"
            )
            
        with exp_col2:
            st.write("📦 **Download All Supporting Invoices (ZIP)**")
            st.caption("Packages all matching receipt photos into a single compressed archive file.")
            
            # Filter rows within current selection that have image blobs attached
            zip_target_df = filtered_df[filtered_df['receipt_file'].notna()]
            
            if zip_target_df.empty:
                st.info("No scanned invoice image uploads exist within this trip date range.")
            else:
                # Build an in-memory ZIP package using io streams dynamically
                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                    for _, row in zip_target_df.iterrows():
                        record_id = row['id']
                        merchant_name = str(row['organization']).replace(" ", "_").strip()
                        raw_bytes = row['receipt_file']
                        
                        # Set default filename extensions safely
                        filename = f"Receipt_ID_{record_id}_{merchant_name}.jpg"
                        zip_file.writestr(filename, raw_bytes)
                
                # Rewind pointer to serve stream data
                zip_buffer.seek(0)
                
                st.download_button(
                    label="📥 Download Receipts .ZIP",
                    data=zip_buffer.getvalue(),
                    file_name=f"Trip_Receipts_{start_date}_to_{end_date}.zip",
                    mime="application/zip"
                )

        # --- VIEW ATTACHED RECEIPT UTILITY ---
        st.markdown("---")
        st.subheader("🖼️ View Stored Invoice Attachment")
        blob_df = filtered_df[filtered_df['receipt_file'].notna()]
        
        if blob_df.empty:
            st.info("No records have attached receipt files yet.")
        else:
            view_target = st.selectbox(
                "Select Record to pull up receipt file:",
                options=blob_df['id'].sort_values(ascending=False).tolist(),
                format_func=lambda x: f"ID {x} - {blob_df[blob_df['id']==x]['organization'].values[0]} (${blob_df[blob_df['id']==x]['amount'].values[0]:.2f})"
            )
            raw_blob = blob_df[blob_df['id'] == view_target]['receipt_file'].values[0]
            with st.expander("👁️ Click to Expand and View Attached Receipt Image", expanded=True):
                st.image(raw_blob, width=450)
            
        # --- DELETION UTILITY FOOTER ---
        st.markdown("---")
        st.subheader("🗑️ Delete/Purge Incorrect Records")
        delete_target = st.selectbox(
            "Select Record ID to delete:", 
            options=filtered_df['id'].sort_values(ascending=False).tolist(),
            format_func=lambda x: f"ID {x} - {filtered_df[filtered_df['id']==x]['organization'].values[0]} (${filtered_df[filtered_df['id']==x]['amount'].values[0]:.2f})"
        )
        if st.button("Confirm Permanently Delete Record", type="primary"):
            delete_expense_record(delete_target)
            st.warning(f"Record ID {delete_target} deleted successfully.")
            st.rerun()