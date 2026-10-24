import os
import glob
from src.db import init_db, get_connection
from src.graph import run_invoice_agent
from src.file_manager import (
    INBOX_DIR,
    init_triage_folders,
    triage_invoice_file,
)
from src.gmail import fetch_invoices_from_gmail


def view_latest_records(db_path: str = "data/invoices.db") -> None:
    # Displays the current stored invoices and agent logs
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        print("\n=== Current Invoices in SQLite ===")
        cursor.execute("SELECT id, vendor_name, invoice_number, invoice_date, amount, currency FROM invoices")
        rows = cursor.fetchall()
        if not rows:
            print("  (No invoices recorded yet)")
        for r in rows:
            print(f"  [ID #{r['id']}] Vendor: {r['vendor_name']} | Invoice #: {r['invoice_number']} | Date: {r['invoice_date']} | Total: {r['currency']} {r['amount']}")

        print("\n=== Recent Agent Execution Logs ===")
        cursor.execute("SELECT timestamp, source_filename, step, status, details FROM execution_logs ORDER BY id DESC LIMIT 5")
        logs = cursor.fetchall()
        for l in logs:
            print(f"  [{l['timestamp'][:19]}] File: {l['source_filename']} | Step: {l['step']} | Status: {l['status']} | {l['details']}")


def main():
    init_db()
    init_triage_folders()

    # 1. Check Gmail for new invoice attachments (graceful fallback if not configured)
    fetch_invoices_from_gmail()

    # 2. Scan inbox for files to process
    valid_patterns = ("*.pdf", "*.png", "*.jpg", "*.jpeg")
    invoice_files = []
    for pattern in valid_patterns:
        invoice_files.extend(glob.glob(os.path.join(INBOX_DIR, pattern)))

    print("=" * 60)
    print(f"Scanning Inbox: '{INBOX_DIR}' ({len(invoice_files)} invoice(s) waiting)")
    print("=" * 60)

    if not invoice_files:
        print(f"Inbox is empty! Place incoming invoices in '{INBOX_DIR}' or connect Gmail.")
        view_latest_records()
        return

    for file_path in invoice_files:
        filename = os.path.basename(file_path)
        print(f"\n[AGENT RUNNING] Processing: {filename}")

        # 3. Run LangGraph agent
        final_state = run_invoice_agent(file_path)

        # 4. Print agent execution timeline
        print("  Timeline:")
        for step in final_state.get("history", []):
            print(f"    -> {step}")
        print(f"  Final Status: {final_state['status']}")

        # 5. Triage: Move file to success, human_review, or failed
        triaged_path = triage_invoice_file(file_path, final_state["status"])
        print(f"  Triaged: Moved file to '{triaged_path}'")

    # 6. Display final database records
    view_latest_records()


if __name__ == "__main__":
    main()
