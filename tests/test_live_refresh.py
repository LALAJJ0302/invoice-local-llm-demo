"""The dashboard notices rows another process wrote, without a manual refresh.

Streamlit only reruns the script on a click or a page load, and the pipeline and the email
listener are separate processes. `watch_for_new_rows` in app.py polls `change_token()` on a
timer and redraws only when it differs. AppTest cannot advance a timer, so the pieces are
tested directly: the token, and the decision of whether to redraw.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage import StorageManager, connect  # noqa: E402


@pytest.fixture
def store(tmp_path):
    return StorageManager(str(tmp_path / "live.db"))


def _insert_email(store, message_id="<a@x>"):
    with connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO email_messages (message_id, sender, subject, received_at) "
            "VALUES (?, 'v@example.com', 's', datetime('now'))", (message_id,))
        conn.commit()


def _insert_outbox(store):
    run_id = store.start_run("test-model", 0.9)
    with connect(store.db_path) as conn:
        invoice_id = conn.execute(
            "INSERT INTO invoices (run_id, file_name, source_sha256, content_sha256, "
            "validation_status, validation_score) "
            "VALUES (?, 'f.pdf', 's', 'c', 'NeedsReview', 0.5) RETURNING invoice_id",
            (run_id,),
        ).fetchone()["invoice_id"]
        outbox_id = conn.execute(
            "INSERT INTO outbound_messages (invoice_id, channel, payload) "
            "VALUES (?, 'Jira', '{}') RETURNING outbox_id",
            (invoice_id,),
        ).fetchone()["outbox_id"]
        conn.commit()
    return int(outbox_id)


class TestChangeToken:
    def test_it_is_stable_when_nothing_is_written(self, store):
        assert store.change_token() == store.change_token()

    def test_a_new_email_row_changes_it(self, store):
        before = store.change_token()
        _insert_email(store)
        assert store.change_token() != before

    def test_a_new_invoice_changes_it(self, store):
        run_id = store.start_run("test-model", 0.9)
        before = store.change_token()
        with connect(store.db_path) as conn:
            conn.execute(
                "INSERT INTO invoices (run_id, file_name, source_sha256, content_sha256, "
                "validation_status, validation_score) "
                "VALUES (?, 'f.pdf', 's', 'c', 'NeedsReview', 0.5)", (run_id,))
            conn.commit()
        assert store.change_token() != before

    def test_an_outbox_failure_changes_it(self, store):
        outbox_id = _insert_outbox(store)
        before = store.change_token()

        store.mark_outbound_failed(outbox_id, "network unavailable")

        assert store.change_token() != before

    def test_an_in_place_task_update_changes_it(self, store):
        outbox_id = _insert_outbox(store)
        with connect(store.db_path) as conn:
            invoice_id = conn.execute(
                "SELECT invoice_id FROM outbound_messages WHERE outbox_id = ?", (outbox_id,)
            ).fetchone()["invoice_id"]
        task_id = store.open_task(int(invoice_id), "Review")["task_id"]
        before = store.change_token()

        store.assign_task(task_id, "jj")

        assert store.change_token() != before


class TestShouldRedraw:
    @pytest.fixture(autouse=True)
    def _app(self):
        import app
        self.should_redraw = app.should_redraw

    def test_no_change_means_no_redraw(self):
        assert self.should_redraw("a", "a", dialog_open=False) is False

    def test_a_change_redraws(self):
        assert self.should_redraw("a", "b", dialog_open=False) is True

    def test_a_change_is_held_back_while_the_review_dialog_is_open(self):
        assert self.should_redraw("a", "b", dialog_open=True) is False

    def test_the_held_back_change_is_still_there_once_the_dialog_closes(self):
        # `seen` is not updated while held back, so the same difference shows up on the next tick.
        assert self.should_redraw("a", "b", dialog_open=True) is False
        assert self.should_redraw("a", "b", dialog_open=False) is True
