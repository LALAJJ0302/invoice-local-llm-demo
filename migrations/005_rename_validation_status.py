"""Migration 005: rename invoices.status to validation_status.

    ./.venv/bin/python migrations/005_rename_validation_status.py [--db workflow_platform.db] [--dry-run]

Why: `status` said nothing about whose judgement it holds, which mattered once
`approval_status` arrived beside it. A reader seeing `status = NeedsReview` and
`approval_status = Approved` had no way to tell that the two are independent.

The name `model_result_status` was considered and rejected. The value does not come from
the model. It is computed in ConfidenceValidator:

    status = "Validated" if final_score >= self.threshold else "NeedsReview"

which thresholds a score our own gate calculates from field completeness and substring
matching. Changing the threshold changes every value in this column while the model does
exactly the same work, so attributing it to the model would be wrong, and wrong in the one
direction this project keeps having to correct.

`validation_status` pairs with `validation_score`: the gate produces a score, and a verdict
derived from that score.

SQLite carries the CHECK constraint across a RENAME COLUMN, so no table rebuild is needed.
"""

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import storage  # noqa: E402
from storage import backup_path as make_backup_path  # noqa: E402
from storage import connect, table_exists  # noqa: E402

FROM_VERSION = 4
TARGET_VERSION = 5


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
            print("[!] No 'invoices' table. Run the earlier migrations first.")
            return 1

        version = current_version(conn)
        columns = column_names(conn, "invoices")

        if "validation_status" in columns and "status" not in columns:
            print(f"[*] 'validation_status' already exists. Database at version {version}.")
            # >= not ==, so re-running the whole sequence is a clean no-op.
            return 0 if (version is not None and version >= TARGET_VERSION) else 1

        if version != FROM_VERSION:
            print(f"[!] Expected schema version {FROM_VERSION}, found {version}. Refusing to run.")
            return 1

        before = conn.execute(
            "SELECT status AS s, COUNT(*) AS n FROM invoices GROUP BY 1 ORDER BY 1").fetchall()
        totals_before = conn.execute(
            "SELECT invoice_id, total_cents FROM invoices ORDER BY invoice_id").fetchall()

    print(f"=== Migration 005: rename status -> validation_status in {db_path} ===")
    print(f"[*] {sum(r['n'] for r in before)} invoice rows at schema version {version}.")

    if dry_run:
        print("[*] Dry run: planning only, no writes.\n")
        for row in before:
            print(f"    {row['s']:<12} {row['n']:>3} rows")
        print("\nDry run only. Re-run without --dry-run to apply.")
        return 0

    backup_path = make_backup_path(db_path)
    shutil.copy2(db_path, backup_path)
    print(f"[*] Backed up to {backup_path}")

    conn = connect(db_path)
    try:
        conn.execute("BEGIN")
        conn.execute("ALTER TABLE invoices RENAME COLUMN status TO validation_status")
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (TARGET_VERSION,))
        conn.commit()
    except Exception:
        conn.rollback()
        print(f"[!] Migration failed and was rolled back. Restore from {backup_path} if needed.")
        raise
    finally:
        conn.close()

    with connect(db_path) as conn:
        after = conn.execute(
            "SELECT validation_status AS s, COUNT(*) AS n FROM invoices GROUP BY 1 ORDER BY 1"
        ).fetchall()
        totals_after = conn.execute(
            "SELECT invoice_id, total_cents FROM invoices ORDER BY invoice_id").fetchall()
        constraint = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='invoices'").fetchone()["sql"]

    print("\n" + "=" * 62)
    print("Value distribution")
    print("=" * 62)
    for row in after:
        print(f"  {row['s']:<12} {row['n']:>3} rows")
    print("=" * 62)

    if [dict(r) for r in totals_before] != [dict(r) for r in totals_after]:
        print("[!] Totals changed, which must never happen in a rename.")
        return 1
    print(f"{len(totals_after)} totals unchanged, as a rename requires.")

    flat = " ".join(constraint.split())
    kept = "CHECK (validation_status IN ('Validated','NeedsReview','Failed'))" in flat
    print(f"CHECK constraint carried across the rename: {'yes' if kept else 'NO, investigate'}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rename status to validation_status.")
    parser.add_argument("--db", default=storage.DEFAULT_DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(migrate(args.db, dry_run=args.dry_run))
