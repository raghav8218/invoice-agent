import os
import glob
from src.db import init_db, get_connection
from src.graph import run_invoice_agent
from src.file_manager import (
    INBOX_DIR,
    init_triage_folders,
    triage_invoice_file,
)


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

    pdf_files = glob.glob(os.path.join(INBOX_DIR, "*.pdf"))

    print("=" * 60)
    print(f"Scanning Inbox: '{INBOX_DIR}' ({len(pdf_files)} new invoices found)")
    print("=" * 60)

    if not pdf_files:
        print(f"Inbox is empty! Place incoming invoices in '{INBOX_DIR}'.")
        view_latest_records()
        return

    for pdf_path in pdf_files:
        filename = os.path.basename(pdf_path)
        print(f"\n[AGENT RUNNING] Processing: {filename}")

        # 1. Run LangGraph agent
        final_state = run_invoice_agent(pdf_path)

        # 2. Print agent execution timeline
        print("  Timeline:")
        for step in final_state.get("history", []):
            print(f"    -> {step}")
        print(f"  Final Status: {final_state['status']}")

        # 3. Triage: Move file to success, human_review, or failed
        triaged_path = triage_invoice_file(pdf_path, final_state["status"])
        print(f"  Triaged: Moved file to '{triaged_path}'")

    # 4. Display final database records
    view_latest_records()


if __name__ == "__main__":
    main()
