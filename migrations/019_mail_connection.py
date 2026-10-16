"""Migration 019: the Gmail connection the dashboard manages.

    ./.venv/bin/python migrations/019_mail_connection.py [--db workflow_platform.db] [--dry-run]

Adds `mail_connection`, one row at most, holding the Gmail address and the App Password
encrypted by mail_connection.py. See gmail-connect-spec.md.

Additive: one table and three triggers, no existing row is touched. The triggers feed the same
dashboard_revision counter as migration 018, on the columns the sidebar shows only, so the
listener recording an unchanged failure does not redraw the page. Reversible by dropping them.
"""

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage import backup_path as make_backup_path  # noqa: E402
from storage import connect, table_exists  # noqa: E402

FROM_VERSION = 18
TARGET_VERSION = 19

DDL = """
CREATE TABLE mail_connection (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    email            TEXT NOT NULL,
    secret_encrypted BLOB NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('connected', 'error')),
    last_error       TEXT,
    connected_by     INTEGER REFERENCES users(user_id),
    connected_at     TEXT NOT NULL
);
CREATE TRIGGER dashboard_mail_connection_insert AFTER INSERT ON mail_connection BEGIN
    UPDATE dashboard_revision SET revision = revision + 1 WHERE id = 1; END;
CREATE TRIGGER dashboard_mail_connection_update AFTER UPDATE OF email, status, last_error
    ON mail_connection BEGIN
    UPDATE dashboard_revision SET revision = revision + 1 WHERE id = 1; END;
CREATE TRIGGER dashboard_mail_connection_delete AFTER DELETE ON mail_connection BEGIN
    UPDATE dashboard_revision SET revision = revision + 1 WHERE id = 1; END;
"""


def current_version(conn):
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return row["v"] if row and row["v"] is not None else 0


def migrate(db_path, dry_run=False):
    if not os.path.exists(db_path):
        print(f"[!] No database at {db_path}. Nothing to migrate.")
        return 1

    with connect(db_path) as conn:
        if not table_exists(conn, "dashboard_revision"):
            print("[!] No 'dashboard_revision' table. Run migration 018 first.")
            return 1
        if table_exists(conn, "mail_connection"):
            print(f"[*] Already applied. Database at version {current_version(conn)}.")
            return 0
        version = current_version(conn)
        if version != FROM_VERSION:
            print(f"[!] Expected schema version {FROM_VERSION}, found {version}. Refusing.")
            return 1

    print(f"=== Migration 019: mail connection in {db_path} ===")
    if dry_run:
        print("[*] Dry run: would add mail_connection and its triggers; no row is rewritten.")
        return 0

    backup = make_backup_path(db_path)
    shutil.copy2(db_path, backup)
    print(f"[*] Backup written to {backup}")

    with connect(db_path) as conn:
        conn.executescript(DDL)
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (TARGET_VERSION,))
        conn.commit()

    print("[+] mail_connection created. Database at version 19.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="workflow_platform.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(migrate(args.db, dry_run=args.dry_run))
