"""Tests for the migration chain.

Builds a throwaway legacy database in pytest's tmp_path and runs 001 -> 002 -> 003 over it.
Nothing here touches workflow_platform.db.

The most important test is test_migrated_matches_fresh: it proves that a database built by
replaying every migration ends up with the same shape as one created directly from
storage.DDL. Without it, the two paths can drift apart silently and only diverge on a
teammate's machine.
"""

import importlib.util
import json
import os
import sqlite3
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from storage import SCHEMA_VERSION, StorageManager, connect  # noqa: E402


def load_migration(filename):
    """Migrations start with digits, so they cannot be imported by name."""
    path = os.path.join(REPO_ROOT, "migrations", filename)
    spec = importlib.util.spec_from_file_location(filename[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m001 = load_migration("001_normalise.py")
m002 = load_migration("002_rename_total_source.py")
m003 = load_migration("003_fix_date_check.py")
m004 = load_migration("004_email_and_tasks.py")
m005 = load_migration("005_rename_validation_status.py")
m006 = load_migration("006_storage_completion.py")
m007 = load_migration("007_post_approval.py")
m008 = load_migration("008_email_body.py")
m009 = load_migration("009_attachment_names.py")
m010 = load_migration("010_run_kind_and_threads.py")
m011 = load_migration("011_email_analysis.py")


LEGACY_DDL = """
CREATE TABLE workflow_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence_score REAL NOT NULL,
    invoice_number TEXT,
    vendor_name TEXT,
    date TEXT,
    total_amount REAL,
    currency TEXT,
    archive_path TEXT,
    raw_json TEXT,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# The three real rows as they existed before migration 001, including the 0.00 totals and
# the "Grand Total" line item that made recovery possible.
LEGACY_ROWS = [
    ("sample_invoice_1_INV-2026-001.pdf", "NeedsReview", 0.4, None,
     "Apex Cloud Solutions Pty Ltd", None, 0.0, "Unknown", "./archive/inv1.pdf",
     json.dumps({"items": [
         {"description": "Dedicated Cloud Compute", "quantity": 2.0, "unit_price": 450.0, "total": 900.0},
         {"description": "High Performance SSD Storage", "quantity": 3.0, "unit_price": 120.0, "total": 360.0},
         {"description": "Managed Database Service", "quantity": 1.0, "unit_price": 240.0, "total": 240.0},
     ]})),
    ("sample_invoice_2_INV-2026-002.pdf", "NeedsReview", 0.4, None,
     "NextGen Hardware Supplies", None, 0.0, "Unknown", "./archive/inv2.pdf",
     json.dumps({"items": [
         {"description": "Ergonomic Office Chair", "quantity": 5.0, "unit_price": 350.0, "total": 1750.0},
         {"description": "USB-C Docking Station", "quantity": 5.0, "unit_price": 180.0, "total": 900.0},
         {"description": "Grand Total", "quantity": 1.0, "unit_price": 0.0, "total": 2650.0},
     ]})),
    ("sample_invoice_3_INV-2026-003.pdf", "NeedsReview", 0.4, None,
     "Synthetix AI Consulting", None, 0.0, "Unknown", "./archive/inv3.pdf",
     json.dumps({"items": [
         {"description": "Workflow Automation Consulting", "quantity": 10.0, "unit_price": 150.0, "total": 1500.0},
         {"description": "Local LLM Pipeline Setup", "quantity": 1.0, "unit_price": 850.0, "total": 850.0},
     ]})),
]

# Independently transcribed, the same figures evaluation/ground_truth.json carries.
GROUND_TRUTH_CENTS = {
    "sample_invoice_1_INV-2026-001.pdf": 150000,
    "sample_invoice_2_INV-2026-002.pdf": 265000,
    "sample_invoice_3_INV-2026-003.pdf": 235000,
}


@pytest.fixture
def legacy_db(tmp_path):
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.execute(LEGACY_DDL)
    conn.executemany(
        "INSERT INTO workflow_records (file_name, status, confidence_score, invoice_number, "
        "vendor_name, date, total_amount, currency, archive_path, raw_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)", LEGACY_ROWS)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def migrated_db(legacy_db):
    assert m001.migrate(legacy_db) == 0
    assert m002.migrate(legacy_db) == 0
    assert m003.migrate(legacy_db) == 0
    assert m004.migrate(legacy_db) == 0
    assert m005.migrate(legacy_db) == 0
    assert m006.migrate(legacy_db) == 0
    assert m007.migrate(legacy_db) == 0
    assert m008.migrate(legacy_db) == 0
    assert m009.migrate(legacy_db) == 0
    assert m010.migrate(legacy_db) == 0
    assert m011.migrate(legacy_db) == 0
    return legacy_db


# =====================================================================
# Migration 001
# =====================================================================
class TestNormalise:
    def test_recovers_every_trapped_total(self, legacy_db):
        """The headline result: three totals stored as 0.00 come back correct, with no
        change to the extractor."""
        m001.migrate(legacy_db)
        with connect(legacy_db) as conn:
            rows = conn.execute("SELECT file_name, total_cents FROM invoices").fetchall()
        assert {r["file_name"]: r["total_cents"] for r in rows} == GROUND_TRUTH_CENTS

    def test_recovered_totals_are_labelled_not_extracted(self, legacy_db):
        m001.migrate(legacy_db)
        with connect(legacy_db) as conn:
            sources = [r["extraction_source"] for r in
                       conn.execute("SELECT extraction_source FROM invoices")]
        assert sources == ["fallback"] * 3

    def test_flags_the_grand_total_line_and_nothing_else(self, legacy_db):
        m001.migrate(legacy_db)
        with connect(legacy_db) as conn:
            flagged = conn.execute(
                "SELECT description FROM line_items WHERE is_summary_row = 1").fetchall()
        assert [r["description"] for r in flagged] == ["Grand Total"]

    def test_line_items_leave_the_json_blob(self, legacy_db):
        m001.migrate(legacy_db)
        with connect(legacy_db) as conn:
            assert conn.execute("SELECT COUNT(*) c FROM line_items").fetchone()["c"] == 8

    def test_keeps_the_legacy_table(self, legacy_db):
        """Nothing is dropped. workflow_records_v1 is the only record of pre-migration state."""
        m001.migrate(legacy_db)
        with connect(legacy_db) as conn:
            assert conn.execute("SELECT COUNT(*) c FROM workflow_records_v1").fetchone()["c"] == 3

    def test_is_idempotent(self, legacy_db):
        m001.migrate(legacy_db)
        assert m001.migrate(legacy_db) == 0
        with connect(legacy_db) as conn:
            assert conn.execute("SELECT COUNT(*) c FROM invoices").fetchone()["c"] == 3

    def test_creates_one_run_for_the_legacy_rows(self, legacy_db):
        m001.migrate(legacy_db)
        with connect(legacy_db) as conn:
            runs = conn.execute("SELECT doc_count FROM processing_runs").fetchall()
        assert len(runs) == 1 and runs[0]["doc_count"] == 3

    def test_backs_the_database_up(self, legacy_db):
        m001.migrate(legacy_db)
        directory = os.path.dirname(legacy_db)
        assert any(f.startswith("legacy.db.bak-") for f in os.listdir(directory))

    def test_refuses_a_database_with_nothing_to_migrate(self, tmp_path):
        path = str(tmp_path / "empty.db")
        sqlite3.connect(path).execute("CREATE TABLE unrelated (x INTEGER)")
        assert m001.migrate(path) == 1


# =====================================================================
# Migration 002
# =====================================================================
class TestRenameTotalSource:
    def test_renames_the_column(self, legacy_db):
        m001.migrate(legacy_db)
        m002.migrate(legacy_db)
        with connect(legacy_db) as conn:
            columns = [r["name"] for r in conn.execute("PRAGMA table_info(invoices)")]
        assert "total_source" in columns and "extraction_source" not in columns

    def test_does_not_move_any_money(self, legacy_db):
        m001.migrate(legacy_db)
        m002.migrate(legacy_db)
        with connect(legacy_db) as conn:
            rows = conn.execute("SELECT file_name, total_cents FROM invoices").fetchall()
        assert {r["file_name"]: r["total_cents"] for r in rows} == GROUND_TRUTH_CENTS

    def test_carries_the_check_constraint_across(self, legacy_db):
        m001.migrate(legacy_db)
        m002.migrate(legacy_db)
        with connect(legacy_db) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("UPDATE invoices SET total_source = 'vibes'")

    def test_is_idempotent(self, legacy_db):
        m001.migrate(legacy_db)
        m002.migrate(legacy_db)
        assert m002.migrate(legacy_db) == 0

    def test_refuses_to_run_out_of_order(self, legacy_db):
        """002 must not run against an unmigrated database."""
        assert m002.migrate(legacy_db) == 1


# =====================================================================
# Migration 003
# =====================================================================
class TestFixDateCheck:
    def test_the_old_constraint_rejected_every_real_date(self, legacy_db):
        """Documents the bug this migration exists to fix."""
        m001.migrate(legacy_db)
        m002.migrate(legacy_db)
        with connect(legacy_db) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("UPDATE invoices SET invoice_date = '2026-08-10' WHERE invoice_id = 1")

    def test_a_valid_date_is_accepted_afterwards(self, migrated_db):
        with connect(migrated_db) as conn:
            conn.execute("UPDATE invoices SET invoice_date = '2026-08-10' WHERE invoice_id = 1")
            assert conn.execute(
                "SELECT invoice_date d FROM invoices WHERE invoice_id = 1").fetchone()["d"] == "2026-08-10"

    @pytest.mark.parametrize("bad", ["not-a-date", "20260810", "2026-8-10", "abcd-ef-gh"])
    def test_malformed_dates_are_still_rejected(self, migrated_db, bad):
        with connect(migrated_db) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("UPDATE invoices SET invoice_date = ? WHERE invoice_id = 1", (bad,))

    def test_the_rebuild_moves_no_data(self, migrated_db):
        with connect(migrated_db) as conn:
            rows = conn.execute("SELECT file_name, total_cents FROM invoices").fetchall()
            items = conn.execute("SELECT COUNT(*) c FROM line_items").fetchone()["c"]
        assert {r["file_name"]: r["total_cents"] for r in rows} == GROUND_TRUTH_CENTS
        assert items == 8

    def test_foreign_keys_survive_the_rebuild(self, migrated_db):
        with connect(migrated_db) as conn:
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
            conn.execute("DELETE FROM invoices WHERE invoice_id = 1")
            assert conn.execute(
                "SELECT COUNT(*) c FROM line_items WHERE invoice_id = 1").fetchone()["c"] == 0

    def test_indexes_survive_the_rebuild(self, migrated_db):
        with connect(migrated_db) as conn:
            names = {r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='invoices' "
                "AND sql IS NOT NULL")}
        assert names == {"ux_invoices_content", "ix_invoices_status",
                         "ix_invoices_vendor", "ix_invoices_run"}

    def test_is_idempotent(self, migrated_db):
        assert m003.migrate(migrated_db) == 0


# =====================================================================
# Migration 004
# =====================================================================
class TestEmailAndTasks:
    @pytest.fixture
    def at_v3(self, legacy_db):
        m001.migrate(legacy_db)
        m002.migrate(legacy_db)
        m003.migrate(legacy_db)
        return legacy_db

    def test_creates_both_tables(self, at_v3):
        m004.migrate(at_v3)
        with connect(at_v3) as conn:
            names = {r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"email_messages", "tasks"} <= names

    def test_adds_the_email_link_as_nullable(self, at_v3):
        """Existing invoices predate intake recording emails. NULL is the correct value:
        it means "not known to have arrived by email", not "missing data"."""
        m004.migrate(at_v3)
        with connect(at_v3) as conn:
            rows = conn.execute("SELECT email_id FROM invoices").fetchall()
        assert [r["email_id"] for r in rows] == [None, None, None]

    def test_is_additive_and_moves_no_money(self, at_v3):
        m004.migrate(at_v3)
        with connect(at_v3) as conn:
            rows = conn.execute("SELECT file_name, total_cents FROM invoices").fetchall()
        assert {r["file_name"]: r["total_cents"] for r in rows} == GROUND_TRUTH_CENTS

    def test_is_idempotent(self, at_v3):
        m004.migrate(at_v3)
        assert m004.migrate(at_v3) == 0

    def test_refuses_to_run_out_of_order(self, legacy_db):
        assert m004.migrate(legacy_db) == 1

    def test_the_one_open_task_rule_survives_the_migration(self, at_v3):
        m004.migrate(at_v3)
        with connect(at_v3) as conn:
            conn.execute("INSERT INTO tasks (invoice_id, task_type) VALUES (1, 'Review')")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO tasks (invoice_id, task_type) VALUES (1, 'Review')")


# =====================================================================
# Migrations 006 and 007
# =====================================================================
class TestStorageCompletionAndHandoff:
    @pytest.fixture
    def at_v5(self, legacy_db):
        for m in (m001, m002, m003, m004, m005):
            assert m.migrate(legacy_db) == 0
        return legacy_db

    def test_006_adds_the_three_columns(self, at_v5):
        m006.migrate(at_v5)
        with connect(at_v5) as conn:
            columns = [r["name"] for r in conn.execute("PRAGMA table_info(invoices)")]
        assert {"vendor_source", "document_type", "reconciliation"} <= set(columns)

    def test_006_backfills_reconciliation_from_stored_data(self, at_v5):
        """The three legacy invoices have totals recovered by summing their own line items,
        so every one must reconcile exactly."""
        m006.migrate(at_v5)
        with connect(at_v5) as conn:
            states = [r["reconciliation"] for r in
                      conn.execute("SELECT reconciliation FROM invoices")]
        assert states == ["exact"] * 3

    def test_006_moves_no_money(self, at_v5):
        m006.migrate(at_v5)
        with connect(at_v5) as conn:
            rows = conn.execute("SELECT file_name, total_cents FROM invoices").fetchall()
        assert {r["file_name"]: r["total_cents"] for r in rows} == GROUND_TRUTH_CENTS

    def test_006_is_idempotent(self, at_v5):
        m006.migrate(at_v5)
        assert m006.migrate(at_v5) == 0

    def test_007_extends_task_type_without_losing_tasks(self, at_v5):
        m006.migrate(at_v5)
        with connect(at_v5) as conn:
            conn.execute("INSERT INTO tasks (invoice_id, task_type) VALUES (1, 'Review')")
            conn.commit()
        m007.migrate(at_v5)
        with connect(at_v5) as conn:
            assert conn.execute("SELECT COUNT(*) c FROM tasks").fetchone()["c"] == 1
            conn.execute("INSERT INTO tasks (invoice_id, task_type) VALUES (1, 'Payment')")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO tasks (invoice_id, task_type) VALUES (1, 'Nonsense')")

    def test_007_creates_an_empty_outbox(self, at_v5):
        m006.migrate(at_v5)
        m007.migrate(at_v5)
        with connect(at_v5) as conn:
            assert conn.execute("SELECT COUNT(*) c FROM outbound_messages").fetchone()["c"] == 0

    def test_007_refuses_to_run_before_006(self, at_v5):
        assert m007.migrate(at_v5) == 1

    def test_007_is_idempotent(self, at_v5):
        m006.migrate(at_v5)
        m007.migrate(at_v5)
        assert m007.migrate(at_v5) == 0


# =====================================================================
# The whole chain
# =====================================================================
class TestChain:
    def test_ends_at_the_current_schema_version(self, migrated_db):
        with connect(migrated_db) as conn:
            assert conn.execute(
                "SELECT MAX(version) v FROM schema_version").fetchone()["v"] == SCHEMA_VERSION

    def test_records_every_step(self, migrated_db):
        with connect(migrated_db) as conn:
            versions = [r["version"] for r in
                        conn.execute("SELECT version FROM schema_version ORDER BY version")]
        assert versions == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]

    def test_migrated_matches_fresh(self, migrated_db, tmp_path):
        """A replayed migration chain and a fresh storage.DDL database must agree.

        If they drift, teammates who migrated and teammates who started clean are running
        different schemas, and nothing tells them.
        """
        fresh = str(tmp_path / "fresh.db")
        StorageManager(fresh)

        def shape(path):
            with connect(path) as conn:
                tables = {}
                for table in ("processing_runs", "invoices", "line_items",
                              "email_messages", "tasks", "outbound_messages",
                              "email_analysis", "thread_analysis", "action_items",
                              "thread_decisions"):
                    tables[table] = [
                        (r["name"], r["type"], r["notnull"], r["dflt_value"])
                        for r in conn.execute(f"PRAGMA table_info({table})")
                    ]
                indexes = sorted(
                    r["name"] for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"))
            return tables, indexes

        assert shape(migrated_db) == shape(fresh)

    def test_the_pipeline_can_write_to_a_migrated_database(self, migrated_db):
        """The real risk of a migration: it produces a schema the code cannot use."""
        store = StorageManager(migrated_db)
        run_id = store.start_run("llama3.2", 0.8)
        result = store.save_invoice(
            run_id=run_id, file_name="new.pdf", source_sha256="f" * 64,
            content_sha256="e" * 64,
            extracted={"invoice_number": "INV-9", "vendor_name": "Test Co",
                       "date": "2026-08-10", "total_amount": 42.5, "currency": "AUD",
                       "items": [{"description": "Thing", "quantity": 1,
                                  "unit_price": 42.5, "total": 42.5}]},
            validation_score=0.9, validation_status="Validated",
            archive_path="/tmp/new.pdf", raw_json="{}")
        assert result["was_update"] is False
        assert result["total_cents"] == 4250
        assert result["total_source"] == "model"

    def test_email_and_task_flow_works_on_a_migrated_database(self, migrated_db):
        store = StorageManager(migrated_db)
        email = store.record_email("<x@mail>", "billing@vendor.com", "Invoice", attachment_count=1)
        assert store.has_seen_email("<x@mail>")
        task = store.open_task(1, "Review", "score below threshold")
        assert task["was_created"] is True
        assert len(store.open_tasks()) == 1
        assert store.resolve_tasks(1, "Done") == 1
        assert store.open_tasks() == []
        assert email["email_id"] > 0

    def test_rerunning_the_whole_sequence_is_a_clean_no_op(self, migrated_db):
        """A teammate following the quickstart runs every migration in order. Doing that
        twice must succeed, not report failure on the ones already applied."""
        for module in (m001, m002, m003, m004, m005, m006, m007,
                       m008, m009, m010, m011):
            assert module.migrate(migrated_db) == 0, f"{module.__name__} failed on re-run"


# =====================================================================
# Migration 008
# =====================================================================
class TestEmailBodyMigration:
    def test_008_adds_both_columns(self, migrated_db):
        with connect(migrated_db) as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(email_messages)")]
        assert "body_text" in cols
        assert "body_source" in cols

    def test_008_moves_no_rows(self, migrated_db):
        """Additive. An email recorded before the migration survives it unchanged."""
        store = StorageManager(migrated_db)
        store.record_email(message_id="<pre@x>", sender="a@x", subject="Before")
        with connect(migrated_db) as conn:
            before = conn.execute("SELECT COUNT(*) c FROM email_messages").fetchone()["c"]
        assert m008.migrate(migrated_db) == 0          # already applied, no-op
        with connect(migrated_db) as conn:
            after = conn.execute("SELECT COUNT(*) c FROM email_messages").fetchone()["c"]
            row = conn.execute(
                "SELECT subject, body_text FROM email_messages "
                "WHERE message_id = '<pre@x>'").fetchone()
        assert after == before
        assert row["subject"] == "Before"
        assert row["body_text"] is None

    def test_008_is_idempotent(self, migrated_db):
        assert m008.migrate(migrated_db) == 0
        assert m008.migrate(migrated_db) == 0

    def test_008_refuses_an_invalid_body_source(self, migrated_db):
        """The CHECK is enforced by SQLite, not only by the Python guard."""
        with connect(migrated_db) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO email_messages (message_id, sender, body_text, body_source) "
                    "VALUES ('<bad@x>', 'a@x', 'text', 'invented')")


# =====================================================================
# Migration 009
# =====================================================================
class TestAttachmentNames:
    def test_009_adds_the_column(self, migrated_db):
        with connect(migrated_db) as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(email_messages)")]
        assert "attachment_names" in cols

    def test_009_is_idempotent(self, migrated_db):
        assert m009.migrate(migrated_db) == 0
        assert m009.migrate(migrated_db) == 0

    def test_a_document_can_be_traced_to_its_email(self, migrated_db):
        """The link that closed the gap: inbox/<file> back to the message that sent it."""
        store = StorageManager(migrated_db)
        result = store.record_email(
            message_id="<x@v>", sender="billing@v.io", received_at="2026-09-01 09:00:00",
            attachment_names=["INV-900.pdf", "statement.pdf"])
        found = store.email_for_attachment("INV-900.pdf")
        assert found["email_id"] == result["email_id"]
        assert found["sender"] == "billing@v.io"

    def test_an_unmatched_file_returns_none(self, migrated_db):
        """A file dropped straight into inbox/ never had an email. Not an error."""
        store = StorageManager(migrated_db)
        store.record_email(message_id="<y@v>", sender="a@v.io",
                           attachment_names=["other.pdf"])
        assert store.email_for_attachment("INV-999.pdf") is None

    def test_a_partial_name_does_not_match(self, migrated_db):
        """'INV-9.pdf' must not match 'INV-90.pdf'. The delimiters exist for this."""
        store = StorageManager(migrated_db)
        store.record_email(message_id="<z@v>", sender="a@v.io",
                           attachment_names=["INV-90.pdf"])
        assert store.email_for_attachment("INV-9.pdf") is None

    def test_the_newest_match_wins(self, migrated_db):
        """Two emails attaching the same name cannot be told apart. Documented, not fixed."""
        store = StorageManager(migrated_db)
        store.record_email(message_id="<old@v>", sender="old@v.io",
                           received_at="2026-01-01 09:00:00", attachment_names=["dup.pdf"])
        newer = store.record_email(message_id="<new@v>", sender="new@v.io",
                                   received_at="2026-08-01 09:00:00",
                                   attachment_names=["dup.pdf"])
        assert store.email_for_attachment("dup.pdf")["email_id"] == newer["email_id"]


# =====================================================================
# Migration 010
# =====================================================================
class TestRunKindAndThreads:
    """The only migration since 003 that rewrites an existing table, and invoices.run_id
    points at it. These tests are mostly about what must not change."""

    @pytest.fixture
    def before_010(self, legacy_db):
        for module in (m001, m002, m003, m004, m005, m006, m007, m008, m009):
            assert module.migrate(legacy_db) == 0
        return legacy_db

    def test_every_existing_run_becomes_an_invoice_run(self, before_010):
        with connect(before_010) as conn:
            thresholds = [r["threshold"] for r in
                          conn.execute("SELECT threshold FROM processing_runs")]
        m010.migrate(before_010)
        with connect(before_010) as conn:
            rows = conn.execute(
                "SELECT run_kind, threshold FROM processing_runs ORDER BY run_id").fetchall()
        assert [r["run_kind"] for r in rows] == ["invoice"] * len(rows)
        assert [r["threshold"] for r in rows] == thresholds

    def test_no_invoice_loses_its_run(self, before_010):
        """The rebuild drops the table invoices.run_id references. This is the failure that
        would matter and it is checked inside the migration too, before it commits."""
        m010.migrate(before_010)
        with connect(before_010) as conn:
            orphans = conn.execute(
                "SELECT COUNT(*) c FROM invoices i "
                "LEFT JOIN processing_runs r ON r.run_id = i.run_id "
                "WHERE r.run_id IS NULL").fetchone()["c"]
            assert orphans == 0
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

    def test_the_rebuild_moves_no_data(self, before_010):
        with connect(before_010) as conn:
            before = conn.execute("SELECT COUNT(*) c FROM invoices").fetchone()["c"]
        m010.migrate(before_010)
        with connect(before_010) as conn:
            assert conn.execute("SELECT COUNT(*) c FROM invoices").fetchone()["c"] == before

    def test_an_email_run_becomes_possible(self, before_010):
        m010.migrate(before_010)
        with connect(before_010) as conn:
            conn.execute("INSERT INTO processing_runs (model_name, threshold, run_kind) "
                         "VALUES ('llama3.2', NULL, 'email')")
            conn.commit()
            assert conn.execute(
                "SELECT COUNT(*) c FROM processing_runs WHERE run_kind = 'email'"
            ).fetchone()["c"] == 1

    def test_the_thread_columns_start_null(self, before_010):
        m010.migrate(before_010)
        with connect(before_010) as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(email_messages)")]
            assert "thread_id" in cols and "thread_source" in cols
            assert conn.execute(
                "SELECT COUNT(*) c FROM email_messages WHERE thread_id IS NOT NULL"
            ).fetchone()["c"] == 0

    def test_an_invented_thread_source_is_rejected(self, before_010):
        m010.migrate(before_010)
        with connect(before_010) as conn:
            conn.execute("INSERT INTO email_messages (message_id, sender) VALUES ('<x>', 's')")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("UPDATE email_messages SET thread_source = 'guessed'")

    def test_is_idempotent(self, before_010):
        m010.migrate(before_010)
        assert m010.migrate(before_010) == 0

    def test_refuses_to_run_out_of_order(self, legacy_db):
        m001.migrate(legacy_db)
        assert m010.migrate(legacy_db) == 1


# =====================================================================
# Migration 011
# =====================================================================
class TestEmailAnalysisTables:
    @pytest.fixture
    def before_011(self, legacy_db):
        for module in (m001, m002, m003, m004, m005, m006, m007, m008, m009, m010):
            assert module.migrate(legacy_db) == 0
        return legacy_db

    def test_creates_four_empty_tables(self, before_011):
        m011.migrate(before_011)
        with connect(before_011) as conn:
            for table in ("email_analysis", "thread_analysis",
                          "action_items", "thread_decisions"):
                assert conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"] == 0

    def test_touches_no_existing_row(self, before_011):
        with connect(before_011) as conn:
            before = conn.execute("SELECT COUNT(*) c FROM invoices").fetchone()["c"]
        m011.migrate(before_011)
        with connect(before_011) as conn:
            assert conn.execute("SELECT COUNT(*) c FROM invoices").fetchone()["c"] == before

    def test_refuses_without_migration_010(self, legacy_db):
        for module in (m001, m002, m003, m004, m005, m006, m007, m008, m009):
            assert module.migrate(legacy_db) == 0
        assert m011.migrate(legacy_db) == 1

    def test_is_idempotent(self, before_011):
        m011.migrate(before_011)
        assert m011.migrate(before_011) == 0

    def test_the_sql_comes_from_storage_ddl(self, before_011):
        """The tables are sliced out of storage.DDL rather than copied here, so a fresh
        database and a migrated one cannot drift. If the marker comment is ever removed,
        this fails rather than silently writing a second definition."""
        assert "CREATE TABLE email_analysis" in m011.ddl_tail()
        assert len(m011.created_objects(m011.ddl_tail())) == 10

    def test_the_pipeline_can_store_an_analysis_afterwards(self, before_011):
        """The real risk of a migration: it produces a schema the code cannot use."""
        m011.migrate(before_011)
        store = StorageManager(before_011)
        store.record_email("<m@mail>", "pm@client.com", "Budget")
        run_id = store.start_email_run("llama3.2:latest")
        result = store.save_email_analysis({
            "message_id": "<m@mail>", "run_id": run_id,
            "processed_at": "2026-09-17T09:00:00Z",
            "validation_status": "Validated", "validation_reason": None, "attempt_count": 1,
            "analysis": {"category": "Invoice", "summary": "An invoice arrived.",
                         "action_items": [{"task": "Pay it", "owner": "Neo",
                                           "deadline_text": "by 30 September 2026",
                                           "evidence_quote": "Pay by 30 September 2026."}]},
        })
        assert result["was_update"] is False
        assert result["dated_count"] == 1
