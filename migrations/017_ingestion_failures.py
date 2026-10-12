"""Migration 017: remember a file the pipeline could not store.

    ./.venv/bin/python migrations/017_ingestion_failures.py [--db workflow_platform.db] [--dry-run]

A scanned PDF has no text layer. The pipeline used to skip it and the listener put it back
in inbox/, so the next poll claimed it again and opened another processing run. This table
is the record of that failure: one row per file-byte hash, with the reason and how many
times it has been tried.

The key is the file bytes, not the extracted text. An empty text layer would make every
scanned PDF share one hash.

Additive: one new table, no existing row rewritten.
"""

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage import backup_path as make_backup_path  # noqa: E402
from storage import connect, table_exists  # noqa: E402

FROM_VERSION = 16
TARGET_VERSION = 17

DDL = """
CREATE TABLE ingestion_failures (
    failure_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name       TEXT    NOT NULL,
    source_sha256   TEXT    NOT NULL UNIQUE,
    status          TEXT    NOT NULL
                    CHECK (status IN ('needs_ocr','unsupported','retryable_failure','retry_exhausted')),
    reason          TEXT    NOT NULL,
    attempt_count   INTEGER NOT NULL CHECK (attempt_count >= 1),
    first_seen_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    last_attempt_at TEXT    NOT NULL DEFAULT (datetime('now')),
    resolved_at     TEXT
);
CREATE INDEX ix_ingestion_failures_status ON ingestion_failures(status);
"""


def current_version(conn):
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return row["v"] if row and row["v"] is not None else 0


def migrate(db_path, dry_run=False):
    if not os.path.exists(db_path):
        print(f"[!] No database at {db_path}. Nothing to migrate.")
        return 1

    with connect(db_path) as conn:
        if not table_exists(conn, "invoices"):
            print("[!] No 'invoices' table. Run migration 001 first.")
            return 1
        if table_exists(conn, "ingestion_failures"):
            print(f"[*] Already applied. Database at version {current_version(conn)}.")
            return 0
        version = current_version(conn)
        if version != FROM_VERSION:
            print(f"[!] Expected schema version {FROM_VERSION}, found {version}. Refusing.")
            return 1
        invoices_before = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]

    print(f"=== Migration 017: ingestion failures in {db_path} ===")
    print(f"[*] {invoices_before} invoices at schema version {version}.")

    if dry_run:
        print("[*] Dry run: planning only, no writes.\n")
        print("Would create ingestion_failures. No existing row is rewritten.")
        return 0

    backup = make_backup_path(db_path)
    shutil.copy2(db_path, backup)
    print(f"[*] Backup written to {backup}")

    with connect(db_path) as conn:
        conn.executescript(DDL)
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (TARGET_VERSION,))
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]

    assert after == invoices_before, f"row count changed: {invoices_before} -> {after}"
    print("[+] ingestion_failures created.")
    print(f"[*] {after} invoices, unchanged. Database at version {TARGET_VERSION}.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="workflow_platform.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(migrate(args.db, dry_run=args.dry_run))
