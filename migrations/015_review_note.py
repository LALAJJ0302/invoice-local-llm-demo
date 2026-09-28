"""Migration 015: a note an approver leaves for whoever opens the document next.

    ./.venv/bin/python migrations/015_review_note.py [--db workflow_platform.db] [--dry-run]

Numbered 015 and not 013. It was written as 013 against a main branch that did not yet have one,
and by the time it was merged Luke's 013_reviewer_login.py and 014_must_change_password.py were
already applied on main. CLAUDE.md's rule decides which one moves: never renumber a migration
that is already on main, because a renumbered migration refuses to run on any database that
already has the original. Luke's stay, this one moved, and it now starts at 14 rather than 12.

Replaces a control that did nothing useful. `invoice_action_items.is_done` is written by a
checkbox in the review dialog and read by nothing: not the validation score, not the approval,
not the Jira task. It was a tick box whose only effect was to be ticked. The actions it hangs
off are worth showing, because the evidence quote beside each one exposes how generic they are,
but marking them done answered a question nobody was asking.

What an approver actually needs to leave behind is a sentence. "Chased the vendor about the
missing line items" is worth more to the next person than three ticks.

Two nullable columns rather than a `review_notes` table, because one note per document is what
was asked for and a table would carry an author column this project has no concept of: there is
no sign-in, and `reviewed_at` is the only trace a person leaves anywhere. If notes ever need a
history, that is a later migration and this column becomes its first row.

`is_done` is left in place and stops being written. Dropping a column rewrites the table, and
the data is small but it is still a record of what someone ticked. Removing it is a later
decision, the same call migration 012 made about `attachment_names`.

Additive: two nullable columns, no existing row rewritten.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage import backup_path as make_backup_path  # noqa: E402
from storage import connect, table_exists  # noqa: E402

FROM_VERSION = 14
TARGET_VERSION = 15

INVOICE_COLUMNS = [
    ("review_note", "TEXT"),
    ("review_note_at", "TEXT"),
]


def current_version(conn):
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return row["v"] if row and row["v"] is not None else 0


def column_names(conn, table):
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]


def migrate(db_path, dry_run=False):
    if not os.path.exists(db_path):
        print(f"[!] No database at {db_path}. Nothing to migrate.")
        return 1

    with connect(db_path) as conn:
        if not table_exists(conn, "invoices"):
            print("[!] No 'invoices' table. Run migration 001 first.")
            return 1
        existing = column_names(conn, "invoices")
        if all(name in existing for name, _ in INVOICE_COLUMNS):
            print(f"[*] Already applied. Database at version {current_version(conn)}.")
            return 0
        version = current_version(conn)
        if version != FROM_VERSION:
            print(f"[!] Expected schema version {FROM_VERSION}, found {version}. Refusing.")
            return 1
        invoices_before = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]

    print(f"=== Migration 015: the review note in {db_path} ===")
    print(f"[*] {invoices_before} invoices at schema version {version}.")

    if dry_run:
        print("[*] Dry run: planning only, no writes.\n")
        print("Would add two nullable columns to invoices: review_note and review_note_at.")
        print("Every existing row keeps NULL. invoice_action_items.is_done is left alone.")
        return 0

    backup = make_backup_path(db_path)
    import shutil
    shutil.copy2(db_path, backup)
    print(f"[*] Backup written to {backup}")

    with connect(db_path) as conn:
        for name, sql_type in INVOICE_COLUMNS:
            if name not in column_names(conn, "invoices"):
                conn.execute(f"ALTER TABLE invoices ADD COLUMN {name} {sql_type}")
                print(f"[+] invoices.{name} added")
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (TARGET_VERSION,))
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]

    assert after == invoices_before, f"row count changed: {invoices_before} -> {after}"
    print(f"[*] {after} invoices, unchanged. Database at version {TARGET_VERSION}.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="workflow_platform.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(migrate(args.db, dry_run=args.dry_run))
