import email
import imaplib
import os
from email.header import decode_header
from pathlib import Path

from dotenv import load_dotenv


def decode_text(value):
    if not value:
        return ""

    decoded_parts = decode_header(value)
    result = ""

    for part, encoding in decoded_parts:
        if isinstance(part, bytes):
            result += part.decode(encoding or "utf-8", errors="ignore")
        else:
            result += part

    return result


def safe_filename(filename):
    filename = decode_text(filename)
    filename = filename.replace("/", "_").replace("\\", "_")
    return filename.strip()


def connect_to_gmail():
    load_dotenv()

    host = os.getenv("EMAIL_HOST", "imap.gmail.com")
    port = int(os.getenv("EMAIL_PORT", "993"))
    user = os.getenv("EMAIL_USER")
    password = os.getenv("EMAIL_PASSWORD")

    if not user or not password:
        raise ValueError("Missing EMAIL_USER or EMAIL_PASSWORD in .env file.")

    mail = imaplib.IMAP4_SSL(host, port)
    mail.login(user, password)
    return mail


def download_attachments(mailbox="INBOX", mark_as_read=True):
    load_dotenv()

    attachment_dir = Path(os.getenv("ATTACHMENT_DIR", "./inbox"))
    attachment_dir.mkdir(parents=True, exist_ok=True)

    mail = connect_to_gmail()
    mail.select(mailbox)

    status, messages = mail.search(None, '(SUBJECT "invoice")')

    if status != "OK":
        print("Could not search mailbox.")
        mail.logout()
        return

    email_ids = messages[0].split()

    if not email_ids:
        print('No unread emails found with subject containing "invoice".')
        mail.logout()
        return

    print(f"Found {len(email_ids)} matching unread email(s).")

    downloaded_count = 0

    for email_id in email_ids:
        status, msg_data = mail.fetch(email_id, "(RFC822)")

        if status != "OK":
            print(f"Could not fetch email ID {email_id.decode()}.")
            continue

        raw_email = msg_data[0][1]
        message = email.message_from_bytes(raw_email)

        subject = decode_text(message.get("Subject"))
        sender = decode_text(message.get("From"))

        print("\n----------------------------------------")
        print(f"From: {sender}")
        print(f"Subject: {subject}")

        has_attachment = False

        for part in message.walk():
            content_disposition = part.get("Content-Disposition", "")

            if "attachment" not in content_disposition.lower():
                continue

            filename = part.get_filename()

            if not filename:
                continue

            filename = safe_filename(filename)
            has_attachment = True

            file_path = attachment_dir / filename

            counter = 1
            while file_path.exists():
                stem = file_path.stem
                suffix = file_path.suffix
                file_path = attachment_dir / f"{stem}_{counter}{suffix}"
                counter += 1

            payload = part.get_payload(decode=True)

            if payload:
                with open(file_path, "wb") as file:
                    file.write(payload)

                downloaded_count += 1
                print(f"Downloaded attachment: {file_path}")

        if not has_attachment:
            print("No attachment found in this email.")

        if mark_as_read:
            mail.store(email_id, "+FLAGS", "\\Seen")

    mail.logout()

    print("\n========================================")
    print(f"Downloaded {downloaded_count} attachment(s) to: {attachment_dir}")
    print("Done.")


if __name__ == "__main__":
    download_attachments()