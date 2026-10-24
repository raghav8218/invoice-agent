import os
from typing import Optional
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage
from src.models import ExtractedInvoice

load_dotenv()

# Centralized model variable - can be overridden via GEMINI_MODEL in .env
DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

EXTRACTION_PROMPT = """
You are an expert financial document assistant.
Extract all invoice details from this document:
- vendor_name: The company or person ISSUING the invoice (not the customer).
- invoice_number: The unique invoice or bill number.
- invoice_date: The date this invoice was created.
- due_date: The payment deadline date (if specified).
- amount: The final total or balance due amount.
- currency: The 3-letter ISO currency code (e.g. USD, EUR, GBP, INR). If you see '$', output 'USD'. If you see '₹' or 'Rs', output 'INR'. If you see '€', output 'EUR'.
"""


def get_llm(model_name: str = DEFAULT_MODEL) -> ChatGoogleGenerativeAI:
    """Initializes ChatGoogleGenerativeAI with configured model."""
    return ChatGoogleGenerativeAI(
        model=model_name,
    )


def call_gemini_with_text(text: str, model_name: str = DEFAULT_MODEL) -> ExtractedInvoice:
    """Sends document text to Gemini and returns structured raw fields."""
    llm = get_llm(model_name=model_name)
    structured_llm = llm.with_structured_output(ExtractedInvoice)
    prompt = f"{EXTRACTION_PROMPT}\n\nDocument Text:\n{text}"
    return structured_llm.invoke(prompt)


def call_gemini_with_vision(base64_data: str, mime_type: str, model_name: str = DEFAULT_MODEL) -> ExtractedInvoice:
    """Sends document image/PDF bytes to Gemini vision and returns structured raw fields."""
    llm = get_llm(model_name=model_name)
    structured_llm = llm.with_structured_output(ExtractedInvoice)
    message = HumanMessage(
        content=[
            {"type": "text", "text": EXTRACTION_PROMPT},
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{base64_data}"},
            },
        ]
    )
    return structured_llm.invoke([message])
