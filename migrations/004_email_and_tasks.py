"""Migration 004: add email_messages and tasks, and link invoices to the email that delivered them.

    ./.venv/bin/python migrations/004_email_and_tasks.py [--db workflow_platform.db] [--dry-run]

Why these two tables:

email_messages
    email_listener.py currently writes nothing to the database. It drops attachments into
    inbox/ and exits, so a stored invoice cannot say who sent it, under what subject, or
    when. Keying on the RFC 5322 Message-ID also gives intake the duplicate protection it
    lacks today (FR-1.3 in requirements-spec.md).

tasks
    DownstreamDispatcher prints fake Teams and Jira lines. Nothing is recorded, so the
    question "how many documents are waiting for a person" cannot be answered at all. The
    trigger condition already lives in the data as status = 'NeedsReview' and
    approval_status = 'Pending'; what was missing was somewhere to write the task down.

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

FROM_VERSION = 3
TARGET_VERSION = 4

# Frozen at the version-4 shape, for the same reason 001 freezes its DDL.
DDL_V4 = """
CREATE TABLE email_messages (
    email_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id       TEXT    NOT NULL UNIQUE,
    sender           TEXT    NOT NULL,
    subject          TEXT,
    received_at      TEXT,
    fetched_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    attachment_count INTEGER NOT NULL DEFAULT 0 CHECK (attachment_count >= 0)
);

CREATE INDEX ix_email_sender ON email_messages(sender);

CREATE TABLE tasks (
    task_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id   INTEGER NOT NULL REFERENCES invoices(invoice_id) ON DELETE CASCADE,
    task_type    TEXT    NOT NULL CHECK (task_type IN ('Review','Approve','Fix')),
    reason       TEXT,
    assignee     TEXT,
    state        TEXT    NOT NULL DEFAULT 'Open'
                         CHECK (state IN ('Open','InProgress','Done','Cancelled')),
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    resolved_at  TEXT,
    external_ref TEXT,
    CHECK ((state IN ('Done','Cancelled')) = (resolved_at IS NOT NULL))
);

CREATE UNIQUE INDEX ux_tasks_one_open ON tasks(invoice_id, task_type)
    WHERE state IN ('Open','InProgress');

CREATE INDEX ix_tasks_state ON tasks(state);
CREATE INDEX ix_tasks_invoice ON tasks(invoice_id);
"""

# ADD COLUMN with a REFERENCES clause is allowed only when the default is NULL, which it is.
ADD_EMAIL_LINK = "ALTER TABLE invoices ADD COLUMN email_id INTEGER REFERENCES email_messages(email_id)"


def current_version(conn):
    if not table_exists(conn, "schema_version"):
        return None
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return row["v"] if row else None


def migrate(db_path, dry_run=False):
    if not os.path.exists(db_path):
        print(f"[!] No database at {db_path}. Nothing to migrate.")
        return 1

    with connect(db_path) as conn:
        if not table_exists(conn, "invoices"):
            print("[!] No 'invoices' table. Run the earlier migrations first.")
            return 1

        version = current_version(conn)
        if table_exists(conn, "tasks") and table_exists(conn, "email_messages"):
            print(f"[*] email_messages and tasks already exist. Database at version {version}.")
            # >= not ==: see the note in 002. Re-running the sequence must be a no-op.
            return 0 if (version is not None and version >= TARGET_VERSION) else 1
        if version != FROM_VERSION:
            print(f"[!] Expected schema version {FROM_VERSION}, found {version}. Refusing to run.")
            return 1

        invoices_before = conn.execute("SELECT COUNT(*) c FROM invoices").fetchone()["c"]
        sum_before = conn.execute("SELECT COALESCE(SUM(total_cents),0) s FROM invoices").fetchone()["s"]

    print(f"=== Migration 004: add email_messages and tasks to {db_path} ===")
    print(f"[*] {invoices_before} invoices at schema version {version}. This migration is additive.")

    if dry_run:
        print("[*] Dry run: planning only, no writes.\n")
        print("Would create:")
        print("    email_messages   one row per fetched email, keyed on the RFC 5322 Message-ID")
        print("    tasks            one row per unit of human work, with at most one live")
        print("                     task of each type per invoice")
        print("Would add:")
        print("    invoices.email_id  nullable FK, NULL means the file was not delivered by email")
        print("\nDry run only. Re-run without --dry-run to apply.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = f"{db_path}.bak-{stamp}"
    shutil.copy2(db_path, backup_path)
    print(f"[*] Backed up to {backup_path}")

    conn = connect(db_path)
    try:
        conn.execute("BEGIN")
        conn.executescript(DDL_V4)
        conn.execute(ADD_EMAIL_LINK)
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
        invoices_after = conn.execute("SELECT COUNT(*) c FROM invoices").fetchone()["c"]
        sum_after = conn.execute("SELECT COALESCE(SUM(total_cents),0) s FROM invoices").fetchone()["s"]
        linked = conn.execute(
            "SELECT COUNT(*) c FROM invoices WHERE email_id IS NOT NULL").fetchone()["c"]
        tables = sorted(r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))

    print("\n" + "=" * 66)
    print("After")
    print("=" * 66)
    print(f"  tables      {', '.join(tables)}")
    print(f"  invoices    {invoices_before} -> {invoices_after}")
    print(f"  sum(total)  {sum_before/100:,.2f} -> {sum_after/100:,.2f}")
    print(f"  linked to an email  {linked} of {invoices_after}")
    print("=" * 66)

    if (invoices_before, sum_before) != (invoices_after, sum_after):
        print("[!] Data moved during an additive migration. Investigate.")
        return 1
    print("No existing rows or totals changed, as an additive migration requires.")
    print("Existing invoices have email_id = NULL: they predate intake recording emails,")
    print("and NULL correctly means 'not known to have arrived by email'.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Add email_messages and tasks.")
    parser.add_argument("--db", default=storage.DEFAULT_DB_PATH, help="path to the SQLite file")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args()
    raise SystemExit(migrate(args.db, dry_run=args.dry_run))
