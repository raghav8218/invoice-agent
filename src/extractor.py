import base64
import os
import mimetypes
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional, Tuple
import pypdf
from src.models import ExtractedInvoice, InvoiceRecord
from src.llm import DEFAULT_MODEL, call_gemini_with_text, call_gemini_with_vision

CURRENCY_MAP = {
    "₹": "INR",
    "RS": "INR",
    "RS.": "INR",
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
}


def extract_text_from_pdf(file_path: str) -> str:
    try:
        reader = pypdf.PdfReader(file_path)
        pages_text = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages_text).strip()
    except Exception:
        return ""


def encode_file_to_base64(file_path: str) -> Tuple[str, str]:
    #Reads raw file bytes and detects MIME type for multimodal vision fallback
    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type:
        mime_type = "application/pdf" if file_path.lower().endswith(".pdf") else "image/jpeg"

    with open(file_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")

    return encoded, mime_type


def parse_amount(raw_amount: Optional[str]) -> Optional[Decimal]:
    #currency symbol n decimal handling cuz float->risky
    if not raw_amount:
        return None
    cleaned = (
        raw_amount.replace(",", "")
        .replace("₹", "")
        .replace("Rs.", "")
        .replace("Rs", "")
        .replace("$", "")
        .replace("€", "")
        .replace("£", "")
        .strip()
    )
    try:
        val = Decimal(cleaned)
        return round(val, 2)
    except InvalidOperation:
        return None


def parse_date(raw_date: Optional[str]) -> Optional[date]:
    #date format
    if not raw_date:
        return None
    cleaned = raw_date.strip().replace(",", "")
    formats = [
        "%Y-%m-%d",      # 2024-01-15
        "%b %d %Y",      # Apr 21 2012
        "%B %d %Y",      # April 21 2012
        "%d/%m/%Y",      # 15/01/2024
        "%m/%d/%Y",      # 01/15/2024
        "%d-%m-%Y",      # 15-01-2024
    ]
    for fmt in formats:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def parse_currency(raw_currency: Optional[str]) -> str:
    # Safely maps currency symbol or code; raises ValueError if ambiguous
    if not raw_currency:
        raise ValueError("Missing currency in extracted invoice")
    stripped = raw_currency.strip().upper()
    if stripped in CURRENCY_MAP:
        return CURRENCY_MAP[stripped]
    if "$" in stripped:
        return "USD"
    if "₹" in stripped or "RS" in stripped:
        return "INR"
    if "€" in stripped:
        return "EUR"
    if "£" in stripped:
        return "GBP"
    if len(stripped) == 3 and stripped.isalpha():
        return stripped
    raise ValueError(f"Ambiguous or invalid currency: '{raw_currency}'")


def build_validated_record(raw: ExtractedInvoice, source_filename: str) -> InvoiceRecord:
    # Validates raw LLM output and converts it into a strict InvoiceRecord.
    if not raw or not raw.vendor_name:
        raise ValueError(f"Vendor name could not be extracted from {source_filename}")
    if not raw.invoice_number:
        raise ValueError(f"Invoice number could not be extracted from {source_filename}")

    amount = parse_amount(raw.amount)
    if amount is None or amount <= 0:
        raise ValueError(f"Invalid or missing amount ('{raw.amount}') in {source_filename}")

    invoice_date = parse_date(raw.invoice_date)
    if not invoice_date:
        raise ValueError(f"Invalid or missing invoice date ('{raw.invoice_date}') in {source_filename}")

    due_date = parse_date(raw.due_date)
    currency = parse_currency(raw.currency)

    return InvoiceRecord(
        vendor_name=raw.vendor_name,
        invoice_number=raw.invoice_number,
        invoice_date=invoice_date,
        due_date=due_date,
        amount=amount,
        currency=currency,
        source_filename=source_filename,
    )


def extract_invoice(file_path: str, model_name: str = DEFAULT_MODEL) -> InvoiceRecord:
    # Extracts invoice data visually using Gemini, immune to PDF font encoding bugs
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    source_filename = os.path.basename(file_path)
    base64_data, mime_type = encode_file_to_base64(file_path)
    raw = call_gemini_with_vision(base64_data, mime_type, model_name)
    return build_validated_record(raw, source_filename)
