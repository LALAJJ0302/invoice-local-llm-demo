"""Migration 011: store what the email AI module returns.

    ./.venv/bin/python migrations/011_email_analysis.py [--db workflow_platform.db] [--dry-run]

Four tables, nothing existing touched. The design and the reasoning behind every constraint
are in `email-analysis-schema-spec.md`; this docstring records only what a person running the
migration needs to know.

`email_analysis` and `thread_analysis` hold JJ's two records. `action_items` and
`thread_decisions` hold the two fields he confirmed are lists rather than blocks of text.
Storing a list as a joined string was the `line_items` mistake and it cost a migration to
undo, so `latest_decisions` becomes one row per decision with an ordinal that preserves the
order a list has and rows do not.

Three choices that look odd and are deliberate:

- `action_items` has two nullable parent keys and an exactly-one CHECK, because `ActionItem`
  is one Pydantic class serving both an email's action items and a thread's outstanding
  actions. Identical row shape, different owner. That is normalisation, not the flat table.
- `attempt_count` is checked only for `>= 1`. `email_ai.py` already caps it at
  `MAX_EVIDENCE_ATTEMPTS` in four places, and a second cap here would mean raising that
  constant breaks every insert.
- `validation_status` allows `Failed`, which JJ's module cannot produce. A retained result
  that failed evidence validation is `NeedsReview`; `Failed` would mean no analysis exists,
  which is not a row. The column matches `invoices` so the vocabulary is one thing across
  the schema, and storage never writes it.

Additive: four CREATE TABLEs and five indexes. Reversible by dropping them. Requires
migration 010, which supplies `processing_runs.run_kind` and the `email_messages` thread
columns these tables depend on.
"""

import argparse
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage import DDL, connect, table_exists  # noqa: E402

FROM_VERSION = 10
TARGET_VERSION = 11

NEW_TABLES = ("email_analysis", "thread_analysis", "action_items", "thread_decisions")


def ddl_tail():
    """Takes the four tables and their indexes straight out of storage.DDL.

    Copying the SQL into this file would create a second definition that can drift from the
    first, which is exactly what tests/test_migration.py exists to catch. Slicing the one
    definition means a fresh database and a migrated one cannot disagree.

    Returned whole and run with executescript rather than split on semicolons, because the
    comments in that DDL contain semicolons of their own and splitting on them produced
    fragments that are not SQL.
    """
    marker = "-- The email AI module's output. Added by migration 011."
    if marker not in DDL:
        raise RuntimeError("storage.DDL no longer carries the migration 011 marker.")
    return DDL[DDL.index(marker):]


def created_objects(script):
    """The CREATE lines, for the dry run. Reporting, not parsing."""
    return [
        line.strip().rstrip(";").rstrip("(").strip()
        for line in script.splitlines()
        if line.strip().upper().startswith("CREATE ")
    ]


def current_version(conn):
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return row["v"] if row and row["v"] is not None else 0


def migrate(db_path, dry_run=False):
    if not os.path.exists(db_path):
        print(f"[!] No database at {db_path}. Nothing to migrate.")
        return 1

    with connect(db_path) as conn:
        if all(table_exists(conn, name) for name in NEW_TABLES):
            print(f"[*] The analysis tables already exist. Database at version "
                  f"{current_version(conn)}.")
            return 0
        runs = [r[1] for r in conn.execute("PRAGMA table_info(processing_runs)")]
        if "run_kind" not in runs:
            print("[!] processing_runs has no run_kind. Run migration 010 first.")
            return 1
        version = current_version(conn)
        if version != FROM_VERSION:
            print(f"[!] Expected schema version {FROM_VERSION}, found {version}. Refusing.")
            return 1
        emails = conn.execute("SELECT COUNT(*) FROM email_messages").fetchone()[0]

    script = ddl_tail()

    print(f"=== Migration 011: email analysis tables in {db_path} ===")
    print(f"[*] {emails} emails at schema version {version}.")

    if dry_run:
        print("[*] Dry run: planning only, no writes.\n")
        for created in created_objects(script):
            print(f"    {created}")
        print("\nNo existing table is touched and no row is written. The tables stay empty")
        print("until the email AI module is wired to save_email_analysis().")
        print("\nDry run only. Re-run without --dry-run to apply.")
        return 0

    # Not simply the timestamp every earlier migration uses. The quickstart runs these two
    # back to back, both land in the same second, and the second copy then overwrites the
    # first, leaving only the state after 010 and no way back to the state before it.
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = f"{db_path}.bak-{stamp}"
    suffix = 2
    while os.path.exists(backup_path):
        backup_path = f"{db_path}.bak-{stamp}-{suffix}"
        suffix += 1
    shutil.copy2(db_path, backup_path)
    print(f"[*] Backed up to {backup_path}")

    try:
        with connect(db_path) as conn:
            before = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0]
            conn.executescript(script)
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (TARGET_VERSION,))
            conn.commit()
    except Exception as error:                          # noqa: BLE001
        print(f"[!] Migration failed: {error}")
        print(f"[!] Nothing committed. Restore from {backup_path} if needed.")
        return 1

    with connect(db_path) as conn:
        after = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0]
        missing = [name for name in NEW_TABLES if not table_exists(conn, name)]
        emails_after = conn.execute("SELECT COUNT(*) FROM email_messages").fetchone()[0]
        version_after = current_version(conn)

    print("\n" + "=" * 62)
    print(f"  tables            {before} -> {after}")
    print(f"  emails            {emails} -> {emails_after}, none touched")
    print(f"  version           {version} -> {version_after}")
    print("=" * 62)

    if missing:
        print(f"[!] Expected tables missing after the migration: {missing}")
        return 1
    if emails_after != emails:
        print("[!] Row count changed during an additive migration. Investigate.")
        return 1

    print("The four tables are empty and stay that way until JJ's module calls")
    print("save_email_analysis() and save_thread_analysis().")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Add the email analysis tables.")
    parser.add_argument("--db", default="workflow_platform.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(migrate(args.db, dry_run=args.dry_run))
