"""Migration 018: one reliable revision for dashboard live refresh.

    ./.venv/bin/python migrations/018_dashboard_revision.py [--db workflow_platform.db] [--dry-run]

COUNT/MAX fingerprints miss in-place updates such as Pending -> Failed. This migration adds
one counter and SQLite triggers that increment it whenever dashboard-visible data changes.
"""

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage import backup_path as make_backup_path  # noqa: E402
from storage import connect, table_exists  # noqa: E402

FROM_VERSION = 17
TARGET_VERSION = 18

TABLES = (
    "processing_runs", "email_messages", "invoices", "line_items", "tasks",
    "outbound_messages", "invoice_decisions", "invoice_action_items", "ingestion_failures",
)


def revision_ddl():
    statements = [
        "CREATE TABLE dashboard_revision ("
        "id INTEGER PRIMARY KEY CHECK (id = 1), revision INTEGER NOT NULL DEFAULT 0)",
        "INSERT INTO dashboard_revision (id, revision) VALUES (1, 0)",
    ]
    for table in TABLES:
        stem = "invoice_actions" if table == "invoice_action_items" else table
        for operation in ("insert", "update", "delete"):
            statements.append(
                f"CREATE TRIGGER dashboard_{stem}_{operation} AFTER {operation.upper()} "
                f"ON {table} BEGIN UPDATE dashboard_revision SET revision = revision + 1 "
                "WHERE id = 1; END"
            )
    return ";\n".join(statements) + ";"


def current_version(conn):
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return row["v"] if row and row["v"] is not None else 0


def migrate(db_path, dry_run=False):
    if not os.path.exists(db_path):
        print(f"[!] No database at {db_path}. Nothing to migrate.")
        return 1

    with connect(db_path) as conn:
        if not table_exists(conn, "invoices"):
            print(f"[!] No 'invoices' table. Run migration 001 first.")
            return 1
        if table_exists(conn, "dashboard_revision"):
            print(f"[*] Already applied. Database at version {current_version(conn)}.")
            return 0
        version = current_version(conn)
        if version != FROM_VERSION:
            print(f"[!] Expected schema version {FROM_VERSION}, found {version}. Refusing.")
            return 1

    print(f"=== Migration 018: dashboard revision in {db_path} ===")
    if dry_run:
        print("[*] Dry run: would add dashboard_revision and change triggers; no row is rewritten.")
        return 0

    backup = make_backup_path(db_path)
    shutil.copy2(db_path, backup)
    print(f"[*] Backup written to {backup}")

    with connect(db_path) as conn:
        conn.executescript(revision_ddl())
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (TARGET_VERSION,))
        conn.commit()

    print("[+] dashboard_revision and triggers created. Database at version 18.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="workflow_platform.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(migrate(args.db, dry_run=args.dry_run))
