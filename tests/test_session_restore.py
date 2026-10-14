"""A refresh keeps the person signed in, and Log out ends it.

Streamlit gives a refreshed page an empty session, so the login used to vanish on every
refresh. The dashboard now keeps a signed token in the URL (`?s=`). AppTest has no browser to
refresh, so a refresh is a new AppTest carrying the same query parameter.
"""
import os
import sys

import pytest
from streamlit.testing.v1 import AppTest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from signed_in import REPO_ROOT, active_db_path, sign_in  # noqa: E402

import auth  # noqa: E402
from storage import DEFAULT_DB_PATH, StorageManager  # noqa: E402

APP = os.path.join(REPO_ROOT, "app.py")


def _token_for(username="neo"):
    store = StorageManager(active_db_path())
    auth.ensure_default_users(store)
    return auth.issue_session_token(store, store.user_by_username(username)["user_id"])


def _shows_login_form(at):
    return any(button.label == "Log in" for button in at.button) or \
        any(getattr(el, "label", "") == "Log in" for el in at.get("form_submit_button"))


def test_a_valid_token_in_the_url_skips_the_login_form():
    at = AppTest.from_file(APP, default_timeout=120)
    at.query_params[auth.SESSION_PARAM] = _token_for("neo")
    at.run()
    assert not at.exception
    assert not _shows_login_form(at)
    assert at.session_state["display_name"] == "Nattakrit Pitayasiri"


def test_no_token_shows_the_login_form():
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert _shows_login_form(at)


def test_a_bad_token_shows_the_login_form_and_is_dropped_from_the_url():
    at = AppTest.from_file(APP, default_timeout=120)
    at.query_params[auth.SESSION_PARAM] = "1.9999999999.deadbeef"
    at.run()
    assert _shows_login_form(at)
    assert auth.SESSION_PARAM not in at.query_params


def test_a_forced_password_change_survives_a_refresh():
    store = StorageManager(active_db_path())
    auth.ensure_default_users(store)
    # jj's flag is 1 on a fresh database; a person who has since changed it makes this moot.
    if not store.user_by_username("jj")["must_change_password"]:
        pytest.skip("jj has already chosen a password in this database")
    at = AppTest.from_file(APP, default_timeout=120)
    at.query_params[auth.SESSION_PARAM] = _token_for("jj")
    at.run()
    assert any("Choose a new password" in (t.value or "") for t in at.text_input) or \
        any("first login" in c.value for c in at.caption)


def test_log_out_clears_the_token():
    """Uses the first-login screen, which has a Log out button whether or not any document
    exists. The sidebar's Log out calls the same sign_out_session()."""
    store = StorageManager(active_db_path())
    auth.ensure_default_users(store)
    if not store.user_by_username("luke")["must_change_password"]:
        pytest.skip("luke has already chosen a password in this database")
    at = AppTest.from_file(APP, default_timeout=120)
    at.query_params[auth.SESSION_PARAM] = _token_for("luke")
    at.run()
    logout = [b for b in at.button if b.label == "Log out"]
    assert logout, "the first-login screen should offer Log out"
    logout[0].click().run()
    assert auth.SESSION_PARAM not in at.query_params
    assert _shows_login_form(at)


def _needs_documents(at):
    if not at.radio:
        pytest.skip("no documents in this database, so no sidebar to read")


def test_a_refresh_stays_on_the_page_it_was_on():
    at = sign_in(AppTest.from_file(APP, default_timeout=120))
    at.query_params["v"] = "history"
    at.run()
    _needs_documents(at)
    assert at.radio(key="view").value == "history"


def test_an_unknown_page_in_the_url_falls_back_to_overview():
    at = sign_in(AppTest.from_file(APP, default_timeout=120))
    at.query_params["v"] = "not-a-page"
    at.run()
    _needs_documents(at)
    assert at.radio(key="view").value == "overview"


def test_the_current_page_is_written_to_the_url():
    at = sign_in(AppTest.from_file(APP, default_timeout=120)).run()
    _needs_documents(at)
    at.radio(key="view").set_value("outbox").run()
    assert at.query_params["v"] == ["outbox"]  # AppTest returns a list
