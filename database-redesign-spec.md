# Spec: Phase 4 Database Redesign

**Owner:** Neo. **Branch:** `neo/database-redesign` (new, off `main`).
**Depends on:** nothing. Independent of the extraction schema fix, so it ships either way.
**Status:** awaiting three answers (see the end). No code until those are settled.

## Goal

Replace the single flat `workflow_records` table with a normalised relational schema that enforces
its own integrity, makes re-runs idempotent, and recovers the invoice totals currently trapped in
the `raw_json` blob.

## Non-goals

The extraction schema fix (JJ's lane). The approval UI. OCR. Anything in the extractor.

## Why: what is wrong today

Measured 2026-08-26 against the shipped schema. Full evidence in the vault at
`University/subjects/industry-project/extraction-schema-findings.md`.

### Tier 1, costs correct data now

1. **Line items are a JSON blob.** A repeating group stuffed into one column, a 1NF violation.
   `SELECT description FROM line_items WHERE unit_price > 200` fails: no such table. The cost is not
   theoretical. All three stored totals read `0.00` while the correct value sits in the same row:

   | File | stored `total_amount` | recoverable from blob | truth |
   |---|---|---|---|
   | INV-2026-001 | 0.00 | 1500.00 | 1500.00 |
   | INV-2026-002 | 0.00 | 2650.00 (as a "Grand Total" item) | 2650.00 |
   | INV-2026-003 | 0.00 | 2350.00 | 2350.00 |

2. **No constraints anywhere.** This row inserts without complaint:
   `status='banana', confidence_score=99.7, total_amount=-5000, date='not-a-date', currency='Galactic Credits'`

3. **No UNIQUE, so no deduplication.** Two rows sharing `INV-2026-001` are accepted. This is JJ's
   "older results still in SQLite" problem. It is a schema defect, not a cleanup chore.

4. **No run separation.** JJ's `run_id` instinct is right; it belongs in its own table.

### Tier 2, traps not currently firing

5. **`CREATE TABLE IF NOT EXISTS` never migrates.** Change the schema and a teammate's existing
   `.db` silently keeps the old shape, with no error.
6. **Archive and insert are not atomic.** `shutil.move` runs before `save_record`. If the insert
   throws, the file is archived with no record.
7. **Approve overwrites `confidence_score = 1.0`,** destroying the measurement on an audit field.
8. **`date` is free-text** with no format enforcement. 9. **`archive_path` is relative,** so it
   breaks when run from another directory.

### Tier 3, real but lower severity

10. **Money in a `REAL` column.** Float is genuinely imprecise (`0.1+0.2 = 0.3` returns false), but
    it could not be shown producing wrong totals on the current data. A design defect, not a live
    bug. Stated honestly because the report should not overclaim.
11. **No indexes.** 12. **`processed_at TIMESTAMP`** is not a real SQLite type; it gets NUMERIC
    affinity.

## Proposed schema

```sql
PRAGMA foreign_keys = ON;   -- SQLite defaults this OFF; without it the FKs are decorative

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

CREATE UNIQUE INDEX ux_invoices_content ON invoices(source_sha256);
CREATE INDEX ix_invoices_status ON invoices(status);
CREATE INDEX ix_invoices_vendor ON invoices(vendor_name);

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
```

## The five decisions worth arguing about

**1. Deduplication keys on file content, not `invoice_number`.** `invoice_number` is `NULL` on every
current row, so it cannot be a key, and the same invoice arrives from Gmail under different
filenames. SHA-256 of the file bytes is always available and stable. Writes become
`INSERT ... ON CONFLICT(source_sha256) DO UPDATE`, so re-running updates rather than duplicates.

**2. Money as integer cents.** Removes float from the money path. Display divides by 100 at the edge.

**3. `is_summary_row` on line items.** Straight from the evidence: on invoice 2 the model emitted
`"Grand Total": 2650.00` as a line item. Flagging those stops `SUM` double-counting and makes the
grand total recoverable. This is the column that repairs the data without touching the extractor.

**4. `approval_status` and `reviewed_at` go in now, unwired.** The approval UI is deferred to the
group's Trigger/Approval round, but adding columns during a migration already in flight costs
nothing and avoids a second migration.

**5. `schema_version` table.** Makes schema drift between teammates visible instead of silent.

## Migration and backfill

Standalone script, `migrations/001_normalise.py`. Not run on import.

1. Rename `workflow_records` to `workflow_records_v1`. **Nothing is dropped.**
2. Create new tables, insert `schema_version = 1`.
3. Create one synthetic `processing_runs` row for the legacy data.
4. Per legacy row: hash the archived file if present, map fields, convert money to cents.
5. Parse `raw_json.items` into `line_items`, flagging summary rows.
6. **Recover missing totals.** Where `total_amount` is 0 or null: use the summary row if present,
   else `SUM` of non-summary items. Set `extraction_source = 'fallback'` so a recovered total is
   never mistaken for an extracted one.
7. Print a before/after report.

Expected on current data: three rows recovered from `0.00` to `1500.00`, `2650.00`, `2350.00`, with
no change to the extractor.

## Code changes

| File | Change |
|---|---|
| `storage.py` | **New.** DDL, `StorageManager` rewritten for upsert plus child rows, cents helpers |
| `migrations/001_normalise.py` | **New.** Migration and backfill |
| `main.py` | Open a run at start, close at end; **move the file to `archive/` only after the insert commits** |
| `app.py` | `total_cents / 100.0`, `validation_score`, join for line items |

## Delivery

One PR, "Normalise the workflow database", led by the before/after totals table.

## Three open questions, blocking

**A. Table naming.** `invoices` / `line_items` read correctly, but `app.py` and `query_db.py`
reference `workflow_records`. Rename, or keep the old name to reduce JJ's review friction?
Leaning rename, since the migration touches those queries anyway.

**B. The `is_summary_row` rule.** Proposed: flag when `description` matches
`grand total|total|amount due|balance due` case-insensitively **and** `quantity <= 1`. Correctly
catches invoice 2. Would misfire on a real line item called "Total Station Rental". Accept the
heuristic, or store `NULL` when unsure and let a human resolve?

**C. Legacy table.** Keep `workflow_records_v1` indefinitely, or drop after verification?
