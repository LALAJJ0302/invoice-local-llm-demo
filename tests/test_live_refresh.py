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
