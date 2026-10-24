import os
import sqlite3
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from src.models import InvoiceRecord


class DatabaseError(Exception):
    # Base exception for database errors
    pass


class DuplicateInvoiceError(DatabaseError):
    # Raised when an invoice already exists
    pass


class VerificationError(DatabaseError):
    # Raised when saved data doesn't match expected data
    pass


def get_connection(db_path: str = "data/invoices.db") -> sqlite3.Connection:
    # Creates folder if needed and connects to SQLite
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str = "data/invoices.db") -> None:
    # Creates the tables if they don't already exist
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        
        # 1. Invoices table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vendor_name TEXT NOT NULL,
                invoice_number TEXT NOT NULL,
                invoice_date TEXT NOT NULL,
                due_date TEXT,
                amount TEXT NOT NULL,
                currency TEXT NOT NULL,
                source_filename TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(vendor_name, invoice_number)
            );
            """
        )
        
        # 2. Execution audit logs table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                source_filename TEXT NOT NULL,
                step TEXT NOT NULL,
                status TEXT NOT NULL,
                details TEXT
            );
            """
        )

        # 3. Processed Gmail emails table (prevents duplicate downloads)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_emails (
                message_id TEXT PRIMARY KEY,
                processed_at TEXT NOT NULL
            );
            """
        )
        conn.commit()


def is_email_processed(message_id: str, db_path: str = "data/invoices.db") -> bool:
    # Checks if a Gmail message_id was already downloaded or processed
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM processed_emails WHERE message_id = ?", (message_id,))
        return cursor.fetchone() is not None


def mark_email_processed(message_id: str, db_path: str = "data/invoices.db") -> None:
    # Records that a Gmail message_id has been downloaded
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO processed_emails (message_id, processed_at) VALUES (?, ?)",
            (message_id, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def check_duplicate(vendor_name: str, invoice_number: str, db_path: str = "data/invoices.db") -> bool:
    # Checks if an invoice with the given vendor and invoice number already exists
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT 1 FROM invoices 
            WHERE LOWER(vendor_name) = LOWER(?) AND LOWER(invoice_number) = LOWER(?)
            """,
            (vendor_name.strip(), invoice_number.strip()),
        )
        return cursor.fetchone() is not None


def save_invoice(invoice: InvoiceRecord, db_path: str = "data/invoices.db") -> int:
    # Saves a validated invoice to SQLite
    if check_duplicate(invoice.vendor_name, invoice.invoice_number, db_path):
        raise DuplicateInvoiceError(
            f"Invoice already exists for vendor '{invoice.vendor_name}' with number '{invoice.invoice_number}'"
        )

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO invoices (
                vendor_name, invoice_number, invoice_date, due_date,
                amount, currency, source_filename, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                invoice.vendor_name,
                invoice.invoice_number,
                invoice.invoice_date.isoformat(),
                invoice.due_date.isoformat() if invoice.due_date else None,
                str(invoice.amount),
                invoice.currency,
                invoice.source_filename,
                invoice.created_at.isoformat(),
            ),
        )
        conn.commit()
        return cursor.lastrowid


def get_invoice_by_id(record_id: int, db_path: str = "data/invoices.db") -> Optional[InvoiceRecord]:
    # Retrieves an invoice by ID and reconstructs the InvoiceRecord
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM invoices WHERE id = ?", (record_id,))
        row = cursor.fetchone()
        if not row:
            return None

        return InvoiceRecord(
            vendor_name=row["vendor_name"],
            invoice_number=row["invoice_number"],
            invoice_date=date.fromisoformat(row["invoice_date"]),
            due_date=date.fromisoformat(row["due_date"]) if row["due_date"] else None,
            amount=Decimal(row["amount"]),
            currency=row["currency"],
            source_filename=row["source_filename"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )


def verify_invoice(record_id: int, expected: InvoiceRecord, db_path: str = "data/invoices.db") -> bool:
    # Reads back the invoice from the database and checks that every field matches the original
    retrieved = get_invoice_by_id(record_id, db_path)
    if not retrieved:
        raise VerificationError(f"Verification failed: record id {record_id} not found in database.")

    mismatches = []
    if retrieved.vendor_name != expected.vendor_name:
        mismatches.append(f"vendor: saved='{retrieved.vendor_name}', expected='{expected.vendor_name}'")
    if retrieved.invoice_number != expected.invoice_number:
        mismatches.append(f"invoice_number: saved='{retrieved.invoice_number}', expected='{expected.invoice_number}'")
    if retrieved.invoice_date != expected.invoice_date:
        mismatches.append(f"invoice_date: saved='{retrieved.invoice_date}', expected='{expected.invoice_date}'")
    if retrieved.due_date != expected.due_date:
        mismatches.append(f"due_date: saved='{retrieved.due_date}', expected='{expected.due_date}'")
    if retrieved.amount != expected.amount:
        mismatches.append(f"amount: saved={retrieved.amount}, expected={expected.amount}")
    if retrieved.currency != expected.currency:
        mismatches.append(f"currency: saved='{retrieved.currency}', expected='{expected.currency}'")

    if mismatches:
        raise VerificationError("Persisted invoice failed verification: " + "; ".join(mismatches))

    return True


def log_execution(
    source_filename: str,
    step: str,
    status: str,
    details: str = "",
    db_path: str = "data/invoices.db"
) -> None:
    # Logs an agent execution step to the database
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO execution_logs (timestamp, source_filename, step, status, details)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                source_filename,
                step,
                status,
                details,
            ),
        )
        conn.commit()
