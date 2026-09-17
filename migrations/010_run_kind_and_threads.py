"""Migration 010: tell an email run from an invoice run, and give an email a thread.

    ./.venv/bin/python migrations/010_run_kind_and_threads.py [--db workflow_platform.db] [--dry-run]

Two prerequisites for storing the AI module's output. Neither is the analysis itself: that
is migration 011, which is purely additive. They are split because this one rewrites a table
and that one does not, and isolating the risky step is the whole reason to have two.

**processing_runs could not describe a run of the email AI.** `threshold` was
`NOT NULL CHECK (threshold BETWEEN 0 AND 1)`, and a run of the email AI has no threshold.
The cheap answer was to write 0.0, which puts a number in a column where it means nothing.
This project puts provenance in columns rather than in someone's memory, so instead
`threshold` becomes nullable, `run_kind` says which pipeline the run belongs to, and a
paired CHECK ties the two together:

    CHECK ((run_kind = 'invoice') = (threshold IS NOT NULL))

That pattern is already used twice in this schema, on `outbound_messages.sent_at` and
`tasks.resolved_at`. Without `run_kind`, `SELECT AVG(threshold) FROM processing_runs`
silently mixes two different things the moment email runs exist.

**email_messages had no thread identity.** `ThreadAnalysisRecord.thread_id` is a required
string, and nothing in this codebase had any concept of a thread, so `thread_analysis` would
have stored a summary keyed to a string that joined to nothing. Two nullable columns fix
that: `thread_id`, and `thread_source` recording how the grouping was formed. Until intake
captures `In-Reply-To` and `References`, which is Luke's file, every row says `subject` and
every report sentence about threads has to say so. Same reasoning as `body_source`.

The processing_runs rebuild is the standard 12-step: new table, copy, drop, rename, with
foreign keys off for the swap so that `invoices.run_id` is not checked against a table that
briefly does not exist. Every existing run is an invoice run and keeps its threshold, so no
row changes meaning.
"""

import argparse
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage import backup_path as make_backup_path  # noqa: E402
from storage import connect, table_exists  # noqa: E402

FROM_VERSION = 9
TARGET_VERSION = 10

NEW_RUNS = """
CREATE TABLE processing_runs_new (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    model_name  TEXT    NOT NULL,
    threshold   REAL    CHECK (threshold IS NULL OR threshold BETWEEN 0 AND 1),
    doc_count   INTEGER NOT NULL DEFAULT 0,
    run_kind    TEXT    NOT NULL DEFAULT 'invoice'
                        CHECK (run_kind IN ('invoice','email')),
    CHECK ((run_kind = 'invoice') = (threshold IS NOT NULL))
)
"""


def current_version(conn):
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return row["v"] if row and row["v"] is not None else 0


def already_applied(conn):
    runs = [r[1] for r in conn.execute("PRAGMA table_info(processing_runs)")]
    emails = [r[1] for r in conn.execute("PRAGMA table_info(email_messages)")]
    return "run_kind" in runs and "thread_id" in emails


def migrate(db_path, dry_run=False):
    if not os.path.exists(db_path):
        print(f"[!] No database at {db_path}. Nothing to migrate.")
        return 1

    with connect(db_path) as conn:
        if not table_exists(conn, "email_messages"):
            print("[!] No 'email_messages' table. Run migration 004 first.")
            return 1
        if already_applied(conn):
            print(f"[*] run_kind and thread_id already exist. Database at version "
                  f"{current_version(conn)}.")
            return 0
        version = current_version(conn)
        if version != FROM_VERSION:
            print(f"[!] Expected schema version {FROM_VERSION}, found {version}. Refusing.")
            return 1
        runs_before = conn.execute("SELECT COUNT(*) FROM processing_runs").fetchone()[0]
        emails_before = conn.execute("SELECT COUNT(*) FROM email_messages").fetchone()[0]
        invoices_before = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]

    print(f"=== Migration 010: run kind and thread identity in {db_path} ===")
    print(f"[*] {runs_before} runs, {emails_before} emails, {invoices_before} invoices "
          f"at schema version {version}.")

    if dry_run:
        print("[*] Dry run: planning only, no writes.\n")
        print("Would rebuild processing_runs with a nullable threshold and a run_kind column,")
        print("defaulting every existing row to 'invoice' so no run changes meaning.")
        print("Would add thread_id and thread_source to email_messages, both NULL until")
        print("something populates them. Header capture at intake is Luke's file.")
        print("\nDry run only. Re-run without --dry-run to apply.")
        return 0

    # Not simply the timestamp every earlier migration uses. The quickstart runs these two
    # back to back, both land in the same second, and the second copy then overwrites the
    # first, leaving only the state after 010 and no way back to the state before it.
    backup_path = make_backup_path(db_path)
    suffix = 2
    while os.path.exists(backup_path):
        backup_path = f"{db_path}.bak-{stamp}-{suffix}"
        suffix += 1
    shutil.copy2(db_path, backup_path)
    print(f"[*] Backed up to {backup_path}")

    conn = connect(db_path)
    try:
        # Foreign keys must be off for the drop-and-rename, per the SQLite procedure for
        # schema changes ALTER TABLE cannot express: invoices.run_id points at the table
        # being dropped. Re-enabled and checked below, the same shape migration 003 uses.
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN")
        conn.executescript(NEW_RUNS)
        conn.execute("""
            INSERT INTO processing_runs_new (
                run_id, started_at, finished_at, model_name, threshold, doc_count, run_kind
            )
            SELECT run_id, started_at, finished_at, model_name, threshold, doc_count, 'invoice'
            FROM processing_runs
        """)
        conn.execute("DROP TABLE processing_runs")
        conn.execute("ALTER TABLE processing_runs_new RENAME TO processing_runs")

        conn.execute("ALTER TABLE email_messages ADD COLUMN thread_id TEXT")
        conn.execute(
            "ALTER TABLE email_messages ADD COLUMN thread_source TEXT "
            "CHECK (thread_source IS NULL OR thread_source IN ('headers','subject','manual'))"
        )

        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (TARGET_VERSION,))

        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"foreign key violations after rebuild: {violations}")
        conn.commit()
    except Exception:
        conn.rollback()
        print(f"[!] Migration failed and was rolled back. Restore from {backup_path} if needed.")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.close()

    with connect(db_path) as conn:
        runs_after = conn.execute("SELECT COUNT(*) FROM processing_runs").fetchone()[0]
        emails_after = conn.execute("SELECT COUNT(*) FROM email_messages").fetchone()[0]
        invoices_after = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
        orphans = conn.execute(
            "SELECT COUNT(*) FROM invoices i "
            "LEFT JOIN processing_runs r ON r.run_id = i.run_id "
            "WHERE r.run_id IS NULL").fetchone()[0]
        version_after = current_version(conn)

    print("\n" + "=" * 62)
    print(f"  runs              {runs_before} -> {runs_after}, all run_kind 'invoice'")
    print(f"  emails            {emails_before} -> {emails_after}")
    print(f"  invoices          {invoices_before} -> {invoices_after}, {orphans} orphaned")
    print(f"  version           {version} -> {version_after}")
    print("=" * 62)

    if (runs_after, emails_after, invoices_after) != (runs_before, emails_before, invoices_before):
        print("[!] Row count changed during a migration that moves no data. Investigate.")
        return 1
    if orphans:
        print("[!] An invoice lost its run in the rebuild. Restore from the backup.")
        return 1

    print("Every run is an invoice run and keeps its threshold. thread_id and thread_source")
    print("are NULL on every email until something populates them.")
    print("Next: ./.venv/bin/python migrations/011_email_analysis.py")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Add run kind and thread identity.")
    parser.add_argument("--db", default="workflow_platform.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(migrate(args.db, dry_run=args.dry_run))
