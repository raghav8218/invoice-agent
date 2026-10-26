import os
import base64
from unittest.mock import MagicMock, patch
import pytest
from src.db import init_db, is_email_processed
from src.gmail import get_gmail_service, download_attachments, fetch_invoices_from_gmail


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    # Isolates SQLite database in a temporary test file
    db_file = str(tmp_path / "test_gmail.db")
    monkeypatch.setenv("DATABASE_PATH", db_file)
    init_db(db_file)
    return db_file


def test_missing_credentials_fallback(tmp_path):
    # Test 1: Proves missing credentials safely returns None and empty list
    non_existent = str(tmp_path / "does_not_exist.json")
    service = get_gmail_service(credentials_path=non_existent, token_path=non_existent)
    assert service is None

    with patch("src.gmail.get_gmail_service", return_value=None):
        result = fetch_invoices_from_gmail()
        assert result == []


def test_download_attachments_and_deduplication(test_db, tmp_path):
    # Test 2: Proves base64 attachment decoding and email deduplication
    fake_service = MagicMock()

    # Fake email payload with a PDF attachment
    fake_service.users().messages().get().execute.return_value = {
        "payload": {
            "parts": [
                {
                    "filename": "supplier_bill.pdf",
                    "body": {"attachmentId": "att_123"},
                }
            ]
        }
    }

    # Fake attachment payload (urlsafe base64)
    raw_pdf_bytes = b"%PDF-1.4 simulated invoice file"
    encoded_b64 = base64.urlsafe_b64encode(raw_pdf_bytes).decode("utf-8")
    fake_service.users().messages().attachments().get().execute.return_value = {
        "data": encoded_b64
    }

    # 1. Download attachment
    download_dir = str(tmp_path / "inbox")
    downloaded = download_attachments(fake_service, ["msg_100"], output_dir=download_dir)

    # 2. Assert file was saved and content matches
    assert len(downloaded) == 1
    assert os.path.exists(downloaded[0])
    with open(downloaded[0], "rb") as f:
        assert f.read() == raw_pdf_bytes

    # 3. Assert email is marked as processed in SQLite
    assert is_email_processed("msg_100") is True

    # 4. Running again with the same message ID must skip downloading (deduplication)
    downloaded_again = download_attachments(fake_service, ["msg_100"], output_dir=download_dir)
    assert len(downloaded_again) == 0
