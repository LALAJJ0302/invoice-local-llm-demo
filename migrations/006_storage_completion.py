"""Migration 006: reconciliation, vendor_source and document_type on invoices.

    ./.venv/bin/python migrations/006_storage_completion.py [--db workflow_platform.db] [--dry-run]

Closes the three remaining gaps in the storage scope. All additive; no table rebuild.

reconciliation
    We hold the invoice total twice, read two ways: from a total line, and as the sum of the
    line items. Nothing compared them. Measured 2026-08-30, required extraction fields make
    the model invent a total on a document that states none: given a statement of account
    reading "Opening balance 1,200.00 / Payments received 800.00", it returned 2000.0. It did
    not copy a wrong number, it computed a plausible one, and proximity checking cannot catch
    that because the model can point at real numbers on the page. Arithmetic can.

vendor_source
    total_cents records whether it came from the model or was derived. vendor_name does not,
    even though the extractor can silently replace a model answer with a filename guess. The
    column ships reading 'model' on every row until the extractor reports its path, which is
    a change in JJ's lane. Added now because it is free during a migration already in flight.

document_type
    An invoice is unpaid and needs approval; a receipt is already paid and needs filing. The
    schema could not tell them apart, so the work that follows approval could only depend on
    how confidently a document was read, which is the wrong question. Migration 007 uses this.

Backfill: reconciliation and document_type are recomputed for existing rows from data already
stored. vendor_source takes its default.
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import storage  # noqa: E402
from storage import classify_document, connect, normalise_items, reconcile, table_exists  # noqa: E402

FROM_VERSION = 5
TARGET_VERSION = 6

COLUMNS = [
    ("vendor_source", "TEXT NOT NULL DEFAULT 'model' "
                      "CHECK (vendor_source IN ('model','fallback','manual'))"),
    ("document_type", "TEXT NOT NULL DEFAULT 'Unknown' "
                      "CHECK (document_type IN ('Invoice','Receipt','Unknown'))"),
    ("reconciliation", "TEXT NOT NULL DEFAULT 'unknown' "
                       "CHECK (reconciliation IN ('exact','plausible','short','unknown'))"),
]


def current_version(conn):
    if not table_exists(conn, "schema_version"):
        return None
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return row["v"] if row else None


def column_names(conn, table):
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]


def archived_text(archive_path):
    """The document text, for classification. Empty when the file is gone."""
    if not archive_path or not os.path.exists(archive_path):
        return ""
    try:
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(archive_path).pages)
    except Exception:
        return ""


def migrate(db_path, dry_run=False):
    if not os.path.exists(db_path):
        print(f"[!] No database at {db_path}. Nothing to migrate.")
        return 1

    with connect(db_path) as conn:
        if not table_exists(conn, "invoices"):
            print("[!] No 'invoices' table. Run the earlier migrations first.")
            return 1
        version = current_version(conn)
        existing = column_names(conn, "invoices")

        if all(name in existing for name, _ in COLUMNS):
            print(f"[*] Columns already present. Database at version {version}.")
            return 0 if (version is not None and version >= TARGET_VERSION) else 1
        if version != FROM_VERSION:
            print(f"[!] Expected schema version {FROM_VERSION}, found {version}. Refusing to run.")
            return 1

        rows = [dict(r) for r in conn.execute(
            "SELECT invoice_id, file_name, total_cents, raw_json, archive_path FROM invoices")]
        sum_before = conn.execute("SELECT COALESCE(SUM(total_cents),0) s FROM invoices").fetchone()["s"]

    print(f"=== Migration 006: finish the storage scope in {db_path} ===")
    print(f"[*] {len(rows)} invoices at schema version {version}. Additive, no rebuild.")

    # Work out the backfill before writing anything, so a dry run reports the real answer.
    plan = []
    for row in rows:
        try:
            items = normalise_items(json.loads(row["raw_json"] or "{}").get("items"))
        except (TypeError, ValueError):
            items = []
        plan.append({
            "invoice_id": row["invoice_id"],
            "file_name": row["file_name"],
            "reconciliation": reconcile(row["total_cents"], items),
            "document_type": classify_document(archived_text(row["archive_path"])),
        })

    print("\n" + "=" * 78)
    print(f"{'File':<36} | {'reconciliation':<14} | document_type")
    print("-" * 78)
    for p in plan:
        print(f"{p['file_name'][:36]:<36} | {p['reconciliation']:<14} | {p['document_type']}")
    print("=" * 78)

    if dry_run:
        print("\nDry run only. Re-run without --dry-run to apply.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = f"{db_path}.bak-{stamp}"
    shutil.copy2(db_path, backup_path)
    print(f"[*] Backed up to {backup_path}")

    conn = connect(db_path)
    try:
        conn.execute("BEGIN")
        for name, definition in COLUMNS:
            conn.execute(f"ALTER TABLE invoices ADD COLUMN {name} {definition}")
        for p in plan:
            conn.execute("UPDATE invoices SET reconciliation = ?, document_type = ? "
                         "WHERE invoice_id = ?",
                         (p["reconciliation"], p["document_type"], p["invoice_id"]))
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (TARGET_VERSION,))
        conn.commit()
    except Exception:
        conn.rollback()
        print(f"[!] Migration failed and was rolled back. Restore from {backup_path} if needed.")
        raise
    finally:
        conn.close()

    with connect(db_path) as conn:
        sum_after = conn.execute("SELECT COALESCE(SUM(total_cents),0) s FROM invoices").fetchone()["s"]
        counts = {r["reconciliation"]: r["n"] for r in conn.execute(
            "SELECT reconciliation, COUNT(*) n FROM invoices GROUP BY 1")}

    if sum_before != sum_after:
        print("[!] Totals moved during an additive migration. Investigate.")
        return 1
    print(f"\nTotals unchanged at {sum_after / 100:,.2f}, as an additive migration requires.")
    print(f"Reconciliation: {counts}")
    print("'plausible' is normal on a real invoice carrying tax or shipping. Only 'short' is")
    print("an anomaly on its own, because neither can reduce a total.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Finish the storage scope.")
    parser.add_argument("--db", default=storage.DEFAULT_DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(migrate(args.db, dry_run=args.dry_run))
