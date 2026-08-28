"""Migration 001: flat workflow_records -> processing_runs / invoices / line_items.

Standalone. Never imported by the pipeline, never run on import.

    ./.venv/bin/python migrations/001_normalise.py [--db workflow_platform.db] [--dry-run]

Nothing is dropped. workflow_records is renamed to workflow_records_v1 and kept, and the
database file is copied to a timestamped .bak first.

Beyond moving rows, this recovers the invoice totals that the flat schema left trapped in
the raw_json blob: all three current rows store total_amount = 0.00 while the correct value
sits in the same row's line items.
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import storage  # noqa: E402
from storage import (  # noqa: E402
    VALID_STATUSES,
    coerce_currency,
    coerce_date,
    connect,
    content_hash,
    from_cents,
    normalise_items,
    recover_total_cents,
    sha256_file,
    table_exists,
    to_cents,
)

# The version this migration produces. Migration 002 takes it from here.
TARGET_VERSION = 1

# The version-1 DDL is frozen inline, deliberately not imported from storage.py.
# storage.DDL describes the CURRENT schema, which moves on with every migration. This file
# must keep producing the exact shape it was written for, so that 002 and later have the
# structure they expect to alter.
#
# The helper functions above are still imported rather than frozen. That is a different
# concern: they produce data, not structure, so a later bug fix in the cents conversion or
# the summary-row rule should benefit a backfill run today just as much as one run last week.
DDL_V1 = """
CREATE TABLE schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE processing_runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    model_name  TEXT    NOT NULL,
    threshold   REAL    NOT NULL CHECK (threshold BETWEEN 0 AND 1),
    doc_count   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE invoices (
    invoice_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            INTEGER NOT NULL REFERENCES processing_runs(run_id),
    file_name         TEXT    NOT NULL,
    source_sha256     TEXT    NOT NULL,
    content_sha256    TEXT    NOT NULL,
    invoice_number    TEXT,
    vendor_name       TEXT,
    invoice_date      TEXT    CHECK (invoice_date IS NULL OR invoice_date GLOB '____-__-__'),
    total_cents       INTEGER CHECK (total_cents IS NULL OR total_cents >= 0),
    currency          TEXT    CHECK (currency IS NULL OR currency = 'Unknown' OR length(currency) = 3),
    validation_score  REAL    NOT NULL CHECK (validation_score BETWEEN 0 AND 1),
    status            TEXT    NOT NULL CHECK (status IN ('Validated','NeedsReview','Failed')),
    extraction_source TEXT    NOT NULL DEFAULT 'model'
                              CHECK (extraction_source IN ('model','fallback','manual')),
    approval_status   TEXT    NOT NULL DEFAULT 'Pending'
                              CHECK (approval_status IN ('Pending','Approved','Rejected')),
    reviewed_at       TEXT,
    archive_path      TEXT,
    raw_json          TEXT,
    processed_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX ux_invoices_content ON invoices(content_sha256);
CREATE INDEX ix_invoices_status ON invoices(status);
CREATE INDEX ix_invoices_vendor ON invoices(vendor_name);
CREATE INDEX ix_invoices_run    ON invoices(run_id);

CREATE TABLE line_items (
    line_item_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id       INTEGER NOT NULL REFERENCES invoices(invoice_id) ON DELETE CASCADE,
    line_no          INTEGER NOT NULL,
    description      TEXT    NOT NULL,
    quantity         REAL,
    unit_price_cents INTEGER CHECK (unit_price_cents IS NULL OR unit_price_cents >= 0),
    line_total_cents INTEGER CHECK (line_total_cents IS NULL OR line_total_cents >= 0),
    is_summary_row   INTEGER NOT NULL DEFAULT 0 CHECK (is_summary_row IN (0,1)),
    UNIQUE (invoice_id, line_no)
);

CREATE INDEX ix_line_items_invoice ON line_items(invoice_id);
"""

LEGACY_MODEL_NAME = "llama3.2 (legacy, pre-migration)"
LEGACY_THRESHOLD = 0.80


def read_text_layer(path):
    """Extracts the text layer from an archived PDF, for the content hash."""
    if not path or not os.path.exists(path):
        return ""
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as error:
        print(f"    [warn] could not read the text layer of {path}: {error}")
        return ""


def resolve_archive_path(repo_root, stored_path):
    """Legacy archive_path values are relative, so they break when run from elsewhere."""
    if not stored_path:
        return None
    if os.path.isabs(stored_path):
        return stored_path
    return os.path.abspath(os.path.join(repo_root, stored_path))


def migrate(db_path, dry_run=False):
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if not os.path.exists(db_path):
        print(f"[!] No database at {db_path}. Nothing to migrate.")
        return 1

    with connect(db_path) as conn:
        if table_exists(conn, "invoices"):
            print("[*] 'invoices' already exists. This database is already migrated.")
            return 0
        if not table_exists(conn, "workflow_records"):
            print("[!] No 'workflow_records' table found. Nothing to migrate.")
            return 1
        legacy_rows = [dict(r) for r in conn.execute(
            "SELECT * FROM workflow_records ORDER BY id"
        )]

    print(f"=== Migration 001: normalise {db_path} ===")
    print(f"[*] Read {len(legacy_rows)} legacy rows from workflow_records.")

    if dry_run:
        print("[*] Dry run: planning only, no writes.\n")

    # 1. Back up the file before touching it.
    if not dry_run:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = f"{db_path}.bak-{stamp}"
        shutil.copy2(db_path, backup_path)
        print(f"[*] Backed up to {backup_path}")

    report = []
    warnings = []

    conn = connect(db_path)
    try:
        if not dry_run:
            conn.execute("BEGIN")
            # 2. Keep the original table. Nothing is dropped.
            conn.execute("ALTER TABLE workflow_records RENAME TO workflow_records_v1")
            # 3. Create the new schema.
            conn.executescript(DDL_V1)
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (TARGET_VERSION,))

            # 4. One synthetic run to own the legacy rows.
            timestamps = [r.get("processed_at") for r in legacy_rows if r.get("processed_at")]
            cursor = conn.execute(
                "INSERT INTO processing_runs (started_at, finished_at, model_name, threshold, doc_count) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    min(timestamps) if timestamps else datetime.now().isoformat(" ", "seconds"),
                    max(timestamps) if timestamps else None,
                    LEGACY_MODEL_NAME,
                    LEGACY_THRESHOLD,
                    len(legacy_rows),
                ),
            )
            run_id = int(cursor.lastrowid)
            print(f"[*] Created synthetic processing_runs row run_id={run_id} for the legacy data.")
        else:
            run_id = 0

        seen_hashes = {}

        # 5. Move each legacy row across.
        for legacy in legacy_rows:
            legacy_id = legacy["id"]
            file_name = legacy["file_name"]
            archive_path = resolve_archive_path(repo_root, legacy.get("archive_path"))

            source_sha = sha256_file(archive_path) if archive_path else None
            if source_sha is None:
                warnings.append(f"row {legacy_id} ({file_name}): archived file missing, hashes are placeholders")
                source_sha = f"missing:{legacy_id}:{file_name}"

            raw_text = read_text_layer(archive_path)
            try:
                content_sha = content_hash(raw_text, source_sha)
            except ValueError:
                content_sha = f"legacy:{legacy_id}:{file_name}"

            if content_sha in seen_hashes:
                warnings.append(
                    f"row {legacy_id} ({file_name}): duplicate of row {seen_hashes[content_sha]}, "
                    "collapsed by the content hash"
                )
            seen_hashes.setdefault(content_sha, legacy_id)

            try:
                extracted = json.loads(legacy.get("raw_json") or "{}")
            except (TypeError, ValueError):
                extracted = {}
                warnings.append(f"row {legacy_id} ({file_name}): raw_json was unparseable")

            items = normalise_items(extracted.get("items"))

            # 6. Recover the total when the flat row stored 0 or null.
            before_cents = to_cents(legacy.get("total_amount"))
            total_cents, extraction_source, note = before_cents, "model", None
            if not total_cents:
                recovered, reason = recover_total_cents(items)
                if recovered:
                    total_cents, extraction_source, note = recovered, "fallback", reason

            status = legacy.get("status")
            if status not in VALID_STATUSES:
                warnings.append(f"row {legacy_id} ({file_name}): status {status!r} is not valid, set to NeedsReview")
                status = "NeedsReview"

            try:
                score = float(legacy.get("confidence_score") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            if not 0.0 <= score <= 1.0:
                warnings.append(f"row {legacy_id} ({file_name}): confidence_score {score} out of range, clamped")
                score = min(max(score, 0.0), 1.0)

            invoice_date = coerce_date(legacy.get("date"))
            if legacy.get("date") and invoice_date is None:
                warnings.append(
                    f"row {legacy_id} ({file_name}): date {legacy['date']!r} is not an unambiguous "
                    "date, stored as NULL and kept in raw_json"
                )

            report.append({
                "file_name": file_name,
                "before_cents": before_cents,
                "after_cents": total_cents,
                "source": extraction_source,
                "note": note,
                "items": len(items),
                "summary_rows": sum(r["is_summary_row"] for r in items),
            })

            if dry_run:
                continue

            cursor = conn.execute(
                """
                INSERT INTO invoices (
                    run_id, file_name, source_sha256, content_sha256, invoice_number,
                    vendor_name, invoice_date, total_cents, currency, validation_score,
                    status, extraction_source, archive_path, raw_json, processed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(content_sha256) DO NOTHING
                """,
                (
                    run_id, file_name, source_sha, content_sha,
                    legacy.get("invoice_number"), legacy.get("vendor_name"), invoice_date,
                    total_cents, coerce_currency(legacy.get("currency")), score, status,
                    extraction_source, archive_path, legacy.get("raw_json"),
                    legacy.get("processed_at") or datetime.now().isoformat(" ", "seconds"),
                ),
            )
            if cursor.rowcount == 0:  # collapsed into an earlier row by the content hash
                continue
            invoice_id = int(cursor.lastrowid)

            conn.executemany(
                """
                INSERT INTO line_items (
                    invoice_id, line_no, description, quantity,
                    unit_price_cents, line_total_cents, is_summary_row
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (invoice_id, r["line_no"], r["description"], r["quantity"],
                     r["unit_price_cents"], r["line_total_cents"], r["is_summary_row"])
                    for r in items
                ],
            )

        if not dry_run:
            conn.commit()
    except Exception:
        conn.rollback()
        print("[!] Migration failed and was rolled back. The database is unchanged.")
        raise
    finally:
        conn.close()

    print_report(report, warnings, dry_run)
    return 0


def money(cents):
    return "n/a" if cents is None else f"{from_cents(cents):,.2f}"


def print_report(report, warnings, dry_run):
    print("\n" + "=" * 78)
    print("Before / after totals" + (" (dry run)" if dry_run else ""))
    print("=" * 78)
    print(f"{'File':<38} | {'before':>10} | {'after':>10} | source")
    print("-" * 78)
    recovered = 0
    for row in report:
        if row["before_cents"] != row["after_cents"]:
            recovered += 1
        print(f"{row['file_name'][:38]:<38} | {money(row['before_cents']):>10} | "
              f"{money(row['after_cents']):>10} | {row['source']}"
              + (f" ({row['note']})" if row["note"] else ""))
    print("=" * 78)
    total_items = sum(r["items"] for r in report)
    summary_rows = sum(r["summary_rows"] for r in report)
    print(f"{len(report)} rows migrated, {recovered} totals recovered.")
    print(f"{total_items} line items extracted from raw_json, {summary_rows} flagged as summary rows.")
    print("Recovered totals carry extraction_source = 'fallback', never 'model'.")

    if warnings:
        print("\nWarnings:")
        for warning in warnings:
            print(f"  - {warning}")

    if dry_run:
        print("\nDry run only. Re-run without --dry-run to apply.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Normalise the flat workflow_records table.")
    parser.add_argument("--db", default=storage.DEFAULT_DB_PATH, help="path to the SQLite file")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args()
    raise SystemExit(migrate(args.db, dry_run=args.dry_run))
