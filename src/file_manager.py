import os
import shutil

INBOX_DIR = "data/inbox"
SUCCESS_DIR = "data/processed/success"
HUMAN_REVIEW_DIR = "data/processed/human_review"
FAILED_DIR = "data/processed/failed"


def init_triage_folders() -> None:
    # Creates the inbox and processed triage folders if they do not exist
    for folder in [INBOX_DIR, SUCCESS_DIR, HUMAN_REVIEW_DIR, FAILED_DIR]:
        os.makedirs(folder, exist_ok=True)


def triage_invoice_file(file_path: str, status: str) -> str:
    # Moves the processed invoice file from inbox to its corresponding triage folder
    init_triage_folders()
    filename = os.path.basename(file_path)

    if status == "COMPLETED":
        destination_dir = SUCCESS_DIR
    elif status == "NEEDS_HUMAN_REVIEW":
        destination_dir = HUMAN_REVIEW_DIR
    else:
        # For DUPLICATE, FAILED, SAVE_FAILED, etc.
        destination_dir = FAILED_DIR

    destination_path = os.path.join(destination_dir, filename)
    shutil.move(file_path, destination_path)
    return destination_path
