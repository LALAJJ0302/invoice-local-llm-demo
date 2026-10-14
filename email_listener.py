import argparse
import email
import imaplib
import os
import re
import threading
import time
from email.header import decode_header
from html import unescape
from pathlib import Path, PureWindowsPath

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


# The sender chooses an attachment's name, so it must never choose the folder. Windows reads
# "C:x.pdf" as drive-relative and "x.pdf:y" as a hidden stream, which is why removing slashes
# alone was not enough (codeql-fixes-spec.md). Unicode letters are kept on purpose.
_FORBIDDEN_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_DEVICE_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
MAX_FILENAME_LENGTH = 200


def safe_filename(filename):
    name = decode_text(filename)
    # The last component under either separator; this also drops "C:" and "\\server\share".
    name = PureWindowsPath(name.replace("/", "\\")).name
    name = _FORBIDDEN_FILENAME_CHARS.sub("_", name).strip(" .")
    if not name:
        return "attachment"
    if name.split(".")[0].upper() in _RESERVED_DEVICE_NAMES:
        name = f"attachment_{name}"
    if len(name) > MAX_FILENAME_LENGTH:
        suffix = Path(name).suffix[:20]
        name = name[:MAX_FILENAME_LENGTH - len(suffix)] + suffix
    return name


def _is_inside(directory, path):
    """True when `path` resolves to something strictly inside `directory`."""
    directory = Path(directory).resolve()
    path = Path(path).resolve()
    return path != directory and path.is_relative_to(directory)


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


# IMAP SEARCH: unread messages whose subject contains "invoice". Read mail is left alone.
UNREAD_INVOICE_SEARCH = '(UNSEEN SUBJECT "invoice")'
DEFAULT_POLL_INTERVAL_SECONDS = 10
MIN_POLL_INTERVAL_SECONDS = 1


def poll_interval_seconds(explicit=None):
    """Seconds between inbox checks. Never below one second, so the loop cannot spin."""
    if explicit is None:
        raw = os.getenv("EMAIL_POLL_INTERVAL", str(DEFAULT_POLL_INTERVAL_SECONDS))
        try:
            seconds = float(raw)
        except (TypeError, ValueError):
            print(
                f"Invalid EMAIL_POLL_INTERVAL {raw!r}; "
                f"using {DEFAULT_POLL_INTERVAL_SECONDS}s."
            )
            seconds = float(DEFAULT_POLL_INTERVAL_SECONDS)
    else:
        seconds = float(explicit)
    if seconds < MIN_POLL_INTERVAL_SECONDS:
        print(
            f"Poll interval {seconds:g}s is too small; "
            f"using {MIN_POLL_INTERVAL_SECONDS}s."
        )
        return float(MIN_POLL_INTERVAL_SECONDS)
    return seconds


def _close_mailbox(mail):
    try:
        mail.logout()
    except Exception as error:
        print(f"Could not close mailbox: {error}")


def download_attachments(mailbox="INBOX", mark_as_read=True, db_path=storage.DEFAULT_DB_PATH):
    load_dotenv()

    attachment_dir = Path(os.getenv("ATTACHMENT_DIR", "./inbox"))
    attachment_dir.mkdir(parents=True, exist_ok=True)

    # Every fetched email is recorded here, keyed on its Message-ID. That is what makes
    # re-running this script safe: an email already seen is skipped before any attachment
    # is touched, instead of being re-downloaded with a "_1" suffix every time.
    store = storage.StorageManager(db_path)

    mail = connect_to_gmail()
    try:
        counts = _download_from_open_mailbox(
            mail, store, attachment_dir, mailbox, mark_as_read
        )
    finally:
        _close_mailbox(mail)

    if counts is None:
        return

    downloaded_count, skipped_seen_emails, skipped_seen_attachments, skipped_non_document = counts

    print("\n========================================")
    print(f"Downloaded {downloaded_count} attachment(s) to: {attachment_dir}")
    if skipped_seen_emails:
        print(f"Skipped {skipped_seen_emails} already-fetched email(s).")
    if skipped_seen_attachments:
        print(f"Skipped {skipped_seen_attachments} duplicate attachment(s).")
    if skipped_non_document:
        print(f"Skipped {skipped_non_document} non-document attachment(s).")
    print("Done.")


def _download_from_open_mailbox(mail, store, attachment_dir, mailbox, mark_as_read):
    """One pass over the open mailbox. Returns counts, or None when the search failed
    or nothing unread matched. Does not log out; the caller closes the connection.
    """
    mail.select(mailbox)

    status, messages = mail.search(None, UNREAD_INVOICE_SEARCH)

    if status != "OK":
        print("Could not search mailbox.")
        return None

    raw_ids = messages[0] if messages else None
    email_ids = raw_ids.split() if raw_ids else []

    if not email_ids:
        print('No unread emails found with subject containing "invoice".')
        return None

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

            # Backstop: safe_filename should already guarantee this.
            if not _is_inside(attachment_dir, file_path):
                print(f"  [Skip] {filename}: would be saved outside {attachment_dir}.")
                continue

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

    return (
        downloaded_count,
        skipped_seen_emails,
        skipped_seen_attachments,
        skipped_non_document,
    )


# Claimed files leave inbox/ for inbox/.processing/<batch>/ before a pipeline run.
# WorkflowOrchestrator only lists the top of its inbox, so a later poll cannot see them.
# Permanent failures leave the batch for inbox/.failed/<reason>/, which is also invisible
# to the next claim: the name starts with '.' and claim_inbox does not descend.
PROCESSING_DIR_NAME = ".processing"
FAILED_DIR_NAME = ".failed"


def resolve_inbox_dir(inbox_dir=None):
    """Folder the listener downloads into and later claims. Tests pass their own."""
    if inbox_dir is not None:
        return inbox_dir
    load_dotenv()
    return os.getenv("ATTACHMENT_DIR", "./inbox")


def _unique_path(directory, name):
    directory = Path(directory)
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem = Path(name).stem
    suffix = Path(name).suffix
    counter = 1
    while True:
        candidate = directory / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def claim_inbox(inbox_dir="./inbox"):
    """Move visible files in inbox/ into a new batch directory.

    Returns that directory, or None when there is nothing to claim. Names starting
    with '.' and directories stay where they are. os.replace is atomic on one drive,
    so a file is either still in the inbox or already in the batch.
    """
    inbox = Path(inbox_dir)
    inbox.mkdir(parents=True, exist_ok=True)
    files = sorted(
        (
            path for path in inbox.iterdir()
            if path.is_file() and not path.name.startswith(".")
        ),
        key=lambda path: path.name,
    )
    if not files:
        return None

    processing = inbox / PROCESSING_DIR_NAME
    processing.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    batch = processing / stamp
    suffix = 1
    while batch.exists():
        batch = processing / f"{stamp}-{suffix}"
        suffix += 1
    batch.mkdir()
    for path in files:
        os.replace(path, batch / path.name)
    print(f"[poll] claimed {len(files)} file(s) into {batch}")
    return batch


def release_batch(batch):
    """Move anything the pipeline left in the batch back to inbox/."""
    batch = Path(batch)
    if not batch.is_dir():
        return
    inbox = batch.parent.parent
    inbox.mkdir(parents=True, exist_ok=True)
    for path in sorted(batch.iterdir(), key=lambda item: item.name):
        if not path.is_file() or path.name.startswith("."):
            continue
        destination = _unique_path(inbox, path.name)
        os.replace(path, destination)
        print(f"[poll] returned {destination.name} to inbox for a later check")
    try:
        batch.rmdir()
    except OSError:
        pass


def recover_stranded_batches(inbox_dir="./inbox", active=None):
    """Return batches left behind by a killed process. Live batches are left alone."""
    processing = Path(inbox_dir) / PROCESSING_DIR_NAME
    if not processing.is_dir():
        return
    live = {Path(path).resolve() for path in (active or ())}
    for batch in list(processing.iterdir()):
        if not batch.is_dir():
            continue
        if batch.resolve() in live:
            continue
        release_batch(batch)
    try:
        processing.rmdir()
    except OSError:
        pass


def run_pipeline(inbox_dir="./inbox"):
    """Process one folder the same way `python main.py` does.

    Imported here so a poll loop that supplies its own process_fn never loads Ollama.
    Returns the per-file outcomes. A process_fn that returns None keeps the old
    contract: leftovers go back to the inbox.
    """
    from main import WorkflowOrchestrator

    return WorkflowOrchestrator(
        inbox_dir=str(inbox_dir),
        archive_dir="./archive",
        threshold=0.80,
    ).run()


def dispose_outcomes(holding_dir, outcomes, inbox_dir=None):
    """Move files the pipeline will not retry out of the folder it was reading.

    holding_dir is where the files are now: a claimed batch, or inbox/ for a one-shot
    `python main.py`. inbox_dir is the inbox root the next poll claims from. Quarantine
    folders live under that root, never back in it.

    A list of outcomes is the new contract. None means the caller did not classify
    files, and this function leaves them for release_batch.
    """
    if not isinstance(outcomes, list):
        return
    holding = Path(holding_dir)
    inbox = Path(inbox_dir) if inbox_dir is not None else holding
    limit = storage.MAX_INGESTION_ATTEMPTS
    for outcome in outcomes:
        status = getattr(outcome, "status", None)
        name = getattr(outcome, "file_name", None)
        if not name or status == "stored":
            continue
        attempt = getattr(outcome, "attempt_count", 1) or 1
        if status == "retryable_failure" and attempt < limit:
            print(f"[Retry] {name} attempt {attempt}/{limit}, returning to inbox")
            continue
        if status == "needs_ocr":
            bucket, label = "needs_ocr", "Needs OCR"
        elif status == "unsupported":
            bucket, label = "unsupported", "Unsupported"
        elif status in ("retryable_failure", "retry_exhausted"):
            bucket, label = "retry_exhausted", "Retry exhausted"
        else:
            continue
        path = holding / name
        if not path.is_file():
            continue
        destination_dir = inbox / FAILED_DIR_NAME / bucket
        destination_dir.mkdir(parents=True, exist_ok=True)
        os.replace(path, _unique_path(destination_dir, name))
        print(f"[{label}] Moved {name} to {inbox.name}/.failed/{bucket}/")


def finish_batch(batch, process_fn, active):
    """Run the pipeline for one claimed batch, then put each leftover where it belongs.

    Classified permanent failures are quarantined. A retryable failure under the
    attempt limit, and anything left behind by an exception, goes back to inbox/.
    """
    outcomes = None
    try:
        outcomes = process_fn(str(batch))
    except KeyboardInterrupt:
        raise
    except Exception as error:
        print(
            f"[poll] pipeline failed for {batch} ({error}). "
            "Leftover files return to inbox."
        )
    finally:
        try:
            if isinstance(outcomes, list):
                dispose_outcomes(batch, outcomes, inbox_dir=Path(batch).parent.parent)
            release_batch(batch)
        finally:
            active.discard(Path(batch).resolve())


def spawn_background(process_fn, batch, active):
    """Start finish_batch on a thread the process will wait for on the way out."""
    thread = threading.Thread(
        target=finish_batch,
        args=(batch, process_fn, active),
        name=f"pipeline-{Path(batch).name}",
        daemon=False,
    )
    thread.start()
    return thread


def poll_inbox(
    mailbox="INBOX",
    mark_as_read=True,
    db_path=storage.DEFAULT_DB_PATH,
    interval=None,
    sleep_fn=None,
    process_fn=None,
    spawn_fn=None,
    inbox_dir=None,
):
    """Keep checking the inbox until Ctrl+C. One IMAP error does not stop the loop.

    After each check, files still in the inbox are claimed and processed on a
    background thread. The next check does not wait for that thread and does not
    see the claimed files. A failed inbox check still claims what is already there.
    Passing spawn_fn replaces the thread (tests use it to run a batch inline).
    """
    if sleep_fn is None:
        sleep_fn = time.sleep
    if process_fn is None:
        process_fn = run_pipeline
    if spawn_fn is None:
        spawn_fn = spawn_background
    inbox_dir = resolve_inbox_dir(inbox_dir)
    seconds = poll_interval_seconds(interval)
    active = set()

    def stopped():
        running = len(active)
        if running:
            print(
                f"Stopped. {running} pipeline(s) still running; "
                "this process exits when they finish."
            )
        else:
            print("Stopped.")

    recover_stranded_batches(inbox_dir, active)
    print(
        f"Watching {mailbox} for unread invoice mail every {seconds:g}s. "
        "Each check processes new inbox files in the background. Ctrl+C to stop."
    )
    while True:
        print("[poll] checking inbox")
        try:
            download_attachments(
                mailbox=mailbox, mark_as_read=mark_as_read, db_path=db_path
            )
        except KeyboardInterrupt:
            stopped()
            return
        except Exception as error:
            print(f"[poll] inbox check failed ({error}). Will retry.")
        try:
            batch = claim_inbox(inbox_dir)
            if batch is None:
                print("[poll] inbox empty, nothing to process")
            else:
                resolved = Path(batch).resolve()
                active.add(resolved)
                print(f"[poll] processing {Path(batch).name} in the background")
                try:
                    spawn_fn(process_fn, batch, active)
                except Exception:
                    active.discard(resolved)
                    release_batch(batch)
                    raise
        except KeyboardInterrupt:
            stopped()
            return
        except Exception as error:
            print(f"[poll] could not start pipeline ({error}). Will retry.")
        try:
            print(f"[poll] sleeping {seconds:g}s")
            sleep_fn(seconds)
        except KeyboardInterrupt:
            stopped()
            return


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Watch Gmail for unread invoice mail, save attachments, and process them."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Check the inbox once and exit, without processing. The default is to keep polling.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=None,
        help=(
            "Seconds between inbox checks when polling "
            f"(default {DEFAULT_POLL_INTERVAL_SECONDS}, or EMAIL_POLL_INTERVAL)."
        ),
    )
    args = parser.parse_args(argv)
    if args.once:
        download_attachments()
        return 0
    poll_inbox(interval=args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
