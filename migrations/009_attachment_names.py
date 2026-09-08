"""Migration 009: record which attachments each email carried.

    ./.venv/bin/python migrations/009_attachment_names.py [--db workflow_platform.db] [--dry-run]

What was missing. `email_messages.attachment_count` says how many files arrived. It does
not say which. So a PDF sitting in inbox/ could not be traced back to the message that
delivered it, and `invoices.email_id` stayed NULL on every row even after the mailbox was
populated.

The design left this piece open on purpose: a sidecar file per attachment and a staging
table were both proposed and neither was chosen. This is the third option and the cheapest.
File names in inbox/ are already unique, and the mailbox already knows what it attached, so
the link needs one column rather than a new mechanism.

It is a lookup, not a guarantee. Two messages attaching files of the same name cannot be
told apart, and the newest match wins. When intake writes the link directly, this becomes
redundant, which is the intended outcome rather than a defect.

Additive: one nullable column, no row rewritten.
"""

import argparse
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage import connect, table_exists  # noqa: E402

FROM_VERSION = 8
TARGET_VERSION = 9


def current_version(conn):
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return row["v"] if row and row["v"] is not None else 0


def migrate(db_path, dry_run=False):
    if not os.path.exists(db_path):
        print(f"[!] No database at {db_path}. Nothing to migrate.")
        return 1

    with connect(db_path) as conn:
        if not table_exists(conn, "email_messages"):
            print("[!] No 'email_messages' table. Run migration 004 first.")
            return 1
        cols = [r[1] for r in conn.execute("PRAGMA table_info(email_messages)")]
        if "attachment_names" in cols:
            print(f"[*] 'attachment_names' already exists. Database at version "
                  f"{current_version(conn)}.")
            return 0
        version = current_version(conn)
        if version != FROM_VERSION:
            print(f"[!] Expected schema version {FROM_VERSION}, found {version}. Refusing.")
            return 1
        before = conn.execute("SELECT COUNT(*) FROM email_messages").fetchone()[0]

    print(f"=== Migration 009: record attachment names in {db_path} ===")
    print(f"[*] {before} emails at schema version {version}.")

    if dry_run:
        print("[*] Dry run: planning only, no writes.\n")
        print("Would add attachment_names TEXT to email_messages, comma separated.")
        print("Existing rows keep NULL until something writes them; seed_mock_emails.py")
        print("backfills the mock mailbox and intake will supply real ones.")
        print("\nDry run only. Re-run without --dry-run to apply.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = f"{db_path}.bak-{stamp}"
    shutil.copy2(db_path, backup_path)
    print(f"[*] Backed up to {backup_path}")

    try:
        with connect(db_path) as conn:
            conn.execute("ALTER TABLE email_messages ADD COLUMN attachment_names TEXT")
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (TARGET_VERSION,))
            conn.commit()
    except Exception as error:                          # noqa: BLE001
        print(f"[!] Migration failed: {error}")
        print(f"[!] Nothing committed. Restore from {backup_path} if needed.")
        return 1

    with connect(db_path) as conn:
        after = conn.execute("SELECT COUNT(*) FROM email_messages").fetchone()[0]
        named = conn.execute(
            "SELECT COUNT(*) FROM email_messages "
            "WHERE attachment_names IS NOT NULL").fetchone()[0]
        version_after = current_version(conn)

    print("\n" + "=" * 62)
    print(f"  emails            {before} -> {after}")
    print(f"  with named files  {named}")
    print(f"  version           {version} -> {version_after}")
    print("=" * 62)

    if after != before:
        print("[!] Row count changed during an additive migration. Investigate.")
        return 1

    print("No rows changed. Re-run seed_mock_emails.py to backfill the mock mailbox,")
    print("after which the pipeline can resolve inbox/<file> back to its email.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Record attachment names.")
    parser.add_argument("--db", default="workflow_platform.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(migrate(args.db, dry_run=args.dry_run))
