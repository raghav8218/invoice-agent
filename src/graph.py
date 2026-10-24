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


def extract_node(state: InvoiceAgentState) -> Dict[str, Any]:
    # Node 1: Extracts raw invoice fields using Gemini
    file_path = state["file_path"]
    new_retries = state.get("retry_count", 0) + 1
    history = list(state.get("history", []))

    try:
        raw: Optional[ExtractedInvoice] = None

        # Tier 1: Try text first if PDF
        if file_path.lower().endswith(".pdf"):
            text = extract_text_from_pdf(file_path)
            if len(text) >= 50:
                raw = call_gemini_with_text(text)

        # Tier 2: Vision fallback
        if not raw or not raw.vendor_name or not raw.amount:
            base64_data, mime_type = encode_file_to_base64(file_path)
            raw = call_gemini_with_vision(base64_data, mime_type)

        history.append(f"Extraction attempt {new_retries} succeeded")
        return {
            "retry_count": new_retries,
            "raw_extracted": raw.model_dump(),
            "status": "EXTRACTED",
            "history": history,
        }
    except Exception as e:
        history.append(f"Extraction attempt {new_retries} failed: {e}")
        return {
            "retry_count": new_retries,
            "error_message": str(e),
            "status": "EXTRACTION_FAILED",
            "history": history,
        }


def validate_node(state: InvoiceAgentState) -> Dict[str, Any]:
    # Node 2: Validates raw data into a strict InvoiceRecord
    history = list(state.get("history", []))
    raw_dict = state.get("raw_extracted")

    if not raw_dict:
        return {
            "status": "VALIDATION_FAILED",
            "error_message": "No raw extracted data to validate",
            "history": history + ["Validation failed: no data"],
        }

    try:
        raw_model = ExtractedInvoice(**raw_dict)
        record = build_validated_record(raw_model, state["source_filename"])
        history.append("Validation passed Pydantic rules")
        return {
            # mode="json" converts Decimal and dates to strings for safe transfer
            "invoice_record": record.model_dump(mode="json"),
            "status": "VALIDATED",
            "history": history,
        }
    except Exception as e:
        history.append(f"Validation failed: {e}")
        return {
            "status": "VALIDATION_FAILED",
            "error_message": str(e),
            "history": history,
        }


def duplicate_check_node(state: InvoiceAgentState) -> Dict[str, Any]:
    # Node 3: Checks whether invoice already exists in SQLite
    history = list(state.get("history", []))
    record = state.get("invoice_record")

    if not record:
        return {
            "status": "VALIDATION_FAILED",
            "error_message": "Missing invoice record for duplicate check",
            "history": history,
        }

    is_dup = check_duplicate(record["vendor_name"], record["invoice_number"])
    if is_dup:
        history.append(f"Duplicate found: {record['vendor_name']} #{record['invoice_number']}")
        return {"status": "DUPLICATE", "history": history}

    history.append("No duplicate found in database")
    return {"status": "UNIQUE", "history": history}


def save_node(state: InvoiceAgentState) -> Dict[str, Any]:
    # Node 4: Saves validated record into SQLite
    history = list(state.get("history", []))
    record_dict = state.get("invoice_record")

    try:
        record = InvoiceRecord(**record_dict)
        record_id = save_invoice(record)
        history.append(f"Saved invoice to database with ID #{record_id}")
        return {
            "record_id": record_id,
            "status": "SAVED",
            "history": history,
        }
    except Exception as e:
        history.append(f"Save failed: {e}")
        return {
            "status": "SAVE_FAILED",
            "error_message": str(e),
            "history": history,
        }


def verify_node(state: InvoiceAgentState) -> Dict[str, Any]:
    # Node 5: Reads back and independently verifies the saved SQLite row
    history = list(state.get("history", []))
    record_id = state.get("record_id")
    record_dict = state.get("invoice_record")

    try:
        record = InvoiceRecord(**record_dict)
        verify_invoice(record_id, record)
        history.append(f"Verified row #{record_id} matches in-memory record 100%")
        log_execution(
            state["source_filename"],
            "pipeline",
            "success",
            f"Saved record #{record_id} and verified successfully",
        )
        return {"status": "COMPLETED", "history": history}
    except Exception as e:
        history.append(f"Verification failed: {e}")
        log_execution(state["source_filename"], "verification", "failed", str(e))
        return {
            "status": "VERIFICATION_FAILED",
            "error_message": str(e),
            "history": history,
        }


def human_review_node(state: InvoiceAgentState) -> Dict[str, Any]:
    # Node 6: Escalation for corrupted or unreadable invoices
    history = list(state.get("history", []))
    err = state.get("error_message", "Unknown error")
    history.append(f"Escalated to human review: {err}")
    log_execution(state["source_filename"], "human_review", "flagged", err)
    return {"status": "NEEDS_HUMAN_REVIEW", "history": history}


def route_after_extraction(state: InvoiceAgentState) -> str:
    # Routes after extraction: validate on success, retry if attempts remain, or review
    if state.get("status") == "EXTRACTED":
        return "validate"
    if state.get("retry_count", 0) < 3:
        return "extract"
    return "human_review"


def route_after_validation(state: InvoiceAgentState) -> str:
    # Routes after validation: check duplicates if valid, else review
    if state.get("status") == "VALIDATED":
        return "duplicate_check"
    return "human_review"


def route_after_duplicate_check(state: InvoiceAgentState) -> str:
    # Routes after duplicate check: save if unique, or end safely if duplicate
    if state.get("status") == "UNIQUE":
        return "save"
    return "end"


def route_after_save(state: InvoiceAgentState) -> str:
    # Routes after save: verify if saved, else review
    if state.get("status") == "SAVED":
        return "verify"
    return "human_review"


def create_invoice_graph():
    # Assembles all nodes and conditional edges into a runnable LangGraph application
    workflow = StateGraph(InvoiceAgentState)

    # 1. Register all nodes
    workflow.add_node("extract", extract_node)
    workflow.add_node("validate", validate_node)
    workflow.add_node("duplicate_check", duplicate_check_node)
    workflow.add_node("save", save_node)
    workflow.add_node("verify", verify_node)
    workflow.add_node("human_review", human_review_node)

    # 2. Starting edge
    workflow.add_edge(START, "extract")

    # 3. Conditional edges (decision points)
    workflow.add_conditional_edges(
        "extract",
        route_after_extraction,
        {"validate": "validate", "extract": "extract", "human_review": "human_review"},
    )
    workflow.add_conditional_edges(
        "validate",
        route_after_validation,
        {"duplicate_check": "duplicate_check", "human_review": "human_review"},
    )
    workflow.add_conditional_edges(
        "duplicate_check",
        route_after_duplicate_check,
        {"save": "save", "end": END},
    )
    workflow.add_conditional_edges(
        "save",
        route_after_save,
        {"verify": "verify", "human_review": "human_review"},
    )

    # 4. Terminal edges
    workflow.add_edge("verify", END)
    workflow.add_edge("human_review", END)

    return workflow.compile()


def run_invoice_agent(file_path: str) -> InvoiceAgentState:
    # Top-level helper to run the compiled agent on a file
    init_db()
    app = create_invoice_graph()
    initial_state: InvoiceAgentState = {
        "file_path": file_path,
        "source_filename": os.path.basename(file_path),
        "raw_extracted": None,
        "invoice_record": None,
        "record_id": None,
        "retry_count": 0,
        "status": "PENDING",
        "error_message": None,
        "history": ["Started invoice agent"],
    }
    return app.invoke(initial_state)
