import os
from unittest.mock import patch
import pytest
from src.db import init_db, get_connection, DatabaseError, VerificationError
from src.models import ExtractedInvoice
from src.graph import run_invoice_agent


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    # Isolates tests in a temporary SQLite database, protecting data/invoices.db
    db_file = str(tmp_path / "test_agent.db")
    monkeypatch.setenv("DATABASE_PATH", db_file)
    init_db(db_file)
    return db_file


@patch("src.graph.call_gemini_with_vision")
def test_full_agent_success(mock_vision, test_db):
    # Test 1: Proves normal successful flow (Extract -> Validate -> Save -> Verify -> Completed)
    
    # 1. Program the fake Gemini response (0 API cost, instant)
    mock_vision.return_value = ExtractedInvoice(
        vendor_name="Acme Supplies",
        invoice_number="INV-999",
        invoice_date="2024-03-01",
        due_date="2024-04-01",
        amount="1500.00",
        currency="USD",
    )

    # 2. Run the agent on a sample file
    sample_file = "data/inbox/invoice_Aaron Hawkins_38460.pdf"
    final_state = run_invoice_agent(sample_file)

    # 3. Assertions: Prove every step succeeded
    assert final_state["status"] == "COMPLETED"
    assert final_state["retry_count"] == 1
    assert final_state["invoice_record"]["vendor_name"] == "Acme Supplies"
    assert final_state["record_id"] == 1
    assert "Verified row #1 matches in-memory record 100%" in final_state["history"][-1]

    # 4. Verify directly in SQLite that the row was actually persisted
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM invoices WHERE id = 1")
        row = cursor.fetchone()
        assert row is not None
        assert row["vendor_name"] == "Acme Supplies"
        assert row["amount"] == "1500.00"


@patch("src.graph.call_gemini_with_vision")
def test_agent_retry_exhaustion_escalates(mock_vision, test_db):
    # Test 2: Proves LangGraph retries 3 times on failure and safely escalates
    mock_vision.side_effect = Exception("API connection timeout")

    # 1. Count invoices before the run
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM invoices")
        count_before = cursor.fetchone()["count"]

    # 2. Run the agent (expected to retry 3 times and fail gracefully)
    sample_file = "data/inbox/invoice_Aaron Hawkins_38460.pdf"
    final_state = run_invoice_agent(sample_file)

    # 3. Assert retries stopped at max 3 and status escalated
    assert final_state["retry_count"] == 3
    assert final_state["status"] == "NEEDS_HUMAN_REVIEW"
    assert "API connection timeout" in final_state["error_message"]

    # 4. Assert zero new rows were added to the database
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM invoices")
        count_after = cursor.fetchone()["count"]

    assert count_after == count_before


@patch("src.graph.call_gemini_with_vision")
def test_agent_malformed_data_escalates(mock_vision, test_db):
    # Test 3: Proves validation failure stops pipeline and routes to human review
    mock_vision.return_value = ExtractedInvoice(
        vendor_name=None,
        invoice_number="INV-100",
        invoice_date="2024-03-01",
        amount="50.00",
        currency="USD",
    )

    # 1. Count invoices before the run
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM invoices")
        count_before = cursor.fetchone()["count"]

    # 2. Run the agent (expected to fail validation and escalate)
    sample_file = "data/inbox/invoice_Aaron Hawkins_38460.pdf"
    final_state = run_invoice_agent(sample_file)

    # 3. Assert pipeline stopped and routed to human review
    assert final_state["status"] == "NEEDS_HUMAN_REVIEW"
    assert "vendor name" in final_state["error_message"].lower()

    # 4. Assert zero rows were written to the database
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM invoices")
        count_after = cursor.fetchone()["count"]

    assert count_after == count_before


@patch("src.graph.call_gemini_with_vision")
def test_agent_duplicate_detection(mock_vision, test_db):
    # Test 4: Proves processing the same invoice twice flags it as DUPLICATE
    mock_vision.return_value = ExtractedInvoice(
        vendor_name="Acme Supplies",
        invoice_number="INV-999",
        invoice_date="2024-03-01",
        due_date="2024-04-01",
        amount="1500.00",
        currency="USD",
    )

    sample_file = "data/inbox/invoice_Aaron Hawkins_38460.pdf"

    # 1. Run first time: should succeed and create record #1
    state_1 = run_invoice_agent(sample_file)
    assert state_1["status"] == "COMPLETED"
    assert state_1["record_id"] == 1

    # 2. Run second time with identical data: should detect duplicate
    state_2 = run_invoice_agent(sample_file)
    assert state_2["status"] == "DUPLICATE"

    # 3. Assert database still has exactly 1 row (duplicate rejected)
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM invoices")
        assert cursor.fetchone()["count"] == 1


@patch("src.graph.call_gemini_with_vision")
@patch("src.graph.save_invoice")
def test_agent_database_save_failure(mock_save, mock_vision, test_db):
    # Test 5: Proves agent handles SQLite save errors safely without crashing
    mock_vision.return_value = ExtractedInvoice(
        vendor_name="Acme Supplies",
        invoice_number="INV-999",
        invoice_date="2024-03-01",
        due_date="2024-04-01",
        amount="1500.00",
        currency="USD",
    )
    # Simulate a SQLite disk write failure
    mock_save.side_effect = DatabaseError("Simulated SQLite disk write failure")

    sample_file = "data/inbox/invoice_Aaron Hawkins_38460.pdf"
    final_state = run_invoice_agent(sample_file)

    # 1. Assert workflow caught the save failure and escalated to human review
    assert final_state["status"] == "NEEDS_HUMAN_REVIEW"
    assert "Simulated SQLite disk write failure" in final_state["error_message"]
    assert final_state.get("record_id") is None


@patch("src.graph.call_gemini_with_vision")
@patch("src.graph.verify_invoice")
def test_agent_verification_mismatch(mock_verify, mock_vision, test_db):
    # Test 6: Proves agent detects when a saved row does not match in-memory data
    mock_vision.return_value = ExtractedInvoice(
        vendor_name="Acme Supplies",
        invoice_number="INV-999",
        invoice_date="2024-03-01",
        due_date="2024-04-01",
        amount="1500.00",
        currency="USD",
    )
    # Simulate a data mismatch caught during read-back
    mock_verify.side_effect = VerificationError("Saved amount does not match in-memory record")

    sample_file = "data/inbox/invoice_Aaron Hawkins_38460.pdf"
    final_state = run_invoice_agent(sample_file)

    # 1. Assert verification failed
    assert final_state["status"] == "VERIFICATION_FAILED"
    assert "Saved amount does not match" in final_state["error_message"]
    # Row was saved before verification failed
    assert final_state["record_id"] == 1


