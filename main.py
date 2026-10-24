import os
import glob
from src.db import init_db, get_connection
from src.graph import run_invoice_agent


def is_file_already_processed(filename: str, db_path: str = "data/invoices.db") -> bool:
    # Free local check to prevent unnecessary Gemini API calls
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM invoices WHERE source_filename = ?", (filename,))
        return cursor.fetchone() is not None


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

    folder = "data/sample_invoices"
    pdf_files = glob.glob(os.path.join(folder, "*.pdf"))

    if not pdf_files:
        print(f"No PDF invoices found in '{folder}'")
        return

    print("=" * 60)
    print(f"Scanning '{folder}' ({len(pdf_files)} PDF files found)...")
    print("=" * 60)

    for pdf_path in pdf_files:
        filename = os.path.basename(pdf_path)

        # 1. Zero-cost check: skip if already in SQLite
        if is_file_already_processed(filename):
            print(f"\n[SKIP] {filename} is already processed in SQLite. (0 API cost)")
            continue

        # 2. Process new invoice with LangGraph agent
        print(f"\n[AGENT RUNNING] Processing: {filename}")
        final_state = run_invoice_agent(pdf_path)

        # 3. Print the timeline of what the agent decided
        print("  Timeline:")
        for step in final_state.get("history", []):
            print(f"    -> {step}")
        print(f"  Final Status: {final_state['status']}")

    # 4. Display the database tables
    view_latest_records()


if __name__ == "__main__":
    main()
