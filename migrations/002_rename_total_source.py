"""Migration 002: rename invoices.extraction_source to total_source.

Standalone. Never imported by the pipeline, never run on import.

    ./.venv/bin/python migrations/002_rename_total_source.py [--db workflow_platform.db] [--dry-run]

Why: the column is written in exactly one place, the total recovery path, so 'fallback'
means *the total* was derived from line items. It says nothing about the vendor name, the
invoice number or the date. On the current data all three rows read 'fallback' while
vendor_name came straight from the model, so the old name misleads on real data.

SQLite carries the CHECK constraint across a RENAME COLUMN, so no table rebuild is needed
and the recovered totals are never touched.
"""

import argparse
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import storage  # noqa: E402
from storage import connect, table_exists  # noqa: E402

FROM_VERSION = 1
TARGET_VERSION = 2


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
        if not table_exists(conn, "invoices"):
            print("[!] No 'invoices' table. Run migrations/001_normalise.py first.")
            return 1

        version = current_version(conn)
        columns = column_names(conn, "invoices")

        if "total_source" in columns and "extraction_source" not in columns:
            print(f"[*] 'total_source' already exists. This database is at version {version}.")
            # >= not ==: a database that has moved on to a later migration has still had
            # this one applied, so re-running the whole sequence must be a clean no-op.
            if version is None or version < TARGET_VERSION:
                print(f"[!] But schema_version reads {version}, expected at least "
                      f"{TARGET_VERSION}. The rename was applied without recording it.")
                return 1
            return 0

        if version != FROM_VERSION:
            print(f"[!] Expected schema version {FROM_VERSION}, found {version}. Refusing to run.")
            return 1

        before = conn.execute(
            "SELECT extraction_source AS src, COUNT(*) AS n FROM invoices GROUP BY 1 ORDER BY 1"
        ).fetchall()
        totals_before = conn.execute(
            "SELECT invoice_id, file_name, total_cents FROM invoices ORDER BY invoice_id"
        ).fetchall()

    print(f"=== Migration 002: rename extraction_source -> total_source in {db_path} ===")
    print(f"[*] {sum(r['n'] for r in before)} invoice rows at schema version {version}.")

    if dry_run:
        print("[*] Dry run: planning only, no writes.")
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = f"{db_path}.bak-{stamp}"
        shutil.copy2(db_path, backup_path)
        print(f"[*] Backed up to {backup_path}")

        conn = connect(db_path)
        try:
            conn.execute("BEGIN")
            conn.execute("ALTER TABLE invoices RENAME COLUMN extraction_source TO total_source")
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (TARGET_VERSION,))
            conn.commit()
        except Exception:
            conn.rollback()
            print("[!] Migration failed and was rolled back. The database is unchanged.")
            raise
        finally:
            conn.close()

    # Report, and prove the totals did not move.
    with connect(db_path) as conn:
        column = "total_source" if not dry_run else "extraction_source"
        after = conn.execute(
            f"SELECT {column} AS src, COUNT(*) AS n FROM invoices GROUP BY 1 ORDER BY 1"
        ).fetchall()
        totals_after = conn.execute(
            "SELECT invoice_id, file_name, total_cents FROM invoices ORDER BY invoice_id"
        ).fetchall()
        constraint = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='invoices'"
        ).fetchone()["sql"]

    print("\n" + "=" * 62)
    print("Value distribution" + (" (dry run)" if dry_run else ""))
    print("=" * 62)
    for row in after:
        print(f"  {row['src']:<12} {row['n']:>3} rows")

    moved = [
        (b["file_name"], b["total_cents"], a["total_cents"])
        for b, a in zip(totals_before, totals_after)
        if b["total_cents"] != a["total_cents"]
    ]
    print("=" * 62)
    if moved:
        print("[!] Totals changed, which must never happen in a rename:")
        for name, b, a in moved:
            print(f"    {name}: {b} -> {a}")
        return 1
    print(f"{len(totals_after)} totals unchanged, as a rename requires.")

    kept = "CHECK (total_source IN ('model','fallback','manual'))" in constraint.replace("\n", " ").replace("  ", " ")
    if not dry_run:
        print(f"CHECK constraint carried across the rename: {'yes' if kept else 'NO, investigate'}")

    if dry_run:
        print("\nDry run only. Re-run without --dry-run to apply.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rename extraction_source to total_source.")
    parser.add_argument("--db", default=storage.DEFAULT_DB_PATH, help="path to the SQLite file")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args()
    raise SystemExit(migrate(args.db, dry_run=args.dry_run))
