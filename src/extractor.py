import base64
import os
import mimetypes
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional, Tuple
import pypdf
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage
from src.models import ExtractedInvoice, InvoiceRecord

# Load environment variables (.env)
load_dotenv()


def extract_text_from_pdf(file_path: str) -> str:
    """Extracts digital text from a PDF. Returns empty string if scanned/empty."""
    try:
        reader = pypdf.PdfReader(file_path)
        pages_text = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages_text).strip()
    except Exception:
        return ""


def encode_file_to_base64(file_path: str) -> Tuple[str, str]:
    """Reads raw file bytes and detects MIME type for multimodal vision fallback."""
    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type:
        mime_type = "application/pdf" if file_path.lower().endswith(".pdf") else "image/jpeg"

    with open(file_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")

    return encoded, mime_type


CURRENCY_MAP = {
    "₹": "INR",
    "RS": "INR",
    "RS.": "INR",
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
}


def parse_amount(raw_amount: Optional[str]) -> Optional[Decimal]:
    """Cleans currency symbols, commas, and converts string to Decimal."""
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
    """Tries common invoice date formats and returns a datetime.date."""
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
    """Normalizes currency symbol or text into a 3-letter ISO code. Defaults to INR."""
    if not raw_currency:
        return "INR"
    stripped = raw_currency.strip().upper()
    return CURRENCY_MAP.get(stripped, stripped[:3])
