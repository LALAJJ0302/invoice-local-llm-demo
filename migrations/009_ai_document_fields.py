"""Migration 009: attachment names, category, summary and action items.

    ./.venv/bin/python migrations/009_ai_document_fields.py [--db workflow_platform.db] [--dry-run]

Combines two additive changes that both belonged at version 9:

email_messages.attachment_names
    Comma-separated file names so inbox/<file> can be traced back to the email that
    delivered it. A lookup, not a guarantee: the newest match wins when names collide.

invoices.category, invoices.summary, action_items
    Storage for email_ai.py's document-intelligence pass: business category, a one-line
    summary, and follow-up actions with evidence quotes. Distinct from `tasks`, which
    are this pipeline's routing decisions.

Both are additive. No existing table is rebuilt and no existing value changes.
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

INVOICE_COLUMNS = [
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
        if not table_exists(conn, "email_messages"):
            print("[!] No 'email_messages' table. Run the earlier migrations first.")
            return 1

        version = current_version(conn)
        email_cols = column_names(conn, "email_messages")
        invoice_cols = column_names(conn, "invoices")
        has_attachment_names = "attachment_names" in email_cols
        has_invoice_columns = all(name in invoice_cols for name, _ in INVOICE_COLUMNS)
        has_action_items = table_exists(conn, "action_items")

        if has_attachment_names and has_invoice_columns and has_action_items:
            print(f"[*] attachment_names, category/summary and action_items already exist. "
                  f"Database at version {version}.")
            return 0 if (version is not None and version >= TARGET_VERSION) else 1
        if version != FROM_VERSION:
            print(f"[!] Expected schema version {FROM_VERSION}, found {version}. Refusing to run.")
            return 1

        emails_before = conn.execute("SELECT COUNT(*) c FROM email_messages").fetchone()["c"]
        invoices_before = conn.execute("SELECT COUNT(*) c FROM invoices").fetchone()["c"]
        sum_before = conn.execute("SELECT COALESCE(SUM(total_cents),0) s FROM invoices").fetchone()["s"]

    print(f"=== Migration 009: attachment names and document intelligence in {db_path} ===")
    print(f"[*] {invoices_before} invoices, {emails_before} emails at schema version {version}.")

    if dry_run:
        print("[*] Dry run: planning only, no writes.\n")
        print("Would add:")
        print("    email_messages.attachment_names   comma-separated file names")
        print("    invoices.category                 nullable")
        print("    invoices.summary                  nullable")
        print("Would create:")
        print("    action_items                      one row per follow-up action")
        print("\nDry run only. Re-run without --dry-run to apply.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = f"{db_path}.bak-{stamp}"
    shutil.copy2(db_path, backup_path)
    print(f"[*] Backed up to {backup_path}")

    conn = connect(db_path)
    try:
        conn.execute("BEGIN")
        if not has_attachment_names:
            conn.execute("ALTER TABLE email_messages ADD COLUMN attachment_names TEXT")
        if not has_invoice_columns:
            for name, definition in INVOICE_COLUMNS:
                if name not in column_names(conn, "invoices"):
                    conn.execute(f"ALTER TABLE invoices ADD COLUMN {name} {definition}")
        if not has_action_items:
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
        emails_after = conn.execute("SELECT COUNT(*) c FROM email_messages").fetchone()["c"]
        invoices_after = conn.execute("SELECT COUNT(*) c FROM invoices").fetchone()["c"]
        sum_after = conn.execute("SELECT COALESCE(SUM(total_cents),0) s FROM invoices").fetchone()["s"]
        action_items = conn.execute("SELECT COUNT(*) c FROM action_items").fetchone()["c"]
        named = conn.execute(
            "SELECT COUNT(*) c FROM email_messages "
            "WHERE attachment_names IS NOT NULL").fetchone()["c"]

    print("\n" + "=" * 66)
    print(f"  emails       {emails_before} -> {emails_after}")
    print(f"  with names   {named}")
    print(f"  invoices     {invoices_before} -> {invoices_after}")
    print(f"  sum(total)   {sum_before/100:,.2f} -> {sum_after/100:,.2f}")
    print(f"  action_items {action_items} recorded (0 expected on first run)")
    print("=" * 66)

    if (emails_before, invoices_before, sum_before) != (emails_after, invoices_after, sum_after):
        print("[!] Data moved during an additive migration. Investigate.")
        return 1
    print("No existing rows or totals changed, as an additive migration requires.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Add attachment names, category, summary and action items.")
    parser.add_argument("--db", default=storage.DEFAULT_DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(migrate(args.db, dry_run=args.dry_run))
