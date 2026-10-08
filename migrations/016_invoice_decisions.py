"""Migration 016: every decision a person makes, kept rather than overwritten.

    ./.venv/bin/python migrations/016_invoice_decisions.py [--db workflow_platform.db] [--dry-run]

Until now a decision lived in three columns on `invoices`: approval_status, reviewed_at and
reviewed_by. That was enough while a decision was final. It stops being enough the moment a
person can reopen a document for review, which the History tab now allows: approving, reopening
and rejecting the same invoice would leave only the rejection, and the approval that came first
would be gone with no trace that it ever happened.

So the decisions move into their own table, one row per decision, appended and never updated.
The three columns on `invoices` stay and keep meaning "the decision in force now", because the
queue, the system-approved tab and the pipeline all read them and none of that needs to change.

Three kinds of row. Approved and Rejected are what they were. Reopened is new: a person taking a
decision back and returning the document to the queue. It is recorded against that person, the
same as the other two, because taking a decision back is itself a decision.

`validation_score` and `validation_status` are copied onto each row at the moment of deciding.
History used to show the score the invoice carries now, which a re-run of main.py can change,
so a decision could end up displayed beside evidence it was never made against.

Backfill: every invoice with `reviewed_at` set gets one row, carrying its current
approval_status, reviewed_by and reviewed_at. Those are the only decisions the database knows
about. An auto-approval has `reviewed_at` NULL and is not a person's decision, so it gets none.

Additive: one new table, no existing row rewritten.
"""

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage import backup_path as make_backup_path  # noqa: E402
from storage import connect, table_exists  # noqa: E402

FROM_VERSION = 15
TARGET_VERSION = 16

DDL = """
CREATE TABLE invoice_decisions (
    decision_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id        INTEGER NOT NULL REFERENCES invoices(invoice_id) ON DELETE CASCADE,
    decision          TEXT    NOT NULL CHECK (decision IN ('Approved','Rejected','Reopened')),
    decided_by        INTEGER REFERENCES users(user_id),
    decided_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    validation_score  REAL,
    validation_status TEXT
);
CREATE INDEX ix_invoice_decisions_invoice ON invoice_decisions(invoice_id, decided_at);
"""

BACKFILL = """
INSERT INTO invoice_decisions
    (invoice_id, decision, decided_by, decided_at, validation_score, validation_status)
SELECT invoice_id, approval_status, reviewed_by, reviewed_at, validation_score, validation_status
FROM invoices
WHERE reviewed_at IS NOT NULL AND approval_status IN ('Approved', 'Rejected')
ORDER BY reviewed_at, invoice_id
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
        if table_exists(conn, "invoice_decisions"):
            print(f"[*] Already applied. Database at version {current_version(conn)}.")
            return 0
        version = current_version(conn)
        if version != FROM_VERSION:
            print(f"[!] Expected schema version {FROM_VERSION}, found {version}. Refusing.")
            return 1
        invoices_before = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
        decided = conn.execute(
            "SELECT COUNT(*) FROM invoices WHERE reviewed_at IS NOT NULL "
            "AND approval_status IN ('Approved', 'Rejected')").fetchone()[0]

    print(f"=== Migration 016: the decision log in {db_path} ===")
    print(f"[*] {invoices_before} invoices at schema version {version}, "
          f"{decided} of them decided by a person.")

    if dry_run:
        print("[*] Dry run: planning only, no writes.\n")
        print("Would create invoice_decisions and copy one row per decided invoice into it.")
        return 0

    backup = make_backup_path(db_path)
    shutil.copy2(db_path, backup)
    print(f"[*] Backup written to {backup}")

    with connect(db_path) as conn:
        conn.executescript(DDL)
        conn.execute(BACKFILL)
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (TARGET_VERSION,))
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
        logged = conn.execute("SELECT COUNT(*) FROM invoice_decisions").fetchone()[0]

    assert after == invoices_before, f"row count changed: {invoices_before} -> {after}"
    assert logged == decided, f"backfilled {logged} decisions, expected {decided}"
    print(f"[+] invoice_decisions created, {logged} decisions copied in.")
    print(f"[*] {after} invoices, unchanged. Database at version {TARGET_VERSION}.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="workflow_platform.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(migrate(args.db, dry_run=args.dry_run))
