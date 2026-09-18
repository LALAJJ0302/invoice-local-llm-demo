"""Migration 008: email bodies, provenance, and attachment-level dedup.

    ./.venv/bin/python migrations/008_email_body_and_attachments.py [--db workflow_platform.db] [--dry-run]

Combines two additive changes that both belonged at version 8:

email_messages.body_text, email_messages.body_source
    Intake and retrieval need the message body, not just the envelope. body_source
    records provenance ('intake' or 'mock') so a result measured over generated text
    is never reported as real correspondence.

email_attachments
    Attachment-level dedup keyed on (email_id, content_sha256), checked by
    has_seen_attachment() before anything is written to disk.

Both are additive. No existing table is rebuilt and no existing value changes.
"""

import argparse
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import storage  # noqa: E402
from storage import connect, table_exists  # noqa: E402

FROM_VERSION = 7
TARGET_VERSION = 8

DDL_V8 = """
CREATE TABLE email_attachments (
    attachment_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id       INTEGER NOT NULL REFERENCES email_messages(email_id) ON DELETE CASCADE,
    filename       TEXT    NOT NULL,
    content_sha256 TEXT    NOT NULL,
    saved_path     TEXT,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (email_id, content_sha256)
);

CREATE INDEX ix_email_attachments_email ON email_attachments(email_id);
"""


def current_version(conn):
    if not table_exists(conn, "schema_version"):
        return None
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return row["v"] if row else None


def column_names(conn, table):
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]


def migrate(db_path, dry_run=False):
    if not os.path.exists(db_path):
        print(f"[!] No database at {db_path}. Nothing to migrate.")
        return 1

    with connect(db_path) as conn:
        if not table_exists(conn, "email_messages"):
            print("[!] No 'email_messages' table. Run the earlier migrations first.")
            return 1

        version = current_version(conn)
        cols = column_names(conn, "email_messages")
        has_body_text = "body_text" in cols
        has_body_source = "body_source" in cols
        has_attachments_table = table_exists(conn, "email_attachments")

        if has_body_text and has_body_source and has_attachments_table:
            print(f"[*] body_text/body_source and email_attachments already exist. "
                  f"Database at version {version}.")
            return 0 if (version is not None and version >= TARGET_VERSION) else 1
        if version != FROM_VERSION:
            print(f"[!] Expected schema version {FROM_VERSION}, found {version}. Refusing to run.")
            return 1

        emails_before = conn.execute("SELECT COUNT(*) c FROM email_messages").fetchone()["c"]

    print(f"=== Migration 008: email bodies and attachment dedup in {db_path} ===")
    print(f"[*] {emails_before} emails at schema version {version}. This migration is additive.")

    if dry_run:
        print("[*] Dry run: planning only, no writes.\n")
        print("Would add to email_messages:")
        print("    body_text    TEXT, NULL where the body was never captured")
        print("    body_source  TEXT CHECK (NULL, 'intake', 'mock')")
        print("Would create:")
        print("    email_attachments     one row per saved attachment, unique on")
        print("                          (email_id, content_sha256)")
        print("\nDry run only. Re-run without --dry-run to apply.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = f"{db_path}.bak-{stamp}"
    shutil.copy2(db_path, backup_path)
    print(f"[*] Backed up to {backup_path}")

    conn = connect(db_path)
    try:
        conn.execute("BEGIN")
        if not has_body_text:
            conn.execute("ALTER TABLE email_messages ADD COLUMN body_text TEXT")
        if not has_body_source:
            conn.execute(
                "ALTER TABLE email_messages ADD COLUMN body_source TEXT "
                "CHECK (body_source IS NULL OR body_source IN ('intake','mock'))")
        if not has_attachments_table:
            conn.executescript(DDL_V8)
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (TARGET_VERSION,))
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"foreign key violations: {violations}")
        conn.commit()
    except Exception:
        conn.rollback()
        print(f"[!] Migration failed and was rolled back. Restore from {backup_path} if needed.")
        raise
    finally:
        conn.close()

    with connect(db_path) as conn:
        emails_after = conn.execute("SELECT COUNT(*) c FROM email_messages").fetchone()["c"]
        attachments = conn.execute("SELECT COUNT(*) c FROM email_attachments").fetchone()["c"]
        with_body = conn.execute(
            "SELECT COUNT(*) c FROM email_messages WHERE body_text IS NOT NULL").fetchone()["c"]

    print("\n" + "=" * 66)
    print(f"  emails       {emails_before} -> {emails_after}")
    print(f"  with a body  {with_body}")
    print(f"  attachments  {attachments} recorded (0 expected: nothing backfills history "
          "that was never captured)")
    print("=" * 66)

    if emails_before != emails_after:
        print("[!] Emails moved during an additive migration. Investigate.")
        return 1
    print("No existing rows changed, as an additive migration requires.")
    print("Existing emails have body_text = NULL until intake or seed_mock_emails.py writes one.")
    print("body_source keeps mock and real correspondence apart in retrieval queries.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Add email bodies and attachment-level dedup.")
    parser.add_argument("--db", default=storage.DEFAULT_DB_PATH, help="path to the SQLite file")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args()
    raise SystemExit(migrate(args.db, dry_run=args.dry_run))
