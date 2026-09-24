"""Tests for task_dispatch.py."""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jira_client import JiraError  # noqa: E402
from storage import StorageManager, connect  # noqa: E402
from task_dispatch import dispatch_task_to_jira, sync_jira_assignee  # noqa: E402


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


def _open_review_task(store, invoice_id):
    return store.open_task(invoice_id, "Review", "score below threshold")


class TestDispatchTaskToJira:
    def test_jira_disabled_leaves_outbox_pending(self, store, invoice_id, capsys):
        task = _open_review_task(store, invoice_id)
        jira = MagicMock()
        jira.is_configured.return_value = False

        dispatch_task_to_jira(
            store, task["task_id"], invoice_id, "Review", "score below threshold", jira=jira
        )

        jira.create_issue.assert_not_called()
        assert "Skipped task" in capsys.readouterr().out
        with connect(store.db_path) as conn:
            row = conn.execute(
                "SELECT state, external_ref FROM outbound_messages WHERE task_id = ?",
                (task["task_id"],),
            ).fetchone()
            task_row = conn.execute(
                "SELECT external_ref FROM tasks WHERE task_id = ?", (task["task_id"],)
            ).fetchone()
        assert row["state"] == "Pending"
        assert row["external_ref"] is None
        assert task_row["external_ref"] is None

    def test_retries_existing_open_task_without_external_ref(self, store, invoice_id):
        first = store.open_task(invoice_id, "Payment", "approved invoice")
        second = store.open_task(invoice_id, "Payment", "approved invoice")
        assert second["was_created"] is False

        jira = MagicMock()
        jira.is_configured.return_value = True
        jira.resolve_assignee.return_value = "acc-default"
        jira.create_issue.return_value = "KAN-99"

        dispatch_task_to_jira(
            store, second["task_id"], invoice_id, "Payment", "approved invoice", jira=jira
        )

        jira.create_issue.assert_called_once()
        with connect(store.db_path) as conn:
            task_row = conn.execute(
                "SELECT external_ref FROM tasks WHERE task_id = ?", (first["task_id"],)
            ).fetchone()
            outbox_count = conn.execute(
                "SELECT COUNT(*) c FROM outbound_messages WHERE task_id = ? AND channel = 'Jira'",
                (first["task_id"],),
            ).fetchone()["c"]
        assert task_row["external_ref"] == "KAN-99"
        assert outbox_count == 1

    def test_reuses_pending_outbox_on_retry(self, store, invoice_id):
        task = _open_review_task(store, invoice_id)
        disabled = MagicMock()
        disabled.is_configured.return_value = False
        dispatch_task_to_jira(
            store, task["task_id"], invoice_id, "Payment", "approved invoice", jira=disabled
        )

        enabled = MagicMock()
        enabled.is_configured.return_value = True
        enabled.resolve_assignee.return_value = "acc-default"
        enabled.create_issue.return_value = "KAN-42"
        dispatch_task_to_jira(
            store, task["task_id"], invoice_id, "Payment", "approved invoice", jira=enabled
        )

        with connect(store.db_path) as conn:
            rows = conn.execute(
                "SELECT outbox_id, state, external_ref FROM outbound_messages "
                "WHERE task_id = ? AND channel = 'Jira'",
                (task["task_id"],),
            ).fetchall()
        assert len(rows) == 1
        assert rows[0]["state"] == "Sent"
        assert rows[0]["external_ref"] == "KAN-42"

    def test_success_marks_sent_and_sets_external_ref(self, store, invoice_id):
        task = _open_review_task(store, invoice_id)
        jira = MagicMock()
        jira.is_configured.return_value = True
        jira.resolve_assignee.return_value = "acc-default"
        jira.create_issue.return_value = "INV-99"

        dispatch_task_to_jira(
            store, task["task_id"], invoice_id, "Review", "score below threshold", jira=jira
        )

        with connect(store.db_path) as conn:
            outbox = conn.execute(
                "SELECT state, external_ref, sent_at FROM outbound_messages WHERE task_id = ?",
                (task["task_id"],),
            ).fetchone()
            task_row = conn.execute(
                "SELECT external_ref FROM tasks WHERE task_id = ?", (task["task_id"],)
            ).fetchone()
        assert outbox["state"] == "Sent"
        assert outbox["external_ref"] == "INV-99"
        assert outbox["sent_at"] is not None
        assert task_row["external_ref"] == "INV-99"

    def test_failure_marks_outbox_failed(self, store, invoice_id):
        task = _open_review_task(store, invoice_id)
        jira = MagicMock()
        jira.is_configured.return_value = True
        jira.resolve_assignee.return_value = None
        jira.create_issue.side_effect = JiraError("HTTP 500")

        dispatch_task_to_jira(
            store, task["task_id"], invoice_id, "Review", "score below threshold", jira=jira
        )

        with connect(store.db_path) as conn:
            outbox = conn.execute(
                "SELECT state, error FROM outbound_messages WHERE task_id = ?",
                (task["task_id"],),
            ).fetchone()
        assert outbox["state"] == "Failed"
        assert "500" in outbox["error"]

    def test_skips_when_task_already_has_external_ref(self, store, invoice_id):
        task = _open_review_task(store, invoice_id)
        store.set_task_external_ref(task["task_id"], "INV-1")
        jira = MagicMock()
        jira.is_configured.return_value = True

        dispatch_task_to_jira(
            store, task["task_id"], invoice_id, "Review", "score below threshold", jira=jira
        )

        jira.create_issue.assert_not_called()
        with connect(store.db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) c FROM outbound_messages WHERE task_id = ?",
                (task["task_id"],),
            ).fetchone()["c"]
        assert count == 0

    def test_auto_path_transitions_and_labels(self, store, invoice_id):
        task = store.open_task(invoice_id, "Payment", "approved invoice")
        jira = MagicMock()
        jira.is_configured.return_value = True
        jira.resolve_assignee.return_value = "acc-default"
        jira.create_issue.return_value = "KAN-20"
        jira.transition_for_approval_path.return_value = "To Do (Auto Approved)"

        dispatch_task_to_jira(
            store,
            task["task_id"],
            invoice_id,
            "Payment",
            "approved invoice",
            approval_path="auto",
            jira=jira,
        )

        jira.create_issue.assert_called_once()
        labels = jira.create_issue.call_args.kwargs["labels"]
        assert "auto-approved" in labels
        jira.transition_for_approval_path.assert_called_once_with("KAN-20", "auto")

    def test_human_path_transitions(self, store, invoice_id):
        task = store.open_task(invoice_id, "Payment", "approved invoice")
        jira = MagicMock()
        jira.is_configured.return_value = True
        jira.resolve_assignee.return_value = "acc-default"
        jira.create_issue.return_value = "KAN-21"
        jira.transition_for_approval_path.return_value = "To Do (Human Reviewed)"

        dispatch_task_to_jira(
            store,
            task["task_id"],
            invoice_id,
            "Payment",
            "approved invoice",
            approval_path="human",
            jira=jira,
        )

        labels = jira.create_issue.call_args.kwargs["labels"]
        assert "human-reviewed" in labels
        jira.transition_for_approval_path.assert_called_once_with("KAN-21", "human")

    def test_transition_failure_still_marks_sent(self, store, invoice_id, capsys):
        task = store.open_task(invoice_id, "Payment", "approved invoice")
        jira = MagicMock()
        jira.is_configured.return_value = True
        jira.resolve_assignee.return_value = "acc-default"
        jira.create_issue.return_value = "KAN-22"
        jira.transition_for_approval_path.side_effect = JiraError("No transition")

        dispatch_task_to_jira(
            store,
            task["task_id"],
            invoice_id,
            "Payment",
            "approved invoice",
            approval_path="auto",
            jira=jira,
        )

        assert "could not move to approval column" in capsys.readouterr().out
        with connect(store.db_path) as conn:
            outbox = conn.execute(
                "SELECT state, external_ref FROM outbound_messages WHERE task_id = ?",
                (task["task_id"],),
            ).fetchone()
        assert outbox["state"] == "Sent"
        assert outbox["external_ref"] == "KAN-22"

    def test_human_path_sets_the_reviewer_as_reporter(self, store, invoice_id):
        user_id = store.upsert_user("luke", "Luke", "hash", "acc-luke")
        task = store.open_task(invoice_id, "Payment", "approved invoice")
        jira = MagicMock()
        jira.is_configured.return_value = True
        jira.resolve_assignee.return_value = "acc-default"
        jira.create_issue.return_value = "KAN-30"

        dispatch_task_to_jira(
            store,
            task["task_id"],
            invoice_id,
            "Payment",
            "approved invoice",
            approval_path="human",
            reviewer_user_id=user_id,
            jira=jira,
        )

        assert jira.create_issue.call_args.kwargs["reporter_account_id"] == "acc-luke"
        assert "Reviewed by:" not in jira.create_issue.call_args.kwargs["description"]

    def test_missing_account_id_still_creates_and_names_the_reviewer(self, store, invoice_id):
        user_id = store.upsert_user("neo", "Neo", "hash", None)
        task = store.open_task(invoice_id, "Payment", "approved invoice")
        jira = MagicMock()
        jira.is_configured.return_value = True
        jira.resolve_assignee.return_value = "acc-default"
        jira.create_issue.return_value = "KAN-31"

        dispatch_task_to_jira(
            store,
            task["task_id"],
            invoice_id,
            "Payment",
            "approved invoice",
            reviewer_user_id=user_id,
            jira=jira,
        )

        jira.create_issue.assert_called_once()
        assert jira.create_issue.call_args.kwargs["reporter_account_id"] is None
        assert "Reviewed by: Neo" in jira.create_issue.call_args.kwargs["description"]


class TestSyncJiraAssignee:
    def test_syncs_when_external_ref_exists(self, store, invoice_id):
        task = _open_review_task(store, invoice_id)
        store.set_task_external_ref(task["task_id"], "INV-55")
        jira = MagicMock()
        jira.is_configured.return_value = True
        jira.resolve_assignee.return_value = "acc-luke"

        sync_jira_assignee(store, task["task_id"], "Luke", jira=jira)

        jira.assign_issue.assert_called_once_with("INV-55", "acc-luke")

    def test_no_op_without_external_ref(self, store, invoice_id):
        task = _open_review_task(store, invoice_id)
        jira = MagicMock()
        jira.is_configured.return_value = True

        sync_jira_assignee(store, task["task_id"], "Luke", jira=jira)

        jira.assign_issue.assert_not_called()
