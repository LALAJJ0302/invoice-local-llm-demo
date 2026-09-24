"""Migration 011: first-login password change.

    ./.venv/bin/python migrations/011_must_change_password.py [--db workflow_platform.db] [--dry-run]

users.must_change_password
    1 until the person replaces the password stored by `auth.py add`. Existing rows
    become 1: nobody has completed this step yet, because the column did not exist.
"""

import argparse
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import storage  # noqa: E402
from storage import connect, table_exists  # noqa: E402

FROM_VERSION = 10
TARGET_VERSION = 11


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
        if not table_exists(conn, "users"):
            print("[!] No 'users' table. Run the earlier migrations first.")
            return 1

        version = current_version(conn)
        has_flag = "must_change_password" in column_names(conn, "users")
        if has_flag:
            print(f"[*] users.must_change_password already exists. Database at version {version}.")
            return 0 if (version is not None and version >= TARGET_VERSION) else 1
        if version != FROM_VERSION:
            print(f"[!] Expected schema version {FROM_VERSION}, found {version}. Refusing to run.")
            return 1

        users_before = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]

    print(f"=== Migration 011: first-login password change in {db_path} ===")
    print(f"[*] {users_before} users at schema version {version}.")

    if dry_run:
        print("[*] Dry run: planning only, no writes.\n")
        print("Would add:")
        print("    users.must_change_password   1 until the person chooses a password")
        print("\nDry run only. Re-run without --dry-run to apply.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = f"{db_path}.bak-{stamp}"
    shutil.copy2(db_path, backup_path)
    print(f"[*] Backed up to {backup_path}")

    conn = connect(db_path)
    try:
        conn.execute("BEGIN")
        conn.execute(
            "ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 1 "
            "CHECK (must_change_password IN (0, 1))"
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
        users_after = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        pending = conn.execute(
            "SELECT COUNT(*) c FROM users WHERE must_change_password = 1"
        ).fetchone()["c"]

    print("\n" + "=" * 66)
    print(f"  users        {users_before} -> {users_after}")
    print(f"  must change  {pending}")
    print("=" * 66)
    if users_before != users_after:
        print("[!] User count changed during an additive migration. Investigate.")
        return 1
    print("No users were added or removed.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Add users.must_change_password.")
    parser.add_argument("--db", default=storage.DEFAULT_DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(migrate(args.db, dry_run=args.dry_run))
