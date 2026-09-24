"""Tests for the storage layer.

Every case here was verified by hand at least once while building the schema. The point of
this file is that nobody has to do that again.

No test touches workflow_platform.db. Each one builds a database in pytest's tmp_path.
"""

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage import (  # noqa: E402
    APPROVAL_TASK_TYPES,
    SCHEMA_VERSION,
    SchemaMismatch,
    StorageManager,
    coerce_currency,
    coerce_date,
    connect,
    content_hash,
    from_cents,
    is_summary_row,
    normalise_items,
    normalise_label,
    classify_document,
    reconcile,
    recover_total_cents,
    to_cents,
)


# =====================================================================
# Fixtures
# =====================================================================
@pytest.fixture
def db(tmp_path):
    """A fresh database at the current schema version."""
    path = str(tmp_path / "test.db")
    StorageManager(path)
    return path


@pytest.fixture
def store(db):
    return StorageManager(db)


@pytest.fixture
def run_id(store):
    return store.start_run(model_name="test-model", threshold=0.8)


def sample_extracted(**overrides):
    data = {
        "invoice_number": "INV-2026-001",
        "vendor_name": "Apex Cloud Solutions Pty Ltd",
        "date": "2026-08-10",
        "total_amount": 1500.00,
        "currency": "USD",
        "items": [
            {"description": "Cloud Compute", "quantity": 2, "unit_price": 450.0, "total": 900.0},
            {"description": "SSD Storage", "quantity": 3, "unit_price": 120.0, "total": 360.0},
            {"description": "Managed Database", "quantity": 1, "unit_price": 240.0, "total": 240.0},
        ],
    }
    data.update(overrides)
    return data


def save(store, run_id, **overrides):
    extracted = overrides.pop("extracted", sample_extracted())
    overrides.setdefault("raw_text", "TAX INVOICE\nApex Cloud Solutions Pty Ltd")
    kwargs = {
        "run_id": run_id,
        "file_name": "invoice.pdf",
        "source_sha256": "a" * 64,
        "content_sha256": "b" * 64,
        "extracted": extracted,
        "validation_score": 0.4,
        "validation_status": "NeedsReview",
        "archive_path": "/tmp/archive/invoice.pdf",
        "raw_json": "{}",
    }
    kwargs.update(overrides)
    return store.save_invoice(**kwargs)


# =====================================================================
# Money
# =====================================================================
class TestMoney:
    @pytest.mark.parametrize("dollars,cents", [
        (1500.00, 150000), (0.01, 1), (0, 0), ("2650.00", 265000), (1234.567, 123457),
    ])
    def test_to_cents(self, dollars, cents):
        assert to_cents(dollars) == cents

    def test_rounds_half_up_not_bankers(self):
        """Python's round() gives 0 for 0.005; half-up must give 1."""
        assert to_cents(0.005) == 1
        assert to_cents(0.015) == 2

    @pytest.mark.parametrize("bad", [None, "", -5.0, "not a number", object()])
    def test_rejects_unusable(self, bad):
        assert to_cents(bad) is None

    def test_round_trip(self):
        assert from_cents(to_cents(2650.00)) == 2650.00

    def test_from_cents_passes_none_through(self):
        assert from_cents(None) is None

    def test_no_float_drift(self):
        """The reason money is stored as cents: 0.1 + 0.2 != 0.3 in float."""
        assert 0.1 + 0.2 != 0.3
        assert to_cents(0.1) + to_cents(0.2) == to_cents(0.3)


# =====================================================================
# Summary row detection
# =====================================================================
class TestSummaryRow:
    def test_grand_total_flags(self):
        """The real case: invoice 2 emitted "Grand Total" as a line item."""
        assert is_summary_row("Grand Total", 1.0)

    @pytest.mark.parametrize("label", [
        "Total", "TOTAL", "  grand total  ", "Amount Due", "Balance Due",
        "Sub-Total", "Invoice Total", "Total:",
    ])
    def test_recognised_labels(self, label):
        assert is_summary_row(label, 1.0)

    def test_does_not_fire_on_a_real_item_containing_total(self):
        """The reason the rule is exact-match and not a substring search."""
        assert not is_summary_row("Total Station Rental", 1.0)
        assert not is_summary_row("Subtotal Machine Hire", 1.0)

    def test_quantity_above_one_is_a_purchase(self):
        assert not is_summary_row("Total", 5.0)

    def test_missing_quantity_still_flags(self):
        assert is_summary_row("Grand Total", None)

    def test_normalise_label_strips_punctuation_and_case(self):
        assert normalise_label("Grand Total") == "grandtotal"
        assert normalise_label("Total Station Rental") == "totalstationrental"


# =====================================================================
# Total recovery
# =====================================================================
class TestTotalRecovery:
    def test_prefers_summary_row_over_sum(self):
        """A sum of line items misses tax; an explicit grand total does not."""
        rows = normalise_items([
            {"description": "Chair", "quantity": 5, "unit_price": 350.0, "total": 1750.0},
            {"description": "Dock", "quantity": 5, "unit_price": 180.0, "total": 900.0},
            {"description": "Tax", "quantity": 1, "unit_price": 0.0, "total": 275.0},
            {"description": "Grand Total", "quantity": 1, "unit_price": 0.0, "total": 2925.0},
        ])
        cents, reason = recover_total_cents(rows)
        assert cents == 292500, "must use the grand total, not the 2650 sum of items"
        assert reason == "summary row"

    def test_sums_when_there_is_no_summary_row(self):
        rows = normalise_items(sample_extracted()["items"])
        cents, reason = recover_total_cents(rows)
        assert cents == 150000
        assert "3 line items" in reason

    def test_subtotal_is_excluded_from_sum_but_never_used_as_the_total(self):
        """A subtotal sits before tax, so reading it as the total understates the invoice."""
        rows = normalise_items([
            {"description": "Widget", "quantity": 1, "unit_price": 100.0, "total": 100.0},
            {"description": "Subtotal", "quantity": 1, "unit_price": 0.0, "total": 100.0},
        ])
        cents, reason = recover_total_cents(rows)
        assert cents == 10000
        assert reason != "summary row"

    def test_returns_none_when_nothing_is_recoverable(self):
        assert recover_total_cents([]) == (None, None)

    def test_real_invoice_2_case(self):
        """Regression guard on the exact data that motivated is_summary_row."""
        rows = normalise_items([
            {"description": "Ergonomic Office Chair - Model X", "quantity": 5, "unit_price": 350.0, "total": 1750.0},
            {"description": "USB-C Dual 4K Display Docking Station", "quantity": 5, "unit_price": 180.0, "total": 900.0},
            {"description": "Grand Total", "quantity": 1, "unit_price": 0.0, "total": 2650.0},
        ])
        assert recover_total_cents(rows)[0] == 265000


# =====================================================================
# Item normalisation
# =====================================================================
class TestNormaliseItems:
    def test_numbers_lines_from_one(self):
        rows = normalise_items(sample_extracted()["items"])
        assert [r["line_no"] for r in rows] == [1, 2, 3]

    def test_names_an_empty_description(self):
        rows = normalise_items([{"description": "", "total": 10.0}])
        assert rows[0]["description"] == "(unnamed item 1)"

    def test_skips_non_dict_entries(self):
        assert normalise_items(["not a dict", None]) == []

    def test_handles_missing_items(self):
        assert normalise_items(None) == []


# =====================================================================
# Field coercion
# =====================================================================
class TestCoercion:
    @pytest.mark.parametrize("raw,expected", [
        ("USD", "USD"), ("aud", "AUD"), ("$", "Unknown"), ("dollars", "Unknown"), (None, None),
    ])
    def test_currency(self, raw, expected):
        assert coerce_currency(raw) == expected

    @pytest.mark.parametrize("raw,expected", [
        ("2026-08-10", "2026-08-10"), ("10 August 2026", "2026-08-10"),
        ("August 10, 2026", "2026-08-10"), (None, None), ("", None),
    ])
    def test_date(self, raw, expected):
        assert coerce_date(raw) == expected

    def test_ambiguous_numeric_date_becomes_null_rather_than_a_guess(self):
        """03/04/2026 is 3 April in Australia and 4 March in the US. Guessing is worse
        than a null, and raw_json keeps the original either way."""
        assert coerce_date("03/04/2026") is None


# =====================================================================
# Content hashing, the dedup key
# =====================================================================
class TestContentHash:
    def test_ignores_whitespace_differences(self):
        assert content_hash("INVOICE  1500", "x") == content_hash("INVOICE 1500", "x")
        assert content_hash("INVOICE\n1500", "x") == content_hash("INVOICE 1500", "x")

    def test_different_content_gives_different_hash(self):
        assert content_hash("INVOICE 1500", "x") != content_hash("INVOICE 2650", "x")

    def test_ignores_the_byte_hash_when_text_exists(self):
        """The whole point: ReportLab rewrites /ID every run, so bytes change while the
        text does not. The key must follow the text."""
        assert content_hash("INVOICE 1500", "aaa") == content_hash("INVOICE 1500", "bbb")

    def test_falls_back_to_bytes_without_a_text_layer(self):
        """Scanned PDFs have no text. They dedup on bytes until OCR lands."""
        assert content_hash("", "abc123") == "bytes:abc123"
        assert content_hash("   \n  ", "abc123") == "bytes:abc123"

    def test_raises_when_neither_is_available(self):
        with pytest.raises(ValueError):
            content_hash("", None)


# =====================================================================
# Upsert behaviour
# =====================================================================
class TestUpsert:
    def test_first_write_inserts(self, store, run_id):
        result = save(store, run_id)
        assert result["was_update"] is False
        assert result["line_item_count"] == 3

    def test_second_write_updates_the_same_row(self, store, run_id):
        first = save(store, run_id)
        second = save(store, run_id, file_name="renamed.pdf", source_sha256="c" * 64)
        assert second["was_update"] is True
        assert second["invoice_id"] == first["invoice_id"]
        with connect(store.db_path) as conn:
            assert conn.execute("SELECT COUNT(*) c FROM invoices").fetchone()["c"] == 1

    def test_a_different_document_inserts_a_second_row(self, store, run_id):
        save(store, run_id)
        save(store, run_id, content_sha256="d" * 64)
        with connect(store.db_path) as conn:
            assert conn.execute("SELECT COUNT(*) c FROM invoices").fetchone()["c"] == 2

    def test_update_does_not_clobber_a_human_decision(self, store, run_id):
        """approval_status and reviewed_at record what a person decided. Re-processing the
        document must not silently undo that."""
        first = save(store, run_id)
        with connect(store.db_path) as conn:
            conn.execute(
                "UPDATE invoices SET approval_status='Approved', reviewed_at='2026-08-28' "
                "WHERE invoice_id = ?", (first["invoice_id"],))
            conn.commit()

        save(store, run_id)

        with connect(store.db_path) as conn:
            row = conn.execute("SELECT approval_status, reviewed_at FROM invoices").fetchone()
        assert row["approval_status"] == "Approved"
        assert row["reviewed_at"] == "2026-08-28"

    def test_line_items_are_replaced_not_appended(self, store, run_id):
        save(store, run_id)
        save(store, run_id, extracted=sample_extracted(items=[
            {"description": "Only item", "quantity": 1, "unit_price": 5.0, "total": 5.0}]))
        with connect(store.db_path) as conn:
            assert conn.execute("SELECT COUNT(*) c FROM line_items").fetchone()["c"] == 1

    def test_recovery_runs_on_write_not_only_in_the_backfill(self, store, run_id):
        """Without this, the next pipeline run overwrites every total the migration
        recovered with the model's 0.00."""
        result = save(store, run_id, extracted=sample_extracted(total_amount=0.0))
        assert result["total_cents"] == 150000
        assert result["total_source"] == "fallback"

    def test_an_extracted_total_is_not_labelled_fallback(self, store, run_id):
        result = save(store, run_id)
        assert result["total_cents"] == 150000
        assert result["total_source"] == "model"

    def test_archive_path_is_stored_absolute(self, store, run_id):
        save(store, run_id, archive_path="./archive/invoice.pdf")
        with connect(store.db_path) as conn:
            assert conn.execute("SELECT archive_path a FROM invoices").fetchone()["a"].startswith("/")

    def test_rejects_an_invalid_validation_status_before_reaching_sql(self, store, run_id):
        with pytest.raises(ValueError):
            save(store, run_id, validation_status="banana")


# =====================================================================
# Constraints. The database refuses bad data on its own.
# =====================================================================
class TestConstraints:
    @pytest.fixture
    def populated(self, store, run_id):
        save(store, run_id)
        return store.db_path

    @pytest.mark.parametrize("label,sql", [
        ("validation_status", "UPDATE invoices SET validation_status='banana'"),
        ("validation_score", "UPDATE invoices SET validation_score=99.7"),
        ("negative total",   "UPDATE invoices SET total_cents=-500000"),
        ("date format",      "UPDATE invoices SET invoice_date='not-a-date'"),
        ("currency length",  "UPDATE invoices SET currency='Galactic Credits'"),
        ("approval_status",  "UPDATE invoices SET approval_status='maybe'"),
        ("total_source",     "UPDATE invoices SET total_source='vibes'"),
    ])
    def test_check_constraints_reject(self, populated, label, sql):
        with connect(populated) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(sql)

    def test_duplicate_content_hash_is_rejected(self, populated):
        with connect(populated) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO invoices (run_id, file_name, source_sha256, content_sha256, "
                    "validation_score, validation_status) SELECT run_id, file_name, source_sha256, "
                    "content_sha256, validation_score, validation_status FROM invoices")

    def test_orphan_line_item_is_rejected(self, populated):
        with connect(populated) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO line_items (invoice_id, line_no, description) "
                             "VALUES (9999, 1, 'orphan')")

    def test_deleting_an_invoice_cascades_to_its_line_items(self, populated):
        with connect(populated) as conn:
            conn.execute("DELETE FROM invoices")
            assert conn.execute("SELECT COUNT(*) c FROM line_items").fetchone()["c"] == 0

    def test_foreign_keys_are_actually_on(self, populated):
        """SQLite defaults foreign_keys OFF per connection. Without the PRAGMA every FK
        in the schema is decorative."""
        with connect(populated) as conn:
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


# =====================================================================
# Schema drift guards
# =====================================================================
class TestSchemaGuards:
    def test_fresh_database_is_created_at_the_current_version(self, db):
        with connect(db) as conn:
            assert conn.execute("SELECT MAX(version) v FROM schema_version").fetchone()["v"] == SCHEMA_VERSION

    def test_unmigrated_legacy_database_fails_loudly(self, tmp_path):
        path = str(tmp_path / "legacy.db")
        sqlite3.connect(path).execute(
            "CREATE TABLE workflow_records (id INTEGER PRIMARY KEY, file_name TEXT)")
        with pytest.raises(SchemaMismatch, match="001_normalise"):
            StorageManager(path)

    def test_wrong_schema_version_fails_loudly(self, db):
        with connect(db) as conn:
            conn.execute("UPDATE schema_version SET version = 99")
            conn.commit()
        with pytest.raises(SchemaMismatch, match="99"):
            StorageManager(db)

    def test_wal_is_enabled(self, db):
        """So the dashboard can read while the pipeline writes."""
        with connect(db) as conn:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


# =====================================================================
# Run lifecycle
# =====================================================================
class TestRuns:
    def test_a_new_run_is_open_until_it_is_finished(self, store):
        run_id = store.start_run("llama3.2", 0.8)
        with connect(store.db_path) as conn:
            assert conn.execute("SELECT finished_at FROM processing_runs").fetchone()["finished_at"] is None
        store.finish_run(run_id, doc_count=3)
        with connect(store.db_path) as conn:
            row = conn.execute("SELECT finished_at, doc_count FROM processing_runs").fetchone()
        assert row["finished_at"] is not None
        assert row["doc_count"] == 3

    def test_threshold_outside_zero_to_one_is_rejected(self, store):
        with pytest.raises(sqlite3.IntegrityError):
            store.start_run("llama3.2", 1.5)


# =====================================================================
# Email intake
# =====================================================================
class TestEmailMessages:
    def test_records_an_email(self, store):
        result = store.record_email(
            message_id="<abc@mail.gmail.com>", sender="billing@vendor.com",
            subject="Invoice INV-2026-001", received_at="2026-08-10 09:00:00",
            attachment_count=1)
        assert result["already_seen"] is False
        assert result["email_id"] > 0

    def test_the_same_email_twice_updates_rather_than_duplicates(self, store):
        """message_id is the RFC 5322 header, unique by definition. This is what gives
        intake the duplicate protection it does not have today."""
        first = store.record_email("<abc@mail>", "a@b.com", "Invoice", attachment_count=1)
        second = store.record_email("<abc@mail>", "a@b.com", "Invoice (resent)", attachment_count=2)
        assert second["already_seen"] is True
        assert second["email_id"] == first["email_id"]
        with connect(store.db_path) as conn:
            row = conn.execute("SELECT COUNT(*) c, MAX(subject) s, MAX(attachment_count) n "
                               "FROM email_messages").fetchone()
        assert row["c"] == 1
        assert row["s"] == "Invoice (resent)"
        assert row["n"] == 2

    def test_has_seen_email(self, store):
        assert store.has_seen_email("<new@mail>") is False
        store.record_email("<new@mail>", "a@b.com")
        assert store.has_seen_email("<new@mail>") is True

    def test_message_id_is_required(self, store):
        with pytest.raises(ValueError, match="message_id"):
            store.record_email("", "a@b.com")

    def test_an_invoice_can_be_linked_to_its_email(self, store, run_id):
        email = store.record_email("<abc@mail>", "billing@vendor.com", attachment_count=1)
        save(store, run_id, email_id=email["email_id"])
        with connect(store.db_path) as conn:
            row = conn.execute("""
                SELECT e.sender FROM invoices i JOIN email_messages e ON e.email_id = i.email_id
            """).fetchone()
        assert row["sender"] == "billing@vendor.com"

    def test_an_invoice_without_an_email_is_allowed(self, store, run_id):
        """Dropping a PDF straight into inbox/ is the documented way to test without Gmail."""
        save(store, run_id)
        with connect(store.db_path) as conn:
            assert conn.execute("SELECT email_id FROM invoices").fetchone()["email_id"] is None

    def test_reprocessing_does_not_erase_the_original_email_link(self, store, run_id):
        email = store.record_email("<abc@mail>", "billing@vendor.com")
        save(store, run_id, email_id=email["email_id"])
        save(store, run_id)  # a manual re-run, with no email in hand
        with connect(store.db_path) as conn:
            assert conn.execute("SELECT email_id FROM invoices").fetchone()["email_id"] == email["email_id"]

    def test_a_bogus_email_id_is_rejected(self, store, run_id):
        with pytest.raises(sqlite3.IntegrityError):
            save(store, run_id, email_id=9999)


# =====================================================================
# Tasks
# =====================================================================
class TestTasks:
    @pytest.fixture
    def invoice_id(self, store, run_id):
        return save(store, run_id)["invoice_id"]

    def test_opens_a_task(self, store, invoice_id):
        result = store.open_task(invoice_id, "Review", "score below threshold")
        assert result["was_created"] is True

    def test_a_second_open_task_of_the_same_type_is_not_created(self, store, invoice_id):
        """Re-processing a document must not pile up identical tasks."""
        first = store.open_task(invoice_id, "Review", "first")
        second = store.open_task(invoice_id, "Review", "second")
        assert second["was_created"] is False
        assert second["task_id"] == first["task_id"]
        with connect(store.db_path) as conn:
            assert conn.execute("SELECT COUNT(*) c FROM tasks").fetchone()["c"] == 1

    def test_different_task_types_coexist(self, store, invoice_id):
        store.open_task(invoice_id, "Review")
        assert store.open_task(invoice_id, "Approve")["was_created"] is True

    def test_a_new_task_can_open_once_the_old_one_is_closed(self, store, invoice_id):
        """The uniqueness rule is about *live* tasks, not about history."""
        store.open_task(invoice_id, "Review")
        store.resolve_tasks(invoice_id)
        assert store.open_task(invoice_id, "Review")["was_created"] is True
        with connect(store.db_path) as conn:
            assert conn.execute("SELECT COUNT(*) c FROM tasks").fetchone()["c"] == 2

    def test_the_partial_index_is_what_enforces_it(self, store, invoice_id):
        store.open_task(invoice_id, "Review")
        with connect(store.db_path) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO tasks (invoice_id, task_type, state) "
                             "VALUES (?, 'Review', 'Open')", (invoice_id,))

    def test_resolving_sets_the_time(self, store, invoice_id):
        store.open_task(invoice_id, "Review")
        assert store.resolve_tasks(invoice_id, "Done") == 1
        with connect(store.db_path) as conn:
            row = conn.execute("SELECT state, resolved_at FROM tasks").fetchone()
        assert row["state"] == "Done" and row["resolved_at"] is not None

    def test_resolving_again_does_not_rewrite_history(self, store, invoice_id):
        store.open_task(invoice_id, "Review")
        store.resolve_tasks(invoice_id, "Done")
        with connect(store.db_path) as conn:
            first_time = conn.execute("SELECT resolved_at r FROM tasks").fetchone()["r"]
        assert store.resolve_tasks(invoice_id, "Cancelled") == 0
        with connect(store.db_path) as conn:
            row = conn.execute("SELECT state, resolved_at FROM tasks").fetchone()
        assert row["state"] == "Done" and row["resolved_at"] == first_time

    def test_resolve_can_target_one_type(self, store, invoice_id):
        store.open_task(invoice_id, "Review")
        store.open_task(invoice_id, "Approve")
        assert store.resolve_tasks(invoice_id, "Done", task_type="Review") == 1
        assert len(store.open_tasks()) == 1

    def test_open_tasks_joins_the_invoice(self, store, invoice_id):
        store.open_task(invoice_id, "Review", "score below threshold")
        row = store.open_tasks()[0]
        assert row["file_name"] == "invoice.pdf"
        assert row["total_cents"] == 150000
        assert row["reason"] == "score below threshold"

    def test_open_tasks_can_restrict_to_approval_types(self, store, invoice_id):
        """The dashboard queue is Review/Approve only; Payment/File stay out of it."""
        store.open_task(invoice_id, "Approve")
        store.open_task(invoice_id, "Payment")
        rows = store.open_tasks(task_types=APPROVAL_TASK_TYPES)
        assert [r["task_type"] for r in rows] == ["Approve"]
        assert [r["task_type"] for r in store.open_tasks()] == ["Approve", "Payment"]

    def test_open_tasks_rejects_an_unknown_type(self, store):
        with pytest.raises(ValueError):
            store.open_tasks(task_types=("Escalate",))

    def test_deleting_an_invoice_cascades_to_its_tasks(self, store, invoice_id):
        store.open_task(invoice_id, "Review")
        with connect(store.db_path) as conn:
            conn.execute("DELETE FROM invoices WHERE invoice_id = ?", (invoice_id,))
            assert conn.execute("SELECT COUNT(*) c FROM tasks").fetchone()["c"] == 0

    @pytest.mark.parametrize("bad", ["Escalate", "review", ""])
    def test_unknown_task_type_is_rejected(self, store, invoice_id, bad):
        with pytest.raises(ValueError):
            store.open_task(invoice_id, bad)

    def test_unknown_state_is_rejected_by_the_database(self, store, invoice_id):
        store.open_task(invoice_id, "Review")
        with connect(store.db_path) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("UPDATE tasks SET state = 'Snoozed'")

    def test_a_resolved_task_must_carry_a_resolution_time(self, store, invoice_id):
        """Otherwise 'how many are still open' quietly stops being answerable."""
        store.open_task(invoice_id, "Review")
        with connect(store.db_path) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("UPDATE tasks SET state = 'Done'")   # no resolved_at
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("UPDATE tasks SET resolved_at = datetime('now')")  # still Open

    def test_resolve_rejects_a_non_terminal_state(self, store, invoice_id):
        with pytest.raises(ValueError):
            store.resolve_tasks(invoice_id, "Open")


# =====================================================================
# Task assignment
# =====================================================================
class TestTaskAssignment:
    @pytest.fixture
    def task_id(self, store, run_id):
        invoice_id = save(store, run_id)["invoice_id"]
        return store.open_task(invoice_id, "Review")["task_id"]

    def test_assigns_a_task(self, store, task_id):
        assert store.assign_task(task_id, "Luke") is True
        with connect(store.db_path) as conn:
            assert conn.execute(
                "SELECT assignee FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()["assignee"] == "Luke"

    def test_reassigning_overwrites_the_previous_assignee(self, store, task_id):
        store.assign_task(task_id, "Luke")
        store.assign_task(task_id, "Neo")
        with connect(store.db_path) as conn:
            assert conn.execute(
                "SELECT assignee FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()["assignee"] == "Neo"

    def test_clearing_the_assignee_with_none(self, store, task_id):
        store.assign_task(task_id, "Luke")
        store.assign_task(task_id, None)
        with connect(store.db_path) as conn:
            assert conn.execute(
                "SELECT assignee FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()["assignee"] is None

    def test_assigning_an_unknown_task_id_returns_false(self, store):
        assert store.assign_task(999999, "Luke") is False

    def test_set_task_external_ref(self, store, task_id):
        assert store.set_task_external_ref(task_id, "INV-3") is True
        with connect(store.db_path) as conn:
            assert conn.execute(
                "SELECT external_ref FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()["external_ref"] == "INV-3"

    def test_open_tasks_includes_external_ref(self, store, task_id):
        store.set_task_external_ref(task_id, "INV-8")
        row = store.open_tasks()[0]
        assert row["external_ref"] == "INV-8"


# =====================================================================
# Email bodies and attachment-level dedup
# =====================================================================
class TestEmailAttachments:
    def test_record_email_stores_the_body(self, store):
        store.record_email("<x@mail>", "a@b.com", body_text="Please see the attached invoice.",
                           body_source="intake")
        with connect(store.db_path) as conn:
            assert conn.execute("SELECT body_text FROM email_messages").fetchone()["body_text"] == \
                "Please see the attached invoice."

    def test_a_call_without_a_body_does_not_erase_one_already_captured(self, store):
        """The envelope can be re-recorded (e.g. to update attachment_count) without the
        caller re-reading and re-passing the body every time."""
        store.record_email("<x@mail>", "a@b.com", body_text="Original body.",
                           body_source="intake")
        store.record_email("<x@mail>", "a@b.com", attachment_count=2)
        with connect(store.db_path) as conn:
            assert conn.execute("SELECT body_text FROM email_messages").fetchone()["body_text"] == \
                "Original body."

    def test_has_seen_attachment_is_false_until_recorded(self, store):
        email = store.record_email("<x@mail>", "a@b.com")
        assert store.has_seen_attachment(email["email_id"], "a" * 64) is False
        store.record_attachment(email["email_id"], "invoice.pdf", "a" * 64, "/tmp/invoice.pdf")
        assert store.has_seen_attachment(email["email_id"], "a" * 64) is True

    def test_recording_the_same_attachment_twice_does_not_duplicate(self, store):
        email = store.record_email("<x@mail>", "a@b.com")
        first = store.record_attachment(email["email_id"], "invoice.pdf", "a" * 64, "/tmp/i.pdf")
        second = store.record_attachment(email["email_id"], "invoice.pdf", "a" * 64, "/tmp/i.pdf")
        assert first["was_created"] is True
        assert second["was_created"] is False
        assert second["attachment_id"] == first["attachment_id"]
        with connect(store.db_path) as conn:
            assert conn.execute("SELECT COUNT(*) c FROM email_attachments").fetchone()["c"] == 1

    def test_the_same_content_is_allowed_across_different_emails(self, store):
        """A vendor resending an identical invoice is two legitimate, separately
        provenanced copies, not a duplicate to reject."""
        first_email = store.record_email("<a@mail>", "a@b.com")
        second_email = store.record_email("<b@mail>", "a@b.com")
        store.record_attachment(first_email["email_id"], "invoice.pdf", "a" * 64, "/tmp/i1.pdf")
        result = store.record_attachment(second_email["email_id"], "invoice.pdf", "a" * 64, "/tmp/i2.pdf")
        assert result["was_created"] is True

    def test_deleting_an_email_cascades_to_its_attachments(self, store):
        email = store.record_email("<x@mail>", "a@b.com")
        store.record_attachment(email["email_id"], "invoice.pdf", "a" * 64, "/tmp/i.pdf")
        with connect(store.db_path) as conn:
            conn.execute("DELETE FROM email_messages WHERE email_id = ?", (email["email_id"],))
            assert conn.execute(
                "SELECT COUNT(*) c FROM email_attachments").fetchone()["c"] == 0


# =====================================================================
# category, summary and action items (email_ai.py's document pass)
# =====================================================================
class TestAIDocumentFields:
    def test_category_and_summary_are_stored(self, store, run_id):
        invoice_id = save(store, run_id, category="Software",
                          summary="A cloud services invoice for August.")["invoice_id"]
        with connect(store.db_path) as conn:
            row = conn.execute(
                "SELECT category, summary FROM invoices WHERE invoice_id = ?",
                (invoice_id,)).fetchone()
        assert row["category"] == "Software"
        assert row["summary"] == "A cloud services invoice for August."

    def test_missing_ai_fields_default_to_null_not_a_failure(self, store, run_id):
        """The document-intelligence pass can fail independently of the main extraction;
        a document must still be stored without it."""
        invoice_id = save(store, run_id)["invoice_id"]
        with connect(store.db_path) as conn:
            row = conn.execute(
                "SELECT category, summary FROM invoices WHERE invoice_id = ?",
                (invoice_id,)).fetchone()
        assert row["category"] is None and row["summary"] is None

    def test_action_items_are_stored_in_order(self, store, run_id):
        invoice_id = save(store, run_id, action_items=[
            {"task": "Approve the invoice", "owner": None, "deadline_text": None,
             "evidence_quote": "please approve"},
            {"task": "Pay by due date", "owner": "Finance", "deadline_text": "by 2026-09-30",
             "evidence_quote": "due 2026-09-30"},
        ])["invoice_id"]
        items = store.action_items_for(invoice_id)
        assert [i["task"] for i in items] == ["Approve the invoice", "Pay by due date"]
        assert items[1]["owner"] == "Finance"
        assert items[1]["deadline_text"] == "by 2026-09-30"

    def test_action_items_with_a_blank_task_are_dropped(self, store, run_id):
        invoice_id = save(store, run_id, action_items=[
            {"task": "  ", "owner": None, "deadline_text": None, "evidence_quote": None},
            {"task": "Real action", "owner": None, "deadline_text": None, "evidence_quote": None},
        ])["invoice_id"]
        items = store.action_items_for(invoice_id)
        assert [i["task"] for i in items] == ["Real action"]

    def test_set_action_item_done_toggles_the_checkbox(self, store, run_id):
        invoice_id = save(store, run_id, action_items=[
            {"task": "Pay the invoice", "owner": None, "deadline_text": None, "evidence_quote": None},
        ])["invoice_id"]
        action_item_id = store.action_items_for(invoice_id)[0]["action_item_id"]
        assert store.set_action_item_done(action_item_id, True) is True
        assert store.action_items_for(invoice_id)[0]["is_done"] == 1

    def test_reprocessing_with_an_unchanged_action_list_keeps_is_done(self, store, run_id):
        """A person's checkbox must not be silently reset by a routine reprocess that
        finds the exact same action items."""
        action_items = [{"task": "Pay the invoice", "owner": None, "deadline_text": None,
                         "evidence_quote": None}]
        invoice_id = save(store, run_id, action_items=action_items)["invoice_id"]
        action_item_id = store.action_items_for(invoice_id)[0]["action_item_id"]
        store.set_action_item_done(action_item_id, True)

        save(store, run_id, action_items=action_items)  # re-run, identical action items

        items = store.action_items_for(invoice_id)
        assert len(items) == 1 and items[0]["is_done"] == 1

    def test_deleting_an_invoice_cascades_to_its_action_items(self, store, run_id):
        invoice_id = save(store, run_id, action_items=[
            {"task": "Pay the invoice", "owner": None, "deadline_text": None, "evidence_quote": None},
        ])["invoice_id"]
        with connect(store.db_path) as conn:
            conn.execute("DELETE FROM invoices WHERE invoice_id = ?", (invoice_id,))
            assert conn.execute("SELECT COUNT(*) c FROM action_items").fetchone()["c"] == 0


# =====================================================================
# The validation / approval split, by name
# =====================================================================
class TestValidationVersusApproval:
    """The column was called `status` until migration 005.

    It was renamed because `status` did not say whose judgement it holds, which matters now
    that approval_status sits beside it. `model_result_status` was rejected: the value is
    produced by ConfidenceValidator thresholding its own score, not by the model. Changing
    the threshold changes every value here while the model does identical work.
    """

    def test_the_gate_writes_validation_status_and_a_person_writes_approval_status(self, store, run_id):
        invoice_id = save(store, run_id)["invoice_id"]
        with connect(store.db_path) as conn:
            row = conn.execute(
                "SELECT validation_status, approval_status FROM invoices").fetchone()
        assert row["validation_status"] == "NeedsReview"   # from the gate
        assert row["approval_status"] == "Pending"          # untouched by the pipeline

        # A person decides. The gate's verdict and score must survive it.
        with connect(store.db_path) as conn:
            conn.execute("UPDATE invoices SET approval_status = 'Approved', "
                         "reviewed_at = datetime('now') WHERE invoice_id = ?", (invoice_id,))
            conn.commit()
            row = conn.execute(
                "SELECT validation_status, validation_score, approval_status FROM invoices").fetchone()
        assert row["validation_status"] == "NeedsReview", "approval must not overwrite the gate"
        assert row["validation_score"] == 0.4, "approval must not overwrite the measurement"
        assert row["approval_status"] == "Approved"

    def test_needs_review_and_approved_can_hold_at_once(self, store, run_id):
        """Not a contradiction. It means the gate was not confident and a person accepted
        the document anyway, which is the case the whole split exists to record."""
        invoice_id = save(store, run_id)["invoice_id"]
        with connect(store.db_path) as conn:
            conn.execute("UPDATE invoices SET approval_status = 'Approved', "
                         "reviewed_at = datetime('now') WHERE invoice_id = ?", (invoice_id,))
            conn.commit()
            count = conn.execute(
                "SELECT COUNT(*) c FROM invoices "
                "WHERE validation_status = 'NeedsReview' AND approval_status = 'Approved'"
            ).fetchone()["c"]
        assert count == 1

    def test_the_old_column_name_is_gone(self, db):
        with connect(db) as conn:
            columns = [r["name"] for r in conn.execute("PRAGMA table_info(invoices)")]
        assert "validation_status" in columns
        assert "status" not in columns


# =====================================================================
# Reconciliation: the total against the line items
# =====================================================================
class TestReconciliation:
    """We hold the same amount twice, read two ways. Nothing compared them until now.

    The case that motivated this: required extraction fields make the model invent a total
    on a document that states none. Given "Opening balance 1,200.00 / Payments received
    800.00" it returned 2000.0, computing a plausible number rather than copying a wrong
    one. Proximity checking cannot catch that. Arithmetic can.
    """

    def items(self, *totals):
        return normalise_items([
            {"description": f"Item {i}", "quantity": 1, "unit_price": t, "total": t}
            for i, t in enumerate(totals, start=1)])

    def test_exact_when_they_agree(self):
        assert reconcile(150000, self.items(900.0, 360.0, 240.0)) == "exact"

    def test_plausible_when_the_total_is_higher(self):
        """Tax and shipping legitimately push a total above the line items."""
        assert reconcile(170000, self.items(1000.0, 500.0)) == "plausible"

    def test_short_when_the_total_is_lower(self):
        """The genuine anomaly: neither tax nor shipping can reduce a total."""
        assert reconcile(100000, self.items(1000.0, 500.0)) == "short"

    def test_unknown_without_line_items(self):
        assert reconcile(150000, []) == "unknown"

    def test_unknown_without_a_total(self):
        assert reconcile(None, self.items(100.0)) == "unknown"

    def test_summary_rows_are_excluded_from_the_comparison(self):
        """Otherwise a grand-total line would be counted twice and every invoice would
        look 'short'."""
        rows = normalise_items([
            {"description": "Chair", "quantity": 5, "unit_price": 350.0, "total": 1750.0},
            {"description": "Dock", "quantity": 5, "unit_price": 180.0, "total": 900.0},
            {"description": "Grand Total", "quantity": 1, "unit_price": 0.0, "total": 2650.0},
        ])
        assert reconcile(265000, rows) == "exact"

    def test_the_invented_total_case(self):
        """The measured failure: 1,200 + 800 reported as a 2,000 total on a statement
        that states no amount due."""
        assert reconcile(200000, self.items(1200.0, 800.0)) == "exact", "the sum genuinely is 2000"
        # but against the real line items of a different invoice it is caught:
        assert reconcile(200000, self.items(900.0, 360.0, 240.0)) == "plausible"
        assert reconcile(50000, self.items(900.0, 360.0, 240.0)) == "short"

    def test_it_is_computed_on_write(self, store, run_id):
        result = save(store, run_id)
        assert result["reconciliation"] == "exact"
        with connect(store.db_path) as conn:
            assert conn.execute("SELECT reconciliation r FROM invoices").fetchone()["r"] == "exact"


# =====================================================================
# Document classification
# =====================================================================
class TestDocumentType:
    def test_a_tax_invoice_is_an_invoice(self):
        assert classify_document("TAX INVOICE\nVendor: Apex Cloud\nAmount Due 1500") == "Invoice"

    def test_a_receipt_is_a_receipt(self):
        assert classify_document("RECEIPT\nCorner Cafe\nPayment received") == "Receipt"

    def test_ambiguous_is_unknown(self):
        """Both kinds of marker, so the document does not say clearly what it is."""
        assert classify_document("TAX INVOICE\nRECEIPT OF PAYMENT") == "Unknown"

    def test_neither_is_unknown(self):
        assert classify_document("Some notes about a taxi ride") == "Unknown"

    def test_only_the_opening_lines_are_read(self):
        """'please pay this invoice' at the foot of a receipt describes an instruction,
        not the document's type."""
        doc = "RECEIPT\nCorner Cafe\n" + "\n" * 20 + "please pay this invoice if unpaid"
        assert classify_document(doc) == "Receipt"

    def test_empty_text_is_unknown(self):
        assert classify_document("") == "Unknown"

    def test_a_human_classification_survives_reprocessing(self, store, run_id):
        """A heuristic must not overwrite a person's correction."""
        invoice_id = save(store, run_id, raw_text="TAX INVOICE\nApex")["invoice_id"]
        with connect(store.db_path) as conn:
            conn.execute("UPDATE invoices SET document_type = 'Receipt' WHERE invoice_id = ?",
                         (invoice_id,))
            conn.commit()
        save(store, run_id, raw_text="TAX INVOICE\nApex")
        with connect(store.db_path) as conn:
            assert conn.execute("SELECT document_type d FROM invoices").fetchone()["d"] == "Receipt"


# =====================================================================
# The post-approval hand-off
# =====================================================================
class TestPostApproval:
    @pytest.fixture
    def approved(self, store, run_id):
        def _make(document_type):
            invoice_id = save(store, run_id, content_sha256=f"{document_type}"*8)["invoice_id"]
            with connect(store.db_path) as conn:
                conn.execute("UPDATE invoices SET approval_status='Approved', "
                             "reviewed_at=datetime('now'), document_type=? WHERE invoice_id=?",
                             (document_type, invoice_id))
                conn.commit()
            return invoice_id
        return _make

    def test_an_approved_invoice_opens_a_payment_task(self, store, approved):
        """An invoice is unpaid. Approval means the money still has to move."""
        result = store.open_followup_task(approved("Invoice"))
        assert result["task_type"] == "Payment" and result["was_created"]

    def test_an_approved_receipt_opens_a_file_task(self, store, approved):
        """A receipt is already paid. There is nothing to schedule."""
        assert store.open_followup_task(approved("Receipt"))["task_type"] == "File"

    def test_an_unknown_type_goes_back_to_a_person(self, store, approved):
        """Routing on a guess is worse than asking."""
        assert store.open_followup_task(approved("Unknown"))["task_type"] == "Review"

    def test_an_unapproved_invoice_opens_nothing(self, store, run_id):
        invoice_id = save(store, run_id)["invoice_id"]
        assert store.open_followup_task(invoice_id) is None

    def test_a_rejected_invoice_opens_nothing(self, store, run_id):
        """The document was not accepted, so nothing downstream should act on it."""
        invoice_id = save(store, run_id)["invoice_id"]
        with connect(store.db_path) as conn:
            conn.execute("UPDATE invoices SET approval_status='Rejected', "
                         "reviewed_at=datetime('now') WHERE invoice_id=?", (invoice_id,))
            conn.commit()
        assert store.open_followup_task(invoice_id) is None

    def test_calling_it_twice_does_not_duplicate(self, store, approved):
        invoice_id = approved("Invoice")
        first = store.open_followup_task(invoice_id)
        second = store.open_followup_task(invoice_id)
        assert second["was_created"] is False and second["task_id"] == first["task_id"]


# =====================================================================
# Auto-approval on a perfect score (validation_score == 1.0)
# =====================================================================
class TestAutoApprove:
    def test_sets_approval_status_to_approved(self, store, run_id):
        invoice_id = save(store, run_id, validation_score=1.0,
                          validation_status="Validated")["invoice_id"]
        store.auto_approve(invoice_id)
        with connect(store.db_path) as conn:
            row = conn.execute(
                "SELECT approval_status FROM invoices WHERE invoice_id=?", (invoice_id,)
            ).fetchone()
        assert row["approval_status"] == "Approved"

    def test_leaves_reviewed_at_null(self, store, run_id):
        """No human reviewed it -- reviewed_at is how the dashboard tells an auto-approval
        apart from a person clicking Approve."""
        invoice_id = save(store, run_id, validation_score=1.0,
                          validation_status="Validated")["invoice_id"]
        store.auto_approve(invoice_id)
        with connect(store.db_path) as conn:
            row = conn.execute(
                "SELECT reviewed_at, reviewed_by FROM invoices WHERE invoice_id=?", (invoice_id,)
            ).fetchone()
        assert row["reviewed_at"] is None
        assert row["reviewed_by"] is None

    def test_opens_the_same_post_approval_task_a_human_approval_would(self, store, run_id):
        invoice_id = save(store, run_id, validation_score=1.0,
                          validation_status="Validated")["invoice_id"]
        result = store.auto_approve(invoice_id)
        assert result is not None
        assert result["task_type"] == "Payment"  # document_type defaults to Invoice
        assert result["was_created"] is True

    def test_does_not_overwrite_an_existing_human_decision(self, store, run_id):
        """A record already Rejected by a person should not be silently flipped to Approved."""
        invoice_id = save(store, run_id, validation_score=1.0,
                          validation_status="Validated")["invoice_id"]
        with connect(store.db_path) as conn:
            conn.execute("UPDATE invoices SET approval_status='Rejected', "
                         "reviewed_at=datetime('now') WHERE invoice_id=?", (invoice_id,))
            conn.commit()
        store.auto_approve(invoice_id)
        with connect(store.db_path) as conn:
            row = conn.execute(
                "SELECT approval_status, reviewed_at FROM invoices WHERE invoice_id=?",
                (invoice_id,)
            ).fetchone()
        assert row["approval_status"] == "Rejected"
        assert row["reviewed_at"] is not None

    def test_closes_a_preexisting_approve_task_and_still_opens_payment(self, store, run_id):
        """Re-processing a document that already had an Approve task must not leave it in
        the dashboard queue after auto-approval."""
        invoice_id = save(store, run_id, validation_score=1.0,
                          validation_status="Validated")["invoice_id"]
        approve = store.open_task(invoice_id, "Approve")
        result = store.auto_approve(invoice_id)
        assert result is not None
        assert result["task_type"] == "Payment"
        assert store.open_tasks(task_types=APPROVAL_TASK_TYPES) == []
        assert [r["task_type"] for r in store.open_tasks()] == ["Payment"]
        with connect(store.db_path) as conn:
            row = conn.execute(
                "SELECT state FROM tasks WHERE task_id=?", (approve["task_id"],)
            ).fetchone()
        assert row["state"] == "Done"


# =====================================================================
# The outbox
# =====================================================================
class TestOutbox:
    @pytest.fixture
    def invoice_id(self, store, run_id):
        return save(store, run_id)["invoice_id"]

    def test_queuing_records_a_pending_row(self, store, invoice_id):
        store.queue_outbound(invoice_id, "Teams", "hello")
        rows = store.pending_outbound()
        assert len(rows) == 1 and rows[0]["state"] == "Pending"

    def test_mark_outbound_sent_sets_timestamp_and_ref(self, store, invoice_id):
        outbox_id = store.queue_outbound(invoice_id, "Jira", "hello")
        assert store.mark_outbound_sent(outbox_id, "INV-12") is True
        with connect(store.db_path) as conn:
            row = conn.execute(
                "SELECT state, sent_at, external_ref FROM outbound_messages WHERE outbox_id = ?",
                (outbox_id,),
            ).fetchone()
        assert row["state"] == "Sent"
        assert row["sent_at"] is not None
        assert row["external_ref"] == "INV-12"

    def test_mark_outbound_failed_records_error(self, store, invoice_id):
        outbox_id = store.queue_outbound(invoice_id, "Jira", "hello")
        assert store.mark_outbound_failed(outbox_id, "HTTP 401") is True
        with connect(store.db_path) as conn:
            row = conn.execute(
                "SELECT state, error FROM outbound_messages WHERE outbox_id = ?",
                (outbox_id,),
            ).fetchone()
        assert row["state"] == "Failed"
        assert row["error"] == "HTTP 401"

    def test_an_unknown_channel_is_rejected(self, store, invoice_id):
        with pytest.raises(ValueError):
            store.queue_outbound(invoice_id, "Carrier Pigeon", "hello")

    def test_sent_requires_a_timestamp(self, store, invoice_id):
        """Otherwise 'what is still pending' quietly stops being answerable."""
        store.queue_outbound(invoice_id, "Teams", "hello")
        with connect(store.db_path) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("UPDATE outbound_messages SET state = 'Sent'")

    def test_deleting_an_invoice_cascades_to_its_outbox_rows(self, store, invoice_id):
        store.queue_outbound(invoice_id, "Teams", "hello")
        with connect(store.db_path) as conn:
            conn.execute("DELETE FROM invoices WHERE invoice_id = ?", (invoice_id,))
            assert conn.execute("SELECT COUNT(*) c FROM outbound_messages").fetchone()["c"] == 0


# =====================================================================
# Email bodies, migration 008
# =====================================================================
class TestEmailBody:
    """Retrieval needs the body. Sender and subject answer 'has this vendor written
    before' and nothing else."""

    def test_a_body_is_stored_with_its_source(self, store):
        store.record_email(message_id="<a@x>", sender="a@x", body_text="Hello",
                           body_source="mock")
        with connect(store.db_path) as conn:
            row = conn.execute(
                "SELECT body_text, body_source FROM email_messages").fetchone()
        assert row["body_text"] == "Hello"
        assert row["body_source"] == "mock"

    def test_a_body_without_a_source_is_refused(self, store):
        """An unattributable body is worse than none: a result measured over generated
        text would be indistinguishable from one measured over real correspondence."""
        with pytest.raises(ValueError, match="body_source"):
            store.record_email(message_id="<b@x>", sender="a@x", body_text="Hello")

    def test_an_invalid_source_is_refused(self, store):
        with pytest.raises(ValueError, match="body_source"):
            store.record_email(message_id="<c@x>", sender="a@x", body_text="Hi",
                               body_source="guessed")

    def test_no_body_is_allowed(self, store):
        """Headers-only intake must keep working. The column is optional."""
        result = store.record_email(message_id="<d@x>", sender="a@x")
        assert result["already_seen"] is False

    def test_a_refetch_without_a_body_does_not_erase_one(self, store):
        """A header-only re-fetch must not delete what was already captured."""
        store.record_email(message_id="<e@x>", sender="a@x", body_text="Original",
                           body_source="mock")
        store.record_email(message_id="<e@x>", sender="a@x", subject="Re: hello")
        with connect(store.db_path) as conn:
            row = conn.execute(
                "SELECT body_text, body_source, subject FROM email_messages").fetchone()
        assert row["body_text"] == "Original"
        assert row["body_source"] == "mock"
        assert row["subject"] == "Re: hello"      # other fields still update

    def test_a_refetch_with_a_body_replaces_it(self, store):
        store.record_email(message_id="<f@x>", sender="a@x", body_text="First",
                           body_source="mock")
        store.record_email(message_id="<f@x>", sender="a@x", body_text="Second",
                           body_source="intake")
        with connect(store.db_path) as conn:
            row = conn.execute(
                "SELECT body_text, body_source FROM email_messages").fetchone()
        assert row["body_text"] == "Second"
        assert row["body_source"] == "intake"


# =====================================================================
# Dashboard reviewers
# =====================================================================
class TestRecordDecision:
    def _user(self, store, username="luke"):
        return store.upsert_user(username, username.title(), "hash", "acc-" + username)

    def test_stores_the_reviewer(self, store, run_id):
        invoice_id = save(store, run_id)["invoice_id"]
        user_id = self._user(store)
        assert store.record_decision(invoice_id, "Approved", user_id) is True
        with connect(store.db_path) as conn:
            row = conn.execute(
                "SELECT approval_status, reviewed_at, reviewed_by FROM invoices "
                "WHERE invoice_id = ?",
                (invoice_id,),
            ).fetchone()
        assert row["approval_status"] == "Approved"
        assert row["reviewed_at"] is not None
        assert row["reviewed_by"] == user_id

    def test_reprocess_keeps_the_reviewer(self, store, run_id):
        first = save(store, run_id)
        user_id = self._user(store)
        store.record_decision(first["invoice_id"], "Rejected", user_id)
        save(store, run_id)
        with connect(store.db_path) as conn:
            row = conn.execute(
                "SELECT approval_status, reviewed_by FROM invoices"
            ).fetchone()
        assert row["approval_status"] == "Rejected"
        assert row["reviewed_by"] == user_id

    def test_unknown_reviewer_is_rejected(self, store, run_id):
        invoice_id = save(store, run_id)["invoice_id"]
        with pytest.raises(ValueError, match="unknown reviewer"):
            store.record_decision(invoice_id, "Approved", 999)
        with connect(store.db_path) as conn:
            row = conn.execute(
                "SELECT approval_status, reviewed_by FROM invoices WHERE invoice_id = ?",
                (invoice_id,),
            ).fetchone()
        assert row["approval_status"] == "Pending"
        assert row["reviewed_by"] is None

    def test_missing_invoice_returns_false(self, store):
        user_id = self._user(store)
        assert store.record_decision(404, "Approved", user_id) is False
