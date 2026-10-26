import os
import shutil
import pandas as pd
import streamlit as st
from datetime import date
from decimal import Decimal

from src.db import (
    init_db,
    get_connection,
    save_invoice,
    verify_invoice,
    log_execution,
)
from src.models import ExtractedInvoice
from src.extractor import build_validated_record
from src.graph import run_invoice_agent
from src.file_manager import (
    init_triage_folders,
    triage_invoice_file,
    INBOX_DIR,
    SUCCESS_DIR,
    HUMAN_REVIEW_DIR,
    FAILED_DIR,
)
from src.gmail import fetch_invoices_from_gmail

# Page configuration
st.set_page_config(
    page_title="Invoice Processing Agent",
    layout="wide",
)

# Ensure database and folders exist
init_db()
init_triage_folders()


def get_invoices_df() -> pd.DataFrame:
    # Loads all processed invoices from SQLite into a DataFrame
    with get_connection() as conn:
        df = pd.read_sql_query(
            "SELECT id, vendor_name, invoice_number, invoice_date, due_date, amount, currency, source_filename, created_at FROM invoices ORDER BY id DESC",
            conn,
        )
    return df


def get_logs_df() -> pd.DataFrame:
    # Loads the latest 200 execution logs from SQLite
    with get_connection() as conn:
        df = pd.read_sql_query(
            "SELECT id, timestamp, source_filename, step, status, details FROM execution_logs ORDER BY id DESC LIMIT 200",
            conn,
        )
    return df


def get_pending_review_files() -> list[str]:
    # Returns list of files currently in data/processed/human_review/
    if not os.path.exists(HUMAN_REVIEW_DIR):
        return []
    return [
        f for f in os.listdir(HUMAN_REVIEW_DIR)
        if f.lower().endswith((".pdf", ".png", ".jpg", ".jpeg"))
    ]


# --- Sidebar: Agent Controls ---
with st.sidebar:
    st.title("Agent Controls")
    st.caption("Autonomous Invoice Processing System")
    st.divider()

    if st.button("Run Ingestion & Agent", use_container_width=True, type="primary"):
        with st.status("Processing invoices...", expanded=True) as status_box:
            # 1. Fetch from Gmail
            st.write("Checking Gmail for invoice attachments...")
            try:
                new_files = fetch_invoices_from_gmail()
                st.write(f"Gmail sync complete: {len(new_files)} new files downloaded.")
            except Exception as e:
                st.warning(f"Gmail sync skipped: {e}")

            # 2. Check inbox directory
            inbox_files = [
                os.path.join(INBOX_DIR, f)
                for f in os.listdir(INBOX_DIR)
                if f.lower().endswith((".pdf", ".png", ".jpg", ".jpeg"))
            ] if os.path.exists(INBOX_DIR) else []

            if not inbox_files:
                st.info("No invoice files found in data/inbox/ to process.")
                status_box.update(label="No invoices in inbox", state="complete")
            else:
                progress_bar = st.progress(0)
                for idx, file_path in enumerate(inbox_files):
                    fname = os.path.basename(file_path)
                    st.write(f"Processing ({idx + 1}/{len(inbox_files)}): {fname}...")

                    # Run LangGraph Agent
                    result = run_invoice_agent(file_path)
                    status = result.get("status", "UNKNOWN")

                    # Triage file to destination folder
                    triage_invoice_file(file_path, status)
                    progress_bar.progress((idx + 1) / len(inbox_files))

                status_box.update(label="All invoices processed successfully.", state="complete")
                st.rerun()

    st.divider()
    st.markdown("### Storage Locations")
    st.text(f"Inbox:        {INBOX_DIR}")
    st.text(f"Success:      {SUCCESS_DIR}")
    st.text(f"Human Review: {HUMAN_REVIEW_DIR}")
    st.text(f"Failed:       {FAILED_DIR}")


# --- Main Dashboard ---
st.title("Invoice Processing Dashboard")
st.write("Autonomous invoice extraction, verification, and human-in-the-loop audit system.")

# Fetch data
invoices_df = get_invoices_df()
review_files = get_pending_review_files()

# Top KPI Metric Cards
col1, col2, col3 = st.columns(3)

with col1:
    st.metric("Total Invoices Processed", len(invoices_df))

with col2:
    if not invoices_df.empty and "amount" in invoices_df.columns:
        total_spend = pd.to_numeric(invoices_df["amount"], errors="coerce").sum()
        st.metric("Total Recorded Spend", f"${total_spend:,.2f}")
    else:
        st.metric("Total Recorded Spend", "$0.00")

with col3:
    st.metric("Pending Human Review", len(review_files))

st.divider()

# Tab Navigation
tab_invoices, tab_review, tab_logs = st.tabs([
    "Invoices Table",
    "Human Review",
    "Audit Logs",
])

# --- Tab 1: Invoices Table ---
with tab_invoices:
    st.subheader("Verified Invoices in SQLite")

    if invoices_df.empty:
        st.info("No invoices recorded yet. Click 'Run Ingestion & Agent' in the sidebar to process invoices.")
    else:
        # Search & Filter bar
        search = st.text_input("Search by vendor name or invoice number", "").strip().lower()

        filtered_df = invoices_df
        if search:
            filtered_df = invoices_df[
                invoices_df["vendor_name"].str.lower().str.contains(search, na=False)
                | invoices_df["invoice_number"].str.lower().str.contains(search, na=False)
            ]

        st.caption(f"Showing {len(filtered_df)} of {len(invoices_df)} invoices")
        st.dataframe(filtered_df, use_container_width=True, hide_index=True)

        # CSV Download button
        csv_data = filtered_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="Download Table as CSV",
            data=csv_data,
            file_name="invoices_export.csv",
            mime="text/csv",
        )


# --- Tab 2: Human-in-the-Loop Review ---
with tab_review:
    st.subheader("Human Review Queue")

    if not review_files:
        st.info("No invoices currently require human review. All processed files passed validation.")
    else:
        st.warning(f"{len(review_files)} invoice(s) need manual review.")

        selected_file = st.selectbox("Select flagged file to inspect", review_files)
        file_path = os.path.join(HUMAN_REVIEW_DIR, selected_file)

        # Look up reason for failure from audit logs
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT details FROM execution_logs WHERE source_filename = ? AND step = 'human_review' ORDER BY id DESC LIMIT 1",
                (selected_file,),
            )
            log_row = cursor.fetchone()
            flag_reason = log_row["details"] if log_row else "Flagged for manual review."

        st.error(f"Reason for review: {flag_reason}")
        st.caption(f"File: {file_path}")

        # Human correction form
        with st.form("manual_approval_form"):
            st.markdown("#### Review & Correct Data")
            col_a, col_b = st.columns(2)

            with col_a:
                v_name = st.text_input("Vendor Name (Required)")
                inv_num = st.text_input("Invoice Number")
                currency = st.selectbox("Currency", ["USD", "EUR", "GBP", "INR", "CAD", "AUD"])

            with col_b:
                inv_date = st.date_input("Invoice Date", value=date.today())
                due_d = st.date_input("Due Date (Optional)", value=None)
                amount_str = st.text_input("Amount (e.g. 1500.00)")

            approved = st.form_submit_button("Approve & Save to Database", type="primary")

            if approved:
                if not v_name.strip():
                    st.error("Vendor Name is required.")
                elif not amount_str.strip():
                    st.error("Amount is required.")
                else:
                    try:
                        raw = ExtractedInvoice(
                            vendor_name=v_name.strip(),
                            invoice_number=inv_num.strip() or None,
                            invoice_date=str(inv_date),
                            due_date=str(due_d) if due_d else None,
                            amount=amount_str.strip(),
                            currency=currency,
                        )
                        record = build_validated_record(raw, file_path)
                        rec_id = save_invoice(record)
                        verify_invoice(rec_id, record)

                        # Move file to success
                        dest_path = os.path.join(SUCCESS_DIR, selected_file)
                        shutil.move(file_path, dest_path)

                        # Audit log
                        log_execution(
                            selected_file,
                            "human_review",
                            "approved",
                            f"Manually verified and saved as record #{rec_id}",
                        )
                        st.success(f"Invoice approved and saved as record #{rec_id}.")
                        st.rerun()
                    except Exception as err:
                        st.error(f"Validation or save failed: {err}")


# --- Tab 3: Audit Logs ---
with tab_logs:
    st.subheader("System Execution & Audit Trail")

    logs_df = get_logs_df()

    if logs_df.empty:
        st.info("No execution logs recorded yet.")
    else:
        # Filter options
        col_f1, col_f2 = st.columns([1, 3])
        with col_f1:
            status_filter = st.selectbox("Filter by Status", ["All", "success", "failed", "flagged"])

        filtered_logs = logs_df
        if status_filter != "All":
            filtered_logs = logs_df[logs_df["status"] == status_filter]

        st.caption(f"Showing {len(filtered_logs)} of {len(logs_df)} log entries")
        st.dataframe(filtered_logs, use_container_width=True, hide_index=True)

        # CSV Download
        logs_csv = filtered_logs.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="Download Logs as CSV",
            data=logs_csv,
            file_name="audit_logs_export.csv",
            mime="text/csv",
        )





