from datetime import date
from decimal import Decimal
import pytest
from src.models import InvoiceRecord
from src.db import (
    init_db,
    save_invoice,
    verify_invoice,
    check_duplicate,
    log_execution,
    DuplicateInvoiceError,
    VerificationError,
)


@pytest.fixture
def temp_db(tmp_path):
    """Creates a temporary SQLite database for each test."""
    db_file = tmp_path / "test_invoices.db"
    init_db(str(db_file))
    return str(db_file)


@pytest.fixture
def sample_invoice():
    """Provides a sample validated invoice for testing."""
    return InvoiceRecord(
        vendor_name="Acme Supplies",
        invoice_number="INV-2024-001",
        invoice_date=date(2024, 1, 15),
        due_date=date(2024, 2, 15),
        amount=Decimal("1250.50"),
        currency="USD",
        source_filename="acme_inv_001.pdf",
    )


def test_save_and_verify_success(temp_db, sample_invoice):
    """Test saving an invoice and verifying its persisted values."""
    row_id = save_invoice(sample_invoice, db_path=temp_db)
    assert row_id > 0
    assert verify_invoice(row_id, sample_invoice, db_path=temp_db) is True


def test_verify_detects_tampering(temp_db, sample_invoice):
    """Test that verify_invoice raises VerificationError if values do not match."""
    row_id = save_invoice(sample_invoice, db_path=temp_db)

    # Tampered expectation
    tampered = sample_invoice.model_copy(update={"amount": Decimal("9999.99")})

    with pytest.raises(VerificationError) as exc_info:
        verify_invoice(row_id, tampered, db_path=temp_db)
    assert "amount" in str(exc_info.value)


def test_duplicate_prevention(temp_db, sample_invoice):
    """Test that saving duplicate invoice raises DuplicateInvoiceError."""
    assert check_duplicate(sample_invoice.vendor_name, sample_invoice.invoice_number, db_path=temp_db) is False

    save_invoice(sample_invoice, db_path=temp_db)
    assert check_duplicate(sample_invoice.vendor_name, sample_invoice.invoice_number, db_path=temp_db) is True

    with pytest.raises(DuplicateInvoiceError):
        save_invoice(sample_invoice, db_path=temp_db)


def test_log_execution(temp_db):
    """Test audit log writing."""
    log_execution(
        source_filename="invoice_123.pdf",
        step="extraction",
        status="success",
        details="Extracted 7 fields",
        db_path=temp_db,
    )
