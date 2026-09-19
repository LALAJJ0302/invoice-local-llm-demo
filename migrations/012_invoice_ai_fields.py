"""Migration 012: per-attachment rows, and the AI pass over the invoice document.

    ./.venv/bin/python migrations/012_invoice_ai_fields.py [--db workflow_platform.db] [--dry-run]

Three additive changes, all originally written by Luke on `luke/team-tasks` as migrations 008
and 009. They are re-landed here at 12 because 8 and 9 were already merged to `main` and
already run against every teammate's database. A migration that has been applied cannot be
renumbered: the replacement expects a version that no longer exists, refuses, and the chain
stops. That is a numbering problem, not a problem with the SQL, which is unchanged apart from
one table name.

**email_attachments.** One row per file an email carried, keyed on `(email_id, content_sha256)`
so the same attachment arriving twice is stored once. This supersedes `attachment_names`, the
comma-separated column migration 009 added, which cannot tell two files of the same name apart
and was documented at the time as a lookup rather than a guarantee. The old column is left in
place and still populated; nothing reads one and writes the other yet, and removing it is a
later decision rather than part of this merge.

**invoices.category and invoices.summary.** The document-intelligence pass over the invoice
itself, distinct from `email_analysis.category`, which classifies the message that carried it.
Both may be set, and they may disagree, which is legitimate: a covering email can be a query
about an invoice that is itself perfectly ordinary.

**invoice_action_items.** Actions found inside the invoice document. Renamed from
`action_items`, which collided with a table of that name created in parallel for actions found
in an email. Same name, different parent, different columns, so they cannot coexist. Both were
renamed for what they hang off, `invoice_action_items` and `email_action_items`, before either
reached `main`.

Additive: one column pair, two tables, no existing row rewritten.
"""

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage import backup_path as make_backup_path  # noqa: E402
from storage import connect, table_exists  # noqa: E402

FROM_VERSION = 11
TARGET_VERSION = 12

INVOICE_COLUMNS = [
    ("category", "TEXT"),
    ("summary", "TEXT"),
]

DDL = """
CREATE TABLE email_attachments (
    attachment_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id       INTEGER NOT NULL REFERENCES email_messages(email_id) ON DELETE CASCADE,
    filename       TEXT    NOT NULL,
    content_sha256 TEXT    NOT NULL,
    saved_path     TEXT,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (email_id, content_sha256)
);

CREATE INDEX ix_email_attachments_email ON email_attachments(email_id);

CREATE TABLE invoice_action_items (
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

CREATE INDEX ix_invoice_action_items_invoice ON invoice_action_items(invoice_id);
"""

NEW_TABLES = ("email_attachments", "invoice_action_items")


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
        have_tables = all(table_exists(conn, name) for name in NEW_TABLES)
        have_columns = all(name in column_names(conn, "invoices")
                           for name, _ in INVOICE_COLUMNS)
        if have_tables and have_columns:
            print(f"[*] Already applied. Database at version {current_version(conn)}.")
            return 0
        version = current_version(conn)
        if version != FROM_VERSION:
            print(f"[!] Expected schema version {FROM_VERSION}, found {version}. Refusing.")
            return 1
        invoices_before = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
        emails_before = conn.execute("SELECT COUNT(*) FROM email_messages").fetchone()[0]

    print(f"=== Migration 012: invoice AI fields and per-attachment rows in {db_path} ===")
    print(f"[*] {invoices_before} invoices, {emails_before} emails at schema version {version}.")

    if dry_run:
        print("[*] Dry run: planning only, no writes.\n")
        print("Would add email_attachments and invoice_action_items, and two nullable")
        print("columns to invoices: category and summary. Every existing row keeps NULL")
        print("until something writes it. attachment_names is left alone.")
        print("\nDry run only. Re-run without --dry-run to apply.")
        return 0

    backup_path = make_backup_path(db_path)
    shutil.copy2(db_path, backup_path)
    print(f"[*] Backed up to {backup_path}")

    try:
        with connect(db_path) as conn:
            existing = column_names(conn, "invoices")
            for name, column_type in INVOICE_COLUMNS:
                if name not in existing:
                    conn.execute(f"ALTER TABLE invoices ADD COLUMN {name} {column_type}")
            conn.executescript(DDL)
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (TARGET_VERSION,))
            conn.commit()
    except Exception as error:                          # noqa: BLE001
        print(f"[!] Migration failed: {error}")
        print(f"[!] Nothing committed. Restore from {backup_path} if needed.")
        return 1

    with connect(db_path) as conn:
        invoices_after = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
        emails_after = conn.execute("SELECT COUNT(*) FROM email_messages").fetchone()[0]
        missing = [name for name in NEW_TABLES if not table_exists(conn, name)]
        version_after = current_version(conn)

    print("\n" + "=" * 62)
    print(f"  invoices          {invoices_before} -> {invoices_after}, none touched")
    print(f"  emails            {emails_before} -> {emails_after}, none touched")
    print(f"  new tables        {', '.join(NEW_TABLES)}")
    print(f"  version           {version} -> {version_after}")
    print("=" * 62)

    if missing:
        print(f"[!] Expected tables missing after the migration: {missing}")
        return 1
    if (invoices_after, emails_after) != (invoices_before, emails_before):
        print("[!] Row count changed during an additive migration. Investigate.")
        return 1

    print("invoices.category and invoices.summary are NULL on every existing row until")
    print("main.py's document-intelligence pass runs over them again.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Add invoice AI fields and attachment rows.")
    parser.add_argument("--db", default="workflow_platform.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(migrate(args.db, dry_run=args.dry_run))
