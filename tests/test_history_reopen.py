"""History can open and reopen a decided document. Reported 2026-09-29 in the manual walkthrough.

Runs against a copy of the working database, in a temporary directory, because this test
decides things: it approves a document and then takes the approval back. `DEFAULT_DB_PATH` is
relative, so changing directory is enough to point both the app and the store at the copy.
"""
import os
import pathlib
import shutil

import pytest
from streamlit.testing.v1 import AppTest

from signed_in import REPO_ROOT, sign_in
from storage import DEFAULT_DB_PATH, StorageManager, connect

APP = str(pathlib.Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    at = sign_in(AppTest.from_file(APP, default_timeout=120))
    shutil.copy2(os.path.join(REPO_ROOT, DEFAULT_DB_PATH), tmp_path / DEFAULT_DB_PATH)
    monkeypatch.chdir(tmp_path)
    store = StorageManager(DEFAULT_DB_PATH)
    with connect(DEFAULT_DB_PATH) as conn:
        row = conn.execute("SELECT invoice_id FROM invoices ORDER BY invoice_id").fetchone()
    if row is None:
        pytest.skip("no documents in the working database; run main.py first")
    return at, store, int(row["invoice_id"])


def test_a_decided_document_can_be_reopened_from_history(sandbox):
    at, store, invoice_id = sandbox
    user_id = at.session_state["user_id"]
    store.record_decision(invoice_id, "Approved", user_id)

    at.run()
    at.sidebar.radio[0].set_value("history").run()
    assert not at.exception, [str(e.value) for e in at.exception]
    reopen = [b for b in at.button if b.key.endswith("-reopen")]
    assert len(reopen) == 1, "exactly one Reopen, on the one decision in force"
    assert any(b.key.endswith("-open") for b in at.button), "History rows cannot be opened"

    reopen[0].click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    with connect(DEFAULT_DB_PATH) as conn:
        status = conn.execute("SELECT approval_status FROM invoices WHERE invoice_id = ?",
                              (invoice_id,)).fetchone()[0]
        logged = [r[0] for r in conn.execute(
            "SELECT decision FROM invoice_decisions WHERE invoice_id = ? ORDER BY decision_id",
            (invoice_id,))]
    assert status == "Pending"
    assert logged == ["Approved", "Reopened"], "the approval being taken back was lost"

    # Both decisions stay on the screen, and neither now offers a second Reopen.
    rendered = " ".join(m.value for m in at.markdown)
    assert "Reopened for review" in rendered and "Approved" in rendered
    assert not [b for b in at.button if b.key.endswith("-reopen")]


def test_history_labels_what_each_value_is(sandbox):
    """Reported 2026-09-29: the row was eight unlabelled values and read as vague."""
    at, store, invoice_id = sandbox
    store.record_decision(invoice_id, "Rejected", at.session_state["user_id"])
    at.run()
    at.sidebar.radio[0].set_value("history").run()
    rendered = " ".join(m.value for m in at.markdown)
    for label in ("<b>By</b>", "<b>When</b>", "<b>Validation score then</b>",
                  "<b>What followed</b>"):
        assert label in rendered


def test_times_are_shown_in_sydney_time():
    """Reported 2026-09-29: a note saved mid-afternoon read as early morning, because SQLite's
    datetime('now') is UTC and the screen printed it unchanged."""
    import app
    if app.LOCAL_TZ_LABEL != "Sydney":
        pytest.skip("no time zone database on this machine")
    assert app.local_time("2026-09-29 05:00:00") == "2026-09-29 15:00"   # AEST, UTC+10
    assert app.local_time("2026-10-05 05:00:00") == "2026-10-05 16:00"   # AEDT from 4 October
    assert app.local_time(None) == "-"


def test_the_push_button_has_a_function_behind_it():
    """Commit 75a7d8d deleted push_to_jira and kept its three callers. Every Push button is
    disabled until Jira is configured, so nothing noticed, and the first press would have been
    a NameError."""
    import app
    assert callable(getattr(app, "push_to_jira", None))
