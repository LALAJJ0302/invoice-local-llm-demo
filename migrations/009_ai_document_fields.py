"""Migration 009: category, summary and action items from email_ai.py's document pass.

    ./.venv/bin/python migrations/009_ai_document_fields.py [--db workflow_platform.db] [--dry-run]

Why: `email_ai.py` (JJ's `jj/email-ai` branch) already classifies a document into a
category, summarises it, and extracts the concrete follow-up actions it asks for
(task / owner / deadline_text / evidence_quote). None of it had anywhere to live.

invoices.category, invoices.summary
    One document intelligence pass, added as two nullable columns rather than a new table:
    both are one value per invoice, so a join buys nothing normalisation would need to pay
    for. Nullable because the pass can fail (Ollama unreachable, a validation error)
    independently of the main extraction, and a document must still be stored without it.

action_items
    A list per invoice, so it gets its own table, shaped like `line_items`. Kept distinct
    from `tasks`: a task is this pipeline's own routing decision (Review/Approve/Payment/
    File); an action item is a claim about what the *document* says, with the model's
    supporting quote kept alongside it so a person can check the claim without reopening
    the file. `is_done` lets the dashboard offer a checklist without needing a second table.

Both additive. No existing table is rebuilt and no existing value changes.
"""

import argparse
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import storage  # noqa: E402
from storage import connect, table_exists  # noqa: E402

FROM_VERSION = 8
TARGET_VERSION = 9

COLUMNS = [
    ("category", "TEXT"),
    ("summary", "TEXT"),
]

DDL_V9 = """
CREATE TABLE action_items (
    action_item_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id      INTEGER NOT NULL REFERENCES invoices(invoice_id) ON DELETE CASCADE,
    line_no         INTEGER NOT NULL,
    task            TEXT    NOT NULL,
    owner           TEXT,
    deadline_text   TEXT,
    evidence_quote  TEXT,
    is_done         INTEGER NOT NULL DEFAULT 0 CHECK (is_done IN (0,1)),
    UNIQUE (invoice_id, line_no)
);

CREATE INDEX ix_action_items_invoice ON action_items(invoice_id);
"""


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
        existing = column_names(conn, "invoices")
        has_columns = all(name in existing for name, _ in COLUMNS)
        has_table = table_exists(conn, "action_items")

        if has_columns and has_table:
            print(f"[*] category/summary/action_items already exist. Database at version {version}.")
            return 0 if (version is not None and version >= TARGET_VERSION) else 1
        if version != FROM_VERSION:
            print(f"[!] Expected schema version {FROM_VERSION}, found {version}. Refusing to run.")
            return 1

        invoices_before = conn.execute("SELECT COUNT(*) c FROM invoices").fetchone()["c"]
        sum_before = conn.execute("SELECT COALESCE(SUM(total_cents),0) s FROM invoices").fetchone()["s"]

    print(f"=== Migration 009: category, summary, action items in {db_path} ===")
    print(f"[*] {invoices_before} invoices at schema version {version}. This migration is additive.")

    if dry_run:
        print("[*] Dry run: planning only, no writes.\n")
        print("Would add:")
        print("    invoices.category   nullable, existing rows get NULL")
        print("    invoices.summary    nullable, existing rows get NULL")
        print("Would create:")
        print("    action_items        one row per follow-up action, unique on")
        print("                        (invoice_id, line_no)")
        print("\nNothing backfills existing rows: the document-intelligence pass has to")
        print("actually run (main.py) to populate these, same as line_items did originally.")
        print("\nDry run only. Re-run without --dry-run to apply.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = f"{db_path}.bak-{stamp}"
    shutil.copy2(db_path, backup_path)
    print(f"[*] Backed up to {backup_path}")

    conn = connect(db_path)
    try:
        conn.execute("BEGIN")
        if not has_columns:
            for name, definition in COLUMNS:
                conn.execute(f"ALTER TABLE invoices ADD COLUMN {name} {definition}")
        if not has_table:
            conn.executescript(DDL_V9)
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
        action_items = conn.execute("SELECT COUNT(*) c FROM action_items").fetchone()["c"]

    print("\n" + "=" * 66)
    print(f"  invoices     {invoices_before} -> {invoices_after}")
    print(f"  sum(total)   {sum_before/100:,.2f} -> {sum_after/100:,.2f}")
    print(f"  action_items {action_items} recorded (0 expected on first run)")
    print("=" * 66)

    if (invoices_before, sum_before) != (invoices_after, sum_after):
        print("[!] Data moved during an additive migration. Investigate.")
        return 1
    print("No existing rows or totals changed, as an additive migration requires.")
    print("Existing invoices have category = NULL and summary = NULL: they predate the")
    print("document-intelligence pass. Re-run main.py to populate them for existing files")
    print("(or reprocess.py --restore to bring a file back from archive/ first).")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Add category, summary and action items.")
    parser.add_argument("--db", default=storage.DEFAULT_DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(migrate(args.db, dry_run=args.dry_run))
