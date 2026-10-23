from datetime import date
from decimal import Decimal
import pytest
from pydantic import ValidationError
from src.models import InvoiceRecord


def test_valid_invoice():
    """Test creating a valid invoice record."""
    invoice = InvoiceRecord(
        vendor_name="Acme Corp",
        invoice_number="INV-1001",
        invoice_date=date(2024, 1, 15),
        due_date=date(2024, 2, 15),
        amount=Decimal("250.75"),
        currency="usd",
        source_filename="sample_invoice.pdf",
    )
    assert invoice.vendor_name == "Acme Corp"
    assert invoice.currency == "USD"  # Auto-uppercased
    assert invoice.amount == Decimal("250.75")


def test_negative_amount_fails():
    """Negative amount must fail validation."""
    with pytest.raises(ValidationError):
        InvoiceRecord(
            vendor_name="Acme Corp",
            invoice_number="INV-1001",
            invoice_date=date(2024, 1, 15),
            amount=Decimal("-10.00"),
            currency="USD",
            source_filename="sample.pdf",
        )


def test_due_date_before_invoice_date_fails():
    """Due date before invoice date must fail validation."""
    with pytest.raises(ValidationError):
        InvoiceRecord(
            vendor_name="Acme Corp",
            invoice_number="INV-1001",
            invoice_date=date(2024, 2, 15),
            due_date=date(2024, 1, 15),  # Earlier!
            amount=Decimal("100.00"),
            currency="USD",
            source_filename="sample.pdf",
        )
