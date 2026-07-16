import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
import io
import zipfile
import plotly.express as px

# Import the actual dynamic parser functions from your pipeline file
from pipeline.ocr_engine import extract_receipt_data, get_exchange_rate

# Import database management functions
from database.db_manager import (
    init_db, 
    fetch_all_expenses, 
    get_db_bytes, 
    restore_db_from_bytes,
    delete_expense,
    fetch_receipt_file,
    get_expense_trend,
    get_category_breakdown
)

# ==========================================================
# 1. PAGE SETUP (Must be the first Streamlit command)
# ==========================================================
st.set_page_config(
    page_title="Trip Expense Tracker & OCR Parser", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# Initialize session state tracking so we know when a restore happened
if "db_restored" not in st.session_state:
    st.session_state.db_restored = False

# ==========================================================
# 2. DATABASE RESTORE CHECK (INTERCEPTS BEFORE DATA FETCHING)
# ==========================================================
uploaded_db = st.sidebar.file_uploader(
    "📤 Restore Database (Upload Backup)",
    type=["db"],
    help="Upload your previously exported 'expenses_backup.db' file to restore your dashboard.",
    key="db_restorer"
)

if uploaded_db is not None and not st.session_state.db_restored:
    # Overwrite the empty file with your backup bytes
    restore_db_from_bytes(uploaded_db.getvalue())
    st.session_state.db_restored = True
    st.sidebar.success("✅ Database restored successfully!")
    
    # Immediately clear Streamlit's internal cache and rerun
    st.cache_data.clear()
    st.rerun()

# Reset the flag when the user removes/clears the uploader widget
if uploaded_db is None:
    st.session_state.db_restored = False

# ==========================================================
# 3. INITIALIZE AND LOAD ACTIVE DATA
# ==========================================================
init_db()
raw_df = fetch_all_expenses()

# Handle empty state gracefully
if raw_df is None or raw_df.empty:
    df = pd.DataFrame(columns=[
        'id', 'date', 'organization', 'amount', 'category', 'currency', 'original_amount', 'receipt_file'
    ])
else:
    df = raw_df.copy()
    
    # Force numeric datatypes for mathematical calculation
    df['amount'] = pd.to_numeric(df['amount'], errors='coerce').fillna(0.0)
    df['original_amount'] = pd.to_numeric(df['original_amount'], errors='coerce').fillna(0.0)
    
    # Ensure dates are uniform
    df['date'] = pd.to_datetime(df['date'])

# ==========================================================
# 4. MAIN USER INTERFACE & NAVIGATION REDESIGN
# ==========================================================
st.title("💼 Business Trip Claims Dashboard")
st.markdown("---")

# Setup our clean, full-width three tab workflow
tab1, tab2, tab3 = st.tabs([
    "📝 Add Expense Claim", 
    "📋 Tracked Claims & History", 
    "📊 Insights & Analytics"
])

# =====================================================================
# 📝 TAB 1: ADD NEW EXPENSE CLAIM
# =====================================================================
with tab1:
    st.header("➕ Add New Expense Claim")
    
    # Sub-tabs within entry panel for clean input mode routing
    tab_ocr, tab_manual = st.tabs(["📸 OCR Receipt Scan", "✏️ Manual Entry"])
    
    # --- Tab A: Upload & OCR Scan ---
    with tab_ocr:
        st.subheader("Scan Receipt with AI")
        uploaded_invoice = st.file_uploader(
            "Drop or select a receipt/invoice (PNG, JPG, PDF)", 
            type=["png", "jpg", "jpeg", "pdf"],
            key="invoice_uploader"
        )
        
        if uploaded_invoice is not None:
            st.info("Parsing invoice with AI OCR...")
            try:
                # 1. Read raw receipt bytes (needed for both OCR and Database BLOB)
                receipt_bytes = uploaded_invoice.read()
                
                # 2. Identify the correct MIME type
                mime_type = uploaded_invoice.type  # e.g., "image/png", "application/pdf"
                
                # Call extraction pipeline
                extracted_data = extract_receipt_data(receipt_bytes, mime_type=mime_type)
                
                # Extract the parsed dynamic values
                extracted_merchant = extracted_data.get('organization', 'Unknown Merchant')
                extracted_amount = float(extracted_data.get('total_amount', 0.0))
                extracted_orig_amount = float(extracted_data.get('original_amount', 0.0))
                extracted_currency = extracted_data.get('currency', 'SGD')
                extracted_date = extracted_data.get('date', datetime.today().strftime("%Y-%m-%d"))
                extracted_category = extracted_data.get('category', 'Miscellaneous')
                
                # Display the extracted results to the user for validation
                st.success("✅ Extraction Complete!")
                st.write(f"**Extracted Merchant:** {extracted_merchant}")
                st.write(f"**Extracted Total:** ${extracted_amount:.2f} {extracted_currency}")
                st.write(f"**Suggested Category:** {extracted_category}")
                st.write(f"**Invoice Date:** {extracted_date}")
                
                # 3. COMMIT TO DATABASE ON USER CONFIRMATION
                if st.button("Confirm & Save Extracted Expense", key="save_ocr"):
                    conn = sqlite3.connect("expenses.db")
                    cursor = conn.cursor()
                    cursor.execute('''
                        INSERT INTO expenses (date, organization, amount, category, currency, original_amount, receipt_file)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        extracted_date, 
                        extracted_merchant, 
                        extracted_amount, 
                        extracted_category, 
                        extracted_currency, 
                        extracted_orig_amount, 
                        receipt_bytes  # Saves the actual file as a BLOB
                    ))
                    conn.commit()
                    conn.close()
                    
                    st.toast("OCR Claim saved successfully!", icon="💾")
                    
                    st.cache_data.clear()
                    st.rerun()
                    
            except Exception as e:
                st.error(f"Failed to process receipt: {e}")

    # --- Tab B: Manual Input Form ---
    with tab_manual:
        st.subheader("Enter Details Manually")
        with st.form("manual_entry_form", clear_on_submit=True):
            entry_date = st.date_input("Date of Expense", value=datetime.today())
            merchant = st.text_input("Merchant/Organization Name", placeholder="e.g. Grab, Starbucks, FairPrice")
            col_amount, col_curr = st.columns([2, 1])
            with col_amount:
                amount = st.number_input("Amount", min_value=0.0, step=0.01, format="%.2f")
            with col_curr:
                currency = st.selectbox("Currency", [
                    "SGD", "USD", "EUR", "JPY", "CHF", 
                    "MYR", "IDR", "THB", "VND", "PHP", 
                    "AUD", "GBP", "HKD", "CNY", "KRW"
                ])
                
            category = st.selectbox("Category", [
                "Meals & Entertainment", 
                "Transport & Flights", 
                "Accommodation", 
                "Office Supplies", 
                "Miscellaneous"
            ])
            
            manual_attachment = st.file_uploader("Attach PDF/Image copy (Optional)", type=["png", "jpg", "jpeg", "pdf"], key="manual_file")
            submitted = st.form_submit_button("💾 Save Expense")
            
            if submitted:
                if not merchant:
                    st.error("Please enter a merchant or organization name.")
                elif amount <= 0.0:
                    st.error("Please enter a valid expense amount greater than 0.")
                else:
                    receipt_bytes = manual_attachment.read() if manual_attachment else None
                    
                    # Currency Conversion
                    rate = get_exchange_rate(from_currency=currency, to_currency="SGD")
                    converted_amount_sgd = round(amount * rate, 2)
                    
                    # DB Insertion
                    conn = sqlite3.connect("expenses.db")
                    cursor = conn.cursor()
                    cursor.execute('''
                        INSERT INTO expenses (date, organization, amount, category, currency, original_amount, receipt_file)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        entry_date.strftime("%Y-%m-%d"), 
                        merchant, 
                        converted_amount_sgd,  
                        category, 
                        currency,              
                        amount,                
                        receipt_bytes
                    ))
                    conn.commit()
                    conn.close()
                    
                    st.toast(f"Expense added! Converted at 1 {currency} = {rate:.4f} SGD", icon="✅")
                    st.cache_data.clear()
                    st.rerun()

# =====================================================================
# 📋 TAB 2: TRACKED CLAIMS & History (Data Records & Archival Systems)
# =====================================================================
with tab2:
    st.header("🗃️ Tracked Claims & Records")
    
    if df.empty:
        st.info("👋 Welcome! Your database is currently empty. Go to Tab 1 to populate records.")
    else:
        st.subheader("🔍 Filter by Timeline")
        
        min_date = df['date'].min().date()
        max_date = df['date'].max().date()
        
        if min_date == max_date:
            min_date = min_date - timedelta(days=7)
            max_date = max_date + timedelta(days=7)

        selected_date_range = st.date_input(
            "Select Date Range:",
            value=(min_date, max_date),
            min_value=min_date,
            max_value=max_date
        )
        
        if isinstance(selected_date_range, tuple) and len(selected_date_range) == 2:
            start_date, end_date = selected_date_range
            filtered_df = df[(df['date'].dt.date >= start_date) & (df['date'].dt.date <= end_date)].copy()
        else:
            filtered_df = df.copy()

        # Sort dynamically (Newest on top)
        filtered_df = filtered_df.sort_values(by="date", ascending=False)
        
        # Summary KPI Calculation Cards
        total_claims = len(filtered_df)
        total_spend_sgd = float(filtered_df['amount'].sum())
        
        kpi_col1, kpi_col2 = st.columns(2)
        kpi_col1.metric("Claims in Selected Range", total_claims)
        kpi_col2.metric("Filtered Total Spend (SGD)", f"${total_spend_sgd:,.2f}")
        
        st.markdown("---")
        st.subheader("Saved Records Log")
        st.dataframe(
            filtered_df.drop(columns=['receipt_file'], errors='ignore'), 
            use_container_width=True,
            column_config={
                "id": "ID",
                "date": st.column_config.DateColumn("Date", format="YYYY-MM-DD"),
                "organization": "Merchant",
                "amount": st.column_config.NumberColumn("Amount (SGD)", format="$%.2f"),
                "category": "Category",
                "currency": "Currency",
                "original_amount": st.column_config.NumberColumn("Orig. Amt", format="%.2f")
            }
        )
        
        # --- File and Report Storage Exporters ---
        st.markdown("---")
        with st.expander("💾 Export Data Options"):
            st.write("Choose your preferred export configuration below:")
            col_filtered_export, col_full_export = st.columns(2, gap="medium")
            
            with col_filtered_export:
                st.markdown("### 📈 Filtered Report")
                st.caption("Exports only the rows matching your active date filters as a CSV spreadsheet.")
                export_filtered_df = filtered_df.drop(columns=['receipt_file'], errors='ignore').copy()
                export_filtered_df['date'] = export_filtered_df['date'].dt.strftime('%Y-%m-%d')
                csv_filtered_data = export_filtered_df.to_csv(index=False).encode('utf-8')
                
                st.download_button(
                    label=f"📥 Download Filtered Rows ({len(export_filtered_df)} items)",
                    data=csv_filtered_data,
                    file_name=f"expense_report_{start_date}_to_{end_date}.csv",
                    mime="text/csv",
                    use_container_width=True,
                    key="btn_export_filtered"
                )
                
            with col_full_export:
                st.markdown("### 🗄️ Full Database Backup")
                st.caption("Exports your entire system history including hidden binary receipt image attachments (.db file).")
                full_db_bytes = get_db_bytes()
                if full_db_bytes:
                    st.download_button(
                        label=f"📥 Download Full Database ({len(df)} items)",
                        data=full_db_bytes,
                        file_name="expenses_backup.db",
                        mime="application/octet-stream",
                        use_container_width=True,
                        key="btn_export_full"
                    )

        with st.expander("📦 Bulk Download Filtered Receipts"):
            filtered_with_files = filtered_df[
                filtered_df['receipt_file'].notna() & (filtered_df['receipt_file'] != b'')
            ]

            if filtered_with_files.empty:
                st.info("No receipt attachments found within the selected date range.")
            else:
                st.write(f"📂 Found **{len(filtered_with_files)}** receipts matching your filters.")
                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                    for _, row in filtered_with_files.iterrows():
                        record_id = row['id']
                        record_date = pd.to_datetime(row['date']).strftime("%Y-%m-%d")
                        raw_merchant = str(row['organization'])
                        clean_merchant = "".join(x for x in raw_merchant if x.isalnum() or x in (' ', '_', '-')).strip()
                        clean_merchant = clean_merchant.replace(' ', '_')
                        archive_filename = f"{record_date}_ID{record_id}_{clean_merchant}.png"
                        zip_file.writestr(archive_filename, row['receipt_file'])

                zip_buffer.seek(0)
                st.download_button(
                    label=f"📥 Download All {len(filtered_with_files)} Receipts (.zip)",
                    data=zip_buffer.getvalue(),
                    file_name=f"receipts_export_{start_date}_to_{end_date}.zip",
                    mime="application/zip",
                    use_container_width=True
                )

        with st.expander("🗑️ Delete an Expense Claim"):
            delete_options = {
                row["id"]: f"ID {row['id']} - {row['organization']} (${row['amount']:.2f})"
                for _, row in filtered_df.iterrows()
            }
            selected_id = st.selectbox(
                "Select record to delete permanently:",
                options=list(delete_options.keys()),
                format_func=lambda x: delete_options[x],
                key="delete_selector"
            )
            confirm_delete = st.button("Confirm and Remove Record", type="primary", use_container_width=True)
            if confirm_delete:
                delete_expense(selected_id)
                st.toast("Record deleted successfully!", icon="🗑️")
                st.cache_data.clear()
                st.rerun()

# =====================================================================
# 📊 TAB 3: INSIGHTS & ANALYTICS
# =====================================================================
with tab3:
    st.header("📊 Financial Analytics & Insights")
    
    # Connect directly to your database pipeline aggregates
    try:
        # Connect to dynamic SQL analytical handlers
        conn_analytics = sqlite3.connect("expenses.db")
        df_trend = get_expense_trend(conn_analytics)
        df_category = get_category_breakdown(conn_analytics)
        conn_analytics.close()
    except Exception as e:
        # Safe fallback array if the database table is completely cold
        df_trend = pd.DataFrame(columns=['date', 'total_amount'])
        df_category = pd.DataFrame(columns=['category', 'total_amount'])
        
    if not df_trend.empty and not df_category.empty:
        # Convert date column to explicitly support smooth timeline rendering
        df_trend['date'] = pd.to_datetime(df_trend['date'])
        
        # Generate responsive dual-column metrics presentation
        chart_col1, chart_col2 = st.columns(2, gap="large")
        
        with chart_col1:
            st.subheader("📈 Spending Timeline Trend")
            fig_trend = px.area(
                df_trend, 
                x="date", 
                y="total_amount",
                labels={"date": "Timeline Date", "total_amount": "Total Expenditure ($)"},
                template="plotly_white"
            )
            fig_trend.update_traces(line_color="#1F77B4", line_width=2.5)
            fig_trend.update_layout(
                margin=dict(l=15, r=15, t=25, b=15), 
                hovermode="x unified",
                xaxis_title="Date Range",
                yaxis_title="Converted Amount (SGD)"
            )
            st.plotly_chart(fig_trend, use_container_width=True)
            
        with chart_col2:
            st.subheader("🍕 Allocation by Category")
            fig_pie = px.pie(
                df_category, 
                values="total_amount", 
                names="category",
                hole=0.4,
                color_discrete_sequence=px.colors.qualitative.Safe # Accessible, colorblind friendly palettes
            )
            fig_pie.update_layout(margin=dict(l=15, r=15, t=25, b=15))
            fig_pie.update_traces(textposition='inside', textinfo='percent+label')
            st.plotly_chart(fig_pie, use_container_width=True)
            
    else:
        st.info("💡 Keep log entries rolling! Visual trend timelines and allocations will display right here as soon as historical records are saved.")