import os
import base64
from typing import List, Optional, Dict, Any
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build, Resource

# Only read-only permission requested
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def get_gmail_service(
    credentials_path: str = "credentials.json",
    token_path: str = "token.json"
) -> Optional[Resource]:
    # Handles OAuth2 authentication and returns a Gmail API service client
    creds = None

    # 1. Check for existing session token
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    # 2. Refresh or authenticate if needed
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(credentials_path):
                print("\n[Gmail Notice] 'credentials.json' not found.")
                print("  To enable live Gmail fetching:")
                print("  1. Create a Google Cloud project with Gmail API enabled.")
                print("  2. Create an OAuth Client ID (Desktop app).")
                print("  3. Download the JSON and save as 'credentials.json' in project root.\n")
                return None

            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)

        # 3. Save session token for future runs
        with open(token_path, "w") as token:
            token.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def search_invoice_emails(
    service: Resource,
    query: str = "has:attachment (filename:pdf OR filename:png OR filename:jpg OR filename:jpeg) invoice",
    max_results: int = 5
) -> List[str]:
    # Searches Gmail for emails with PDF or image invoice attachments
    try:
        results = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=max_results
        ).execute()
        messages = results.get("messages", [])
        return [msg["id"] for msg in messages]
    except Exception as e:
        print(f"Error searching Gmail: {e}")
        return []


VALID_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg")


def download_attachments(
    service: Resource,
    message_ids: List[str],
    output_dir: str = "data/inbox"
) -> List[str]:
    # Downloads PDF/image attachments from matching emails into data/inbox
    os.makedirs(output_dir, exist_ok=True)
    downloaded_files = []

    for msg_id in message_ids:
        try:
            message = service.users().messages().get(userId="me", id=msg_id).execute()
            payload = message.get("payload", {})
            parts = payload.get("parts", [])

            for part in parts:
                filename = part.get("filename", "")
                body = part.get("body", {})
                attachment_id = body.get("attachmentId")

                # Check if it has an attachment and valid file extension
                if attachment_id and filename.lower().endswith(VALID_EXTENSIONS):
                    attachment = service.users().messages().attachments().get(
                        userId="me",
                        messageId=msg_id,
                        id=attachment_id
                    ).execute()

                    # Gmail encodes files with urlsafe base64
                    file_data = base64.urlsafe_b64decode(attachment["data"])
                    file_path = os.path.join(output_dir, filename)

                    with open(file_path, "wb") as f:
                        f.write(file_data)

                    downloaded_files.append(file_path)
                    print(f"  Downloaded from Gmail: {filename} -> {file_path}")

        except Exception as e:
            print(f"  Failed downloading attachments for email {msg_id}: {e}")

    return downloaded_files


def fetch_invoices_from_gmail(
    query: str = "has:attachment (filename:pdf OR filename:png OR filename:jpg OR filename:jpeg) invoice",
    output_dir: str = "data/inbox",
    max_results: int = 5,
) -> List[str]:
    # High-level coordinator: authenticates, searches, and downloads invoice attachments
    service = get_gmail_service()
    if not service:
        # Graceful fallback: returns empty list if credentials.json is not configured
        return []

    print(f"[Gmail] Searching inbox with query: '{query}'...")
    message_ids = search_invoice_emails(service, query=query, max_results=max_results)

    if not message_ids:
        print("[Gmail] No matching invoice emails found.")
        return []

    print(f"[Gmail] Found {len(message_ids)} matching email(s). Downloading attachments...")
    downloaded = download_attachments(service, message_ids, output_dir=output_dir)
    print(f"[Gmail] Successfully downloaded {len(downloaded)} attachment(s) to '{output_dir}'.")
    return downloaded
