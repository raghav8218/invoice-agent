import os
from typing import TypedDict, Optional, List, Dict, Any, Literal
from langgraph.graph import StateGraph, END, START
from src.models import ExtractedInvoice, InvoiceRecord
from src.extractor import (
    extract_text_from_pdf,
    encode_file_to_base64,
    build_validated_record,
)
from src.llm import DEFAULT_MODEL, call_gemini_with_text, call_gemini_with_vision
from src.db import (
    init_db,
    check_duplicate,
    save_invoice,
    verify_invoice,
    log_execution,
)


class InvoiceAgentState(TypedDict):
    """The central state dictionary passed between LangGraph nodes."""
    # Inputs
    file_path: str
    source_filename: str

    # Data
    raw_extracted: Optional[Dict[str, Any]]
    invoice_record: Optional[Dict[str, Any]]
    record_id: Optional[int]

    # Flow control & recovery
    retry_count: int
    status: str
    error_message: Optional[str]
    history: List[str]
