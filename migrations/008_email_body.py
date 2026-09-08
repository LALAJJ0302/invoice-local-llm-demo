"""Migration 008: give email_messages somewhere to keep the message body.

    ./.venv/bin/python migrations/008_email_body.py [--db workflow_platform.db] [--dry-run]

What was missing. `email_messages` recorded who sent a message, when, its subject and how
many attachments it carried, and threw the message away. That was enough for its original
job, which was deduplication on the Message-ID.

It is not enough for the job assigned on 2026-09-08: a historical email retrieval module
supplying context to the extraction step. **Context lives in the body.** A retrieval module
restricted to sender and subject can answer "has this vendor written before" and nothing
else. It cannot find the earlier message that explains a disputed line item, which is the
case that makes retrieval worth building.

Two columns, both additive. Nothing existing moves, and no row is rewritten.

  body_text     the plain-text body, NULL when not captured
  body_source   where it came from: 'intake', 'mock', or NULL

body_source exists for the same reason `total_source` does. Bodies now arrive from a mock
mailbox and later from real intake, and a retrieval result computed over generated text
must not be reported as though it came from real correspondence. A column that records
provenance makes that distinguishable in a query rather than in someone's memory.

Deliberately NOT added: an embedding column, or a vector index. Whether this project needs
embeddings at all is an open question at n=18, where SQL and keyword matching may well win.
Adding the column now would prejudge a measurement that has not been taken.

Reversal: ALTER TABLE ... DROP COLUMN twice, then set the version back to 7.
"""

import argparse
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage import connect, table_exists  # noqa: E402

FROM_VERSION = 7
TARGET_VERSION = 8


def current_version(conn):
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return row["v"] if row and row["v"] is not None else 0


def columns(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def migrate(db_path, dry_run=False):
    if not os.path.exists(db_path):
        print(f"[!] No database at {db_path}. Nothing to migrate.")
        return 1

    with connect(db_path) as conn:
        if not table_exists(conn, "email_messages"):
            print("[!] No 'email_messages' table. Run migration 004 first.")
            return 1

        version = current_version(conn)
        existing = columns(conn, "email_messages")

        if "body_text" in existing:
            print(f"[*] 'body_text' already exists. Database at version {version}.")
            return 0

        if version != FROM_VERSION:
            print(f"[!] Expected schema version {FROM_VERSION}, found {version}. Refusing to run.")
            return 1

        rows_before = conn.execute("SELECT COUNT(*) FROM email_messages").fetchone()[0]

    print(f"=== Migration 008: add the email body to {db_path} ===")
    print(f"[*] {rows_before} emails at schema version {version}.")

    if dry_run:
        print("[*] Dry run: planning only, no writes.\n")
        print("Would add two columns to email_messages:")
        print("  body_text    TEXT, NULL where the body was never captured")
        print("  body_source  TEXT CHECK (NULL, 'intake', 'mock')")
        print("\nAdditive. No existing column moves and no row is rewritten.")
        print("\nDry run only. Re-run without --dry-run to apply.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = f"{db_path}.bak-{stamp}"
    shutil.copy2(db_path, backup_path)
    print(f"[*] Backed up to {backup_path}")

    try:
        with connect(db_path) as conn:
            conn.execute("ALTER TABLE email_messages ADD COLUMN body_text TEXT")
            conn.execute(
                "ALTER TABLE email_messages ADD COLUMN body_source TEXT "
                "CHECK (body_source IS NULL OR body_source IN ('intake','mock'))")
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (TARGET_VERSION,))
            conn.commit()
    except Exception as error:                              # noqa: BLE001
        print(f"[!] Migration failed: {error}")
        print(f"[!] Nothing was committed. Restore from {backup_path} if needed.")
        return 1

    with connect(db_path) as conn:
        after = columns(conn, "email_messages")
        rows_after = conn.execute("SELECT COUNT(*) FROM email_messages").fetchone()[0]
        version_after = current_version(conn)
        with_body = conn.execute(
            "SELECT COUNT(*) FROM email_messages WHERE body_text IS NOT NULL").fetchone()[0]

    print("\n" + "=" * 66)
    print(f"  emails        {rows_before} -> {rows_after}")
    print(f"  columns       {len(after) - 2} -> {len(after)}")
    print(f"  with a body   {with_body}")
    print(f"  version       {version} -> {version_after}")
    print("=" * 66)

    if rows_after != rows_before:
        print("[!] Row count changed during an additive migration. Investigate.")
        return 1

    print("No rows changed. Every body is NULL until something writes one:")
    print("seed_mock_emails.py writes 'mock', and intake will write 'intake'.")
    print("body_source keeps those apart, so a retrieval result measured over generated")
    print("text is never reported as though it came from real correspondence.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Add the email body to email_messages.")
    parser.add_argument("--db", default="workflow_platform.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(migrate(args.db, dry_run=args.dry_run))
