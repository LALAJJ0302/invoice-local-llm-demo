"""Migration 003: repair the invoice_date CHECK constraint.

    ./.venv/bin/python migrations/003_fix_date_check.py [--db workflow_platform.db] [--dry-run]

The bug: the constraint was written as

    CHECK (invoice_date IS NULL OR invoice_date GLOB '____-__-__')

GLOB has no single-character wildcard. '_' is a literal underscore; the single-character
wildcard '_' belongs to LIKE, and GLOB uses '?'. The constraint therefore only accepted the
literal string '____-__-__' and rejected every real date:

    SELECT '2026-08-10' GLOB '____-__-__'   ->  0
    SELECT '____-__-__' GLOB '____-__-__'   ->  1

It never fired because the extractor returns NULL for every date, so no row has ever carried
one. It would have fired on the first insert after the extraction schema is fixed, which is
work already planned. Found by the storage test suite on the day it was written.

The replacement uses character classes, which also verify the characters are digits:

    GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'

SQLite cannot ALTER a CHECK constraint, so this rebuilds the invoices table following the
procedure in the SQLite docs for schema changes that ALTER TABLE cannot express.
"""

import argparse
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import storage  # noqa: E402
from storage import connect, table_exists  # noqa: E402

FROM_VERSION = 2
TARGET_VERSION = 3

# The version-3 shape of invoices, frozen here for the same reason DDL_V1 is frozen in 001.
INVOICES_V3 = """
CREATE TABLE invoices_new (
    invoice_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            INTEGER NOT NULL REFERENCES processing_runs(run_id),
    file_name         TEXT    NOT NULL,
    source_sha256     TEXT    NOT NULL,
    content_sha256    TEXT    NOT NULL,
    invoice_number    TEXT,
    vendor_name       TEXT,
    invoice_date      TEXT    CHECK (invoice_date IS NULL OR
                                     invoice_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    total_cents       INTEGER CHECK (total_cents IS NULL OR total_cents >= 0),
    currency          TEXT    CHECK (currency IS NULL OR currency = 'Unknown' OR length(currency) = 3),
    validation_score  REAL    NOT NULL CHECK (validation_score BETWEEN 0 AND 1),
    status            TEXT    NOT NULL CHECK (status IN ('Validated','NeedsReview','Failed')),
    total_source      TEXT    NOT NULL DEFAULT 'model'
                              CHECK (total_source IN ('model','fallback','manual')),
    approval_status   TEXT    NOT NULL DEFAULT 'Pending'
                              CHECK (approval_status IN ('Pending','Approved','Rejected')),
    reviewed_at       TEXT,
    archive_path      TEXT,
    raw_json          TEXT,
    processed_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

COLUMNS = """invoice_id, run_id, file_name, source_sha256, content_sha256, invoice_number,
             vendor_name, invoice_date, total_cents, currency, validation_score, status,
             total_source, approval_status, reviewed_at, archive_path, raw_json, processed_at"""

INDEXES = [
    "CREATE UNIQUE INDEX ux_invoices_content ON invoices(content_sha256)",
    "CREATE INDEX ix_invoices_status ON invoices(status)",
    "CREATE INDEX ix_invoices_vendor ON invoices(vendor_name)",
    "CREATE INDEX ix_invoices_run    ON invoices(run_id)",
]


def current_version(conn):
    if not table_exists(conn, "schema_version"):
        return None
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return row["v"] if row else None


def constraint_is_broken(conn):
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='invoices'"
    ).fetchone()["sql"]
    return "'____-__-__'" in sql


def migrate(db_path, dry_run=False):
    if not os.path.exists(db_path):
        print(f"[!] No database at {db_path}. Nothing to migrate.")
        return 1

    with connect(db_path) as conn:
        if not table_exists(conn, "invoices"):
            print("[!] No 'invoices' table. Run the earlier migrations first.")
            return 1
        version = current_version(conn)
        if not constraint_is_broken(conn):
            print(f"[*] The date constraint is already repaired. Database at version {version}.")
            # >= not ==: see the note in 002. Re-running the sequence must be a no-op.
            return 0 if (version is not None and version >= TARGET_VERSION) else 1
        if version != FROM_VERSION:
            print(f"[!] Expected schema version {FROM_VERSION}, found {version}. Refusing to run.")
            return 1
        rows_before = conn.execute("SELECT COUNT(*) c FROM invoices").fetchone()["c"]
        items_before = conn.execute("SELECT COUNT(*) c FROM line_items").fetchone()["c"]
        sum_before = conn.execute("SELECT COALESCE(SUM(total_cents),0) s FROM invoices").fetchone()["s"]

    print(f"=== Migration 003: repair the invoice_date CHECK in {db_path} ===")
    print(f"[*] {rows_before} invoices, {items_before} line items, at schema version {version}.")
    print("[*] Rebuilding 'invoices': SQLite cannot ALTER a CHECK constraint.")

    if dry_run:
        print("[*] Dry run: planning only, no writes.\n")
        print("Would replace:")
        print("    CHECK (invoice_date IS NULL OR invoice_date GLOB '____-__-__')")
        print("with:")
        print("    CHECK (invoice_date IS NULL OR")
        print("           invoice_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]')")
        print("\nDry run only. Re-run without --dry-run to apply.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = f"{db_path}.bak-{stamp}"
    shutil.copy2(db_path, backup_path)
    print(f"[*] Backed up to {backup_path}")

    conn = connect(db_path)
    try:
        # Foreign keys must be off for the drop-and-rename, per the SQLite procedure for
        # schema changes ALTER TABLE cannot express. It is re-enabled and checked below.
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN")
        conn.executescript(INVOICES_V3)
        conn.execute(f"INSERT INTO invoices_new ({COLUMNS}) SELECT {COLUMNS} FROM invoices")
        conn.execute("DROP TABLE invoices")
        conn.execute("ALTER TABLE invoices_new RENAME TO invoices")
        for statement in INDEXES:
            conn.execute(statement)
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
        rows_after = conn.execute("SELECT COUNT(*) c FROM invoices").fetchone()["c"]
        items_after = conn.execute("SELECT COUNT(*) c FROM line_items").fetchone()["c"]
        sum_after = conn.execute("SELECT COALESCE(SUM(total_cents),0) s FROM invoices").fetchone()["s"]
        accepts = conn.execute(
            "SELECT '2026-08-10' GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'").fetchone()[0]

    print("\n" + "=" * 64)
    print("Before / after")
    print("=" * 64)
    print(f"  invoices    {rows_before:>6}  ->  {rows_after:>6}")
    print(f"  line_items  {items_before:>6}  ->  {items_after:>6}")
    print(f"  sum(total)  {sum_before/100:>9,.2f}  ->  {sum_after/100:>9,.2f}")
    print("=" * 64)

    if (rows_before, items_before, sum_before) != (rows_after, items_after, sum_after):
        print("[!] Data moved during a constraint repair. Investigate before continuing.")
        return 1
    print("No rows, line items or totals changed, as a constraint repair requires.")
    print(f"'2026-08-10' now passes the constraint: {'yes' if accepts else 'NO, investigate'}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Repair the invoice_date CHECK constraint.")
    parser.add_argument("--db", default=storage.DEFAULT_DB_PATH, help="path to the SQLite file")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args()
    raise SystemExit(migrate(args.db, dry_run=args.dry_run))
