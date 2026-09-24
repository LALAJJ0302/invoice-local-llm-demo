"""Migration 007: prepare the hand-off from approval to post-approval work.

    ./.venv/bin/python migrations/007_post_approval.py [--db workflow_platform.db] [--dry-run]

**Preparation only. Nothing in this codebase contacts Teams, Jira or Planner.**

What was missing: approval terminated. A person clicked Approve, approval_status became
Approved, the open task closed, and nothing followed. In the original eight-phase design,
Phase 5 hands to Phase 6 (assign a task, raise a ticket, notify a channel). Phase 6 has had
no owner since 2026-08-14.

Two changes.

1. task_type gains 'Payment' and 'File'. Approving an Invoice opens a Payment task, because
   the money still has to move. Approving a Receipt opens a File task, because there is
   nothing to pay. An Unknown document opens a Review task, so it reaches a person rather
   than being routed on a guess. Rejection opens nothing: the document was not accepted.

   This is where document_type from migration 006 earns its place. Without it the follow-on
   work could only depend on how confidently the document was read, which is the wrong
   question to ask about what to do next.

2. outbound_messages records what should be sent to an external system. Rows are written
   Pending and stay there. That is the honest state of the project: the decision to notify is
   made and recorded, the transport is not built. A queue visibly holding pending
   notifications demonstrates the workflow; a print statement that has already scrolled past
   demonstrates nothing.

SQLite cannot alter a CHECK constraint, so extending task_type rebuilds the tasks table
following the documented procedure.
"""

import argparse
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import storage  # noqa: E402
from storage import backup_path as make_backup_path  # noqa: E402
from storage import connect, table_exists  # noqa: E402

FROM_VERSION = 6
TARGET_VERSION = 7

TASKS_V7 = """
CREATE TABLE tasks_new (
    task_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id   INTEGER NOT NULL REFERENCES invoices(invoice_id) ON DELETE CASCADE,
    task_type    TEXT    NOT NULL CHECK (task_type IN ('Review','Approve','Fix','Payment','File')),
    reason       TEXT,
    assignee     TEXT,
    state        TEXT    NOT NULL DEFAULT 'Open'
                         CHECK (state IN ('Open','InProgress','Done','Cancelled')),
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    resolved_at  TEXT,
    external_ref TEXT,
    CHECK ((state IN ('Done','Cancelled')) = (resolved_at IS NOT NULL))
);
"""

TASK_COLUMNS = ("task_id, invoice_id, task_type, reason, assignee, state, created_at, "
                "resolved_at, external_ref")

TASK_INDEXES = [
    "CREATE UNIQUE INDEX ux_tasks_one_open ON tasks(invoice_id, task_type) "
    "WHERE state IN ('Open','InProgress')",
    "CREATE INDEX ix_tasks_state ON tasks(state)",
    "CREATE INDEX ix_tasks_invoice ON tasks(invoice_id)",
]

OUTBOX_V7 = """
CREATE TABLE outbound_messages (
    outbox_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      INTEGER REFERENCES tasks(task_id) ON DELETE SET NULL,
    invoice_id   INTEGER NOT NULL REFERENCES invoices(invoice_id) ON DELETE CASCADE,
    channel      TEXT    NOT NULL CHECK (channel IN ('Teams','Jira','Planner','Email')),
    payload      TEXT    NOT NULL,
    state        TEXT    NOT NULL DEFAULT 'Pending'
                         CHECK (state IN ('Pending','Sent','Failed')),
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    sent_at      TEXT,
    external_ref TEXT,
    error        TEXT,
    CHECK ((state = 'Sent') = (sent_at IS NOT NULL))
);

CREATE INDEX ix_outbound_state ON outbound_messages(state);
CREATE INDEX ix_outbound_invoice ON outbound_messages(invoice_id);
"""


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
        if not table_exists(conn, "tasks"):
            print("[!] No 'tasks' table. Run the earlier migrations first.")
            return 1
        version = current_version(conn)
        if table_exists(conn, "outbound_messages"):
            print(f"[*] 'outbound_messages' already exists. Database at version {version}.")
            return 0 if (version is not None and version >= TARGET_VERSION) else 1
        if version != FROM_VERSION:
            print(f"[!] Expected schema version {FROM_VERSION}, found {version}. Refusing to run.")
            return 1
        tasks_before = conn.execute("SELECT COUNT(*) c FROM tasks").fetchone()["c"]
        open_before = conn.execute(
            "SELECT COUNT(*) c FROM tasks WHERE state IN ('Open','InProgress')").fetchone()["c"]

    print(f"=== Migration 007: prepare the post-approval hand-off in {db_path} ===")
    print(f"[*] {tasks_before} tasks ({open_before} open) at schema version {version}.")

    if dry_run:
        print("[*] Dry run: planning only, no writes.\n")
        print("Would extend task_type with 'Payment' and 'File' (rebuilds tasks: SQLite")
        print("cannot alter a CHECK), and create outbound_messages.")
        print("\nNothing would be sent anywhere. Outbox rows are written Pending and stay there.")
        print("\nDry run only. Re-run without --dry-run to apply.")
        return 0

    backup_path = make_backup_path(db_path)
    shutil.copy2(db_path, backup_path)
    print(f"[*] Backed up to {backup_path}")

    conn = connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN")
        conn.executescript(TASKS_V7)
        conn.execute(f"INSERT INTO tasks_new ({TASK_COLUMNS}) SELECT {TASK_COLUMNS} FROM tasks")
        conn.execute("DROP TABLE tasks")
        conn.execute("ALTER TABLE tasks_new RENAME TO tasks")
        for statement in TASK_INDEXES:
            conn.execute(statement)
        conn.executescript(OUTBOX_V7)
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (TARGET_VERSION,))
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"foreign key violations after rebuild: {violations}")
        conn.commit()
    except Exception:
        conn.rollback()
        print(f"[!] Migration failed and was rolled back. Restore from {backup_path} if needed.")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.close()

    with connect(db_path) as conn:
        tasks_after = conn.execute("SELECT COUNT(*) c FROM tasks").fetchone()["c"]
        open_after = conn.execute(
            "SELECT COUNT(*) c FROM tasks WHERE state IN ('Open','InProgress')").fetchone()["c"]
        outbox = conn.execute("SELECT COUNT(*) c FROM outbound_messages").fetchone()["c"]

    print("\n" + "=" * 66)
    print(f"  tasks        {tasks_before} -> {tasks_after}  ({open_before} open -> {open_after} open)")
    print(f"  outbox rows  {outbox}")
    print("=" * 66)

    if (tasks_before, open_before) != (tasks_after, open_after):
        print("[!] Tasks moved during a rebuild that should preserve them. Investigate.")
        return 1
    print("No tasks lost in the rebuild.")
    print("The outbox is empty and every row it ever holds will read Pending: nothing in this")
    print("codebase contacts Teams, Jira or Planner. That is the point, not an oversight.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prepare the post-approval hand-off.")
    parser.add_argument("--db", default=storage.DEFAULT_DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(migrate(args.db, dry_run=args.dry_run))
