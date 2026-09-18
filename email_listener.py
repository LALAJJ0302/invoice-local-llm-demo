import email
import imaplib
import os
import re
from email.header import decode_header
from html import unescape
from pathlib import Path

from dotenv import load_dotenv

import storage

# Attachments the downstream pipeline can actually read. "Parsing" an attachment starts
# with deciding whether it is a document at all: mailers routinely attach inline signature
# images, calendar invites (.ics) and tracking pixels with Content-Disposition: attachment,
# and saving those into inbox/ only gives main.py files it will skip anyway (or, worse,
# silently misclassify). A short allow-list is safer than trying to enumerate junk types.
DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".doc", ".csv", ".xlsx", ".xls", ".txt"}
DOCUMENT_CONTENT_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/csv",
    "text/plain",
}

_TAG_PATTERN = re.compile(r"<[^>]+>")

# MIME headers often name encodings Python does not register (e.g. windows-874 for Thai).
_ENCODING_ALIASES = {
    "windows-874": "cp874",
    "windows-1250": "cp1250",
    "windows-1251": "cp1251",
    "windows-1252": "cp1252",
    "windows-1253": "cp1253",
    "windows-1254": "cp1254",
    "windows-1255": "cp1255",
    "windows-1256": "cp1256",
    "windows-1257": "cp1257",
    "windows-1258": "cp1258",
}


def _normalise_encoding(encoding):
    if not encoding:
        return None
    key = encoding.lower().replace("_", "-")
    return _ENCODING_ALIASES.get(key, key)


def _decode_bytes(data, encoding=None):
    """Decodes bytes using the declared charset, with aliases and safe fallbacks."""
    normalised = _normalise_encoding(encoding)
    for candidate in (normalised, "utf-8", "latin-1"):
        if not candidate:
            continue
        try:
            return data.decode(candidate, errors="ignore")
        except LookupError:
            continue
    return data.decode("latin-1", errors="ignore")


def decode_text(value):
    if not value:
        return ""

    decoded_parts = decode_header(value)
    result = ""

    for part, encoding in decoded_parts:
        if isinstance(part, bytes):
            result += _decode_bytes(part, encoding)
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


def is_document_attachment(filename, content_type):
    """Decides whether a saved attachment is worth keeping.

    Extension first (cheap, and what main.py itself dispatches on), content-type as a
    fallback for the attachments a mailer names without a useful suffix.
    """
    extension = Path(filename).suffix.lower()
    if extension in DOCUMENT_EXTENSIONS:
        return True
    return (content_type or "").lower() in DOCUMENT_CONTENT_TYPES


def _decode_part_payload(part):
    """Decodes one MIME part's payload to text, using its own declared charset."""
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    return _decode_bytes(payload, part.get_content_charset())


def _html_to_text(html):
    """A deliberately small HTML -> text fallback.

    Strips tags, unescapes entities, collapses whitespace. This body text is only ever
    displayed (see database-spec.md's proposed email_messages.body column and app.py's
    Original Source panel); it is not parsed for structured fields, so it does not need to
    be more careful than that.
    """
    text = _TAG_PATTERN.sub(" ", html)
    text = unescape(text)
    return re.sub(r"[ \t]+", " ", text).strip()


def extract_body(message):
    """Prefers the text/plain part; falls back to text/html stripped of markup.

    Returns "" for a message with neither part (rare, but real), rather than raising.
    """
    plain, html = "", ""
    for part in message.walk():
        if "attachment" in str(part.get("Content-Disposition", "")).lower():
            continue
        content_type = part.get_content_type()
        if content_type == "text/plain" and not plain:
            plain = _decode_part_payload(part)
        elif content_type == "text/html" and not html:
            html = _decode_part_payload(part)

    if plain.strip():
        return plain.strip()
    if html.strip():
        return _html_to_text(html)
    return ""


def download_attachments(mailbox="INBOX", mark_as_read=True, db_path=storage.DEFAULT_DB_PATH):
    load_dotenv()

    attachment_dir = Path(os.getenv("ATTACHMENT_DIR", "./inbox"))
    attachment_dir.mkdir(parents=True, exist_ok=True)

    # Every fetched email is recorded here, keyed on its Message-ID. That is what makes
    # re-running this script safe: an email already seen is skipped before any attachment
    # is touched, instead of being re-downloaded with a "_1" suffix every time.
    store = storage.StorageManager(db_path)

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

    print(f"Found {len(email_ids)} matching email(s).")

    downloaded_count = 0
    skipped_seen_emails = 0
    skipped_seen_attachments = 0
    skipped_non_document = 0

    for email_id in email_ids:
        status, msg_data = mail.fetch(email_id, "(RFC822)")

        if status != "OK":
            print(f"Could not fetch email ID {email_id.decode()}.")
            continue

        raw_email = msg_data[0][1]
        message = email.message_from_bytes(raw_email)

        # The RFC 5322 Message-ID is globally unique by definition, so it is the natural
        # key for "have we already fetched this email". A handful of malformed messages
        # omit it; fall back to a per-mailbox id so those are not silently merged together.
        message_id = message.get("Message-ID") or f"<no-message-id-{email_id.decode()}@local>"
        subject = decode_text(message.get("Subject"))
        sender = decode_text(message.get("From"))
        received_at = message.get("Date")

        attachment_parts = [
            part for part in message.walk()
            if "attachment" in str(part.get("Content-Disposition", "")).lower()
            and part.get_filename()
        ]

        print("\n----------------------------------------")
        print(f"From: {sender}")
        print(f"Subject: {subject}")

        body_text = extract_body(message)
        print(f"Body: {len(body_text)} character(s) captured")

        # Records the email (idempotent upsert on message_id) before touching any
        # attachment, and tells us whether this exact email was already fetched.
        email_record = store.record_email(
            message_id=message_id,
            sender=sender,
            subject=subject,
            received_at=received_at,
            body_text=body_text or None,
            body_source='intake' if body_text else None,
            attachment_count=len(attachment_parts),
            attachment_names=[
                safe_filename(part.get_filename()) for part in attachment_parts
            ] or None,
        )

        if email_record["already_seen"]:
            print("Already fetched (seen by Message-ID). Skipping its attachments.")
            skipped_seen_emails += 1
            if mark_as_read:
                mail.store(email_id, "+FLAGS", "\\Seen")
            continue

        if not attachment_parts:
            print("No attachment found in this email.")

        saved_this_email = 0

        for part in attachment_parts:
            filename = safe_filename(part.get_filename())
            content_type = part.get_content_type()

            if not is_document_attachment(filename, content_type):
                print(f"  [Skip] {filename}: not a parseable document type ({content_type}).")
                skipped_non_document += 1
                continue

            payload = part.get_payload(decode=True)
            if not payload:
                continue

            content_sha256 = storage.sha256_bytes(payload)

            # Attachment-level dedup: the same email can legitimately carry the same bytes
            # twice (e.g. inline + attached copies of one PDF in a multipart/mixed
            # message). Checked before writing anything to disk.
            if store.has_seen_attachment(email_record["email_id"], content_sha256):
                print(f"  [Skip] {filename}: identical attachment already recorded for this email.")
                skipped_seen_attachments += 1
                continue

            file_path = attachment_dir / filename
            counter = 1
            while file_path.exists():
                stem = file_path.stem
                suffix = file_path.suffix
                file_path = attachment_dir / f"{stem}_{counter}{suffix}"
                counter += 1

            with open(file_path, "wb") as file:
                file.write(payload)

            store.record_attachment(
                email_record["email_id"], filename, content_sha256, str(file_path))

            downloaded_count += 1
            saved_this_email += 1
            print(f"Downloaded attachment: {file_path}")

        if attachment_parts and saved_this_email == 0:
            print("No new attachments saved from this email.")

        if mark_as_read:
            mail.store(email_id, "+FLAGS", "\\Seen")

    mail.logout()

    print("\n========================================")
    print(f"Downloaded {downloaded_count} attachment(s) to: {attachment_dir}")
    if skipped_seen_emails:
        print(f"Skipped {skipped_seen_emails} already-fetched email(s).")
    if skipped_seen_attachments:
        print(f"Skipped {skipped_seen_attachments} duplicate attachment(s).")
    if skipped_non_document:
        print(f"Skipped {skipped_non_document} non-document attachment(s).")
    print("Done.")


if __name__ == "__main__":
    download_attachments()
