"""Tests for downstream dispatch behaviour in main.py."""

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import DownstreamDispatcher  # noqa: E402
from storage import StorageManager, connect  # noqa: E402


@pytest.fixture
def store(tmp_path):
    path = str(tmp_path / "test.db")
    StorageManager(path)
    return StorageManager(path)


@pytest.fixture
def invoice_id(store):
    run_id = store.start_run(model_name="test", threshold=0.8)
    result = store.save_invoice(
        run_id=run_id,
        file_name="sample_invoice.pdf",
        source_sha256="a" * 64,
        content_sha256="b" * 64,
        extracted={
            "invoice_number": "INV-1",
            "vendor_name": "Apex Cloud",
            "date": "2026-01-01",
            "total_amount": 100.0,
            "currency": "AUD",
            "items": [],
        },
        validation_score=0.75,
        validation_status="NeedsReview",
        archive_path="/tmp/sample_invoice.pdf",
        raw_json="{}",
        raw_text="TAX INVOICE",
    )
    return result["invoice_id"]


class TestDownstreamDispatcher:
    def test_review_queue_does_not_dispatch_jira(self, store, invoice_id):
        dispatcher = DownstreamDispatcher(store)
        with patch("main.task_dispatch.dispatch_task_to_jira") as mock_dispatch:
            dispatcher.dispatch(
                invoice_id, "sample_invoice.pdf", "NeedsReview", 0.75, reason="low score"
            )
        mock_dispatch.assert_not_called()

    def test_approve_queue_does_not_dispatch_jira(self, store, invoice_id):
        dispatcher = DownstreamDispatcher(store)
        with patch("main.task_dispatch.dispatch_task_to_jira") as mock_dispatch:
            dispatcher.dispatch(
                invoice_id, "sample_invoice.pdf", "Validated", 0.85, reason="awaiting approval"
            )
        mock_dispatch.assert_not_called()

    def test_dispatch_skips_when_approval_already_recorded(self, store, invoice_id):
        dispatcher = DownstreamDispatcher(store)
        with connect(store.db_path) as conn:
            conn.execute(
                "UPDATE invoices SET approval_status = 'Approved', "
                "reviewed_at = datetime('now') WHERE invoice_id = ?",
                (invoice_id,),
            )
            conn.commit()
        dispatcher.dispatch(
            invoice_id, "sample_invoice.pdf", "NeedsReview", 0.75, reason="low score"
        )
        assert store.open_tasks(task_types=("Review",)) == []

    def test_dispatch_cancels_stale_review_task_on_rerun(self, store, invoice_id):
        dispatcher = DownstreamDispatcher(store)
        opened = store.open_task(invoice_id, "Review", reason="first run")
        with connect(store.db_path) as conn:
            conn.execute(
                "UPDATE invoices SET approval_status = 'Approved', "
                "reviewed_at = datetime('now') WHERE invoice_id = ?",
                (invoice_id,),
            )
            conn.commit()
        dispatcher.dispatch(
            invoice_id, "sample_invoice.pdf", "NeedsReview", 0.75, reason="re-run"
        )
        with connect(store.db_path) as conn:
            row = conn.execute(
                "SELECT state FROM tasks WHERE task_id = ?", (opened["task_id"],)
            ).fetchone()
        assert row["state"] == "Cancelled"
