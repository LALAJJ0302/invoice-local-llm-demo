"""Migration 013: dashboard users and who reviewed each invoice.

    ./.venv/bin/python migrations/013_reviewer_login.py [--db workflow_platform.db] [--dry-run]

Written on luke/team-tasks as migration 010. Re-landed at 013 because 010 and 011
were already on main. A migration that has been applied cannot be renumbered onto
a version that no longer exists.

users
    Local dashboard accounts. Passwords are hashes. The plaintext lives in .env and is
    written here by auth.py on dashboard start, not by this migration.

invoices.reviewed_by
    The user who approved or rejected the document. NULL on every existing row: nothing
    recorded a person before this column existed, and auto-approval still leaves it NULL.
"""

import argparse
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import storage  # noqa: E402
from storage import connect, table_exists  # noqa: E402

FROM_VERSION = 12
TARGET_VERSION = 13

DDL_USERS = """
CREATE TABLE users (
    user_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT    NOT NULL UNIQUE,
    display_name    TEXT    NOT NULL,
    password_hash   TEXT    NOT NULL,
    jira_account_id TEXT
);
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
        has_users = table_exists(conn, "users")
        has_reviewed_by = "reviewed_by" in column_names(conn, "invoices")

        if has_users and has_reviewed_by:
            print(f"[*] users and invoices.reviewed_by already exist. "
                  f"Database at version {version}.")
            return 0 if (version is not None and version >= TARGET_VERSION) else 1
        if version != FROM_VERSION:
            print(f"[!] Expected schema version {FROM_VERSION}, found {version}. Refusing to run.")
            return 1

        invoices_before = conn.execute("SELECT COUNT(*) c FROM invoices").fetchone()["c"]
        sum_before = conn.execute(
            "SELECT COALESCE(SUM(total_cents),0) s FROM invoices").fetchone()["s"]

    print(f"=== Migration 013: reviewer login in {db_path} ===")
    print(f"[*] {invoices_before} invoices at schema version {version}.")

    if dry_run:
        print("[*] Dry run: planning only, no writes.\n")
        print("Would create:")
        print("    users                         dashboard accounts")
        print("Would add:")
        print("    invoices.reviewed_by          nullable user id")
        print("\nDry run only. Re-run without --dry-run to apply.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = f"{db_path}.bak-{stamp}"
    shutil.copy2(db_path, backup_path)
    print(f"[*] Backed up to {backup_path}")

    conn = connect(db_path)
    try:
        conn.execute("BEGIN")
        if not has_users:
            conn.executescript(DDL_USERS)
        if not has_reviewed_by:
            conn.execute(
                "ALTER TABLE invoices ADD COLUMN reviewed_by INTEGER REFERENCES users(user_id)"
            )
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
        sum_after = conn.execute(
            "SELECT COALESCE(SUM(total_cents),0) s FROM invoices").fetchone()["s"]
        reviewed = conn.execute(
            "SELECT COUNT(*) c FROM invoices WHERE reviewed_by IS NOT NULL").fetchone()["c"]
        users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]

    print("\n" + "=" * 66)
    print(f"  invoices     {invoices_before} -> {invoices_after}")
    print(f"  sum(total)   {sum_before/100:,.2f} -> {sum_after/100:,.2f}")
    print(f"  reviewed_by  {reviewed} set (0 expected on first run)")
    print(f"  users        {users}")
    print("=" * 66)

    if (invoices_before, sum_before) != (invoices_after, sum_after):
        print("[!] Data moved during an additive migration. Investigate.")
        return 1
    print("No existing rows or totals changed, as an additive migration requires.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Add dashboard users and invoices.reviewed_by.")
    parser.add_argument("--db", default=storage.DEFAULT_DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(migrate(args.db, dry_run=args.dry_run))
