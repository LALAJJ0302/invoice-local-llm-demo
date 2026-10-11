"""Tests for auth.py."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth  # noqa: E402
from storage import StorageManager, connect  # noqa: E402


@pytest.fixture
def store(tmp_path):
    return StorageManager(str(tmp_path / "test.db"))


class TestAuth:
    def test_wrong_password_returns_none(self, store):
        auth.create_user(store, "luke", "Luke", "secret", "acc-luke")
        assert auth.authenticate(store, "luke", "nope") is None
        assert auth.authenticate(store, "nobody", "secret") is None

    def test_right_password_returns_the_user(self, store):
        auth.create_user(store, "luke", "Luke", "secret", "acc-luke")
        user = auth.authenticate(store, "luke", "secret")
        assert user["display_name"] == "Luke"
        assert user["jira_account_id"] == "acc-luke"

    def test_create_user_updates_the_same_username(self, store):
        auth.create_user(store, "luke", "Luke", "secret", "acc-luke")
        first = store.user_by_username("luke")
        auth.create_user(store, "luke", "Luke", "changed", "acc-new")
        second = store.user_by_username("luke")
        assert second["user_id"] == first["user_id"]
        assert second["jira_account_id"] == "acc-new"
        assert auth.authenticate(store, "luke", "secret") is None
        assert auth.authenticate(store, "luke", "changed")["user_id"] == first["user_id"]

    def test_password_is_stored_as_a_hash(self, store):
        auth.create_user(store, "luke", "Luke", "secret")
        with connect(store.db_path) as conn:
            stored = conn.execute(
                "SELECT password_hash FROM users WHERE username = 'luke'"
            ).fetchone()["password_hash"]
        assert stored != "secret"
        assert stored.startswith("pbkdf2_sha256$")

    def test_cli_add_writes_a_user(self, store):
        assert auth.main([
            "--db", store.db_path, "add",
            "--username", "neo",
            "--display-name", "Neo",
            "--password", "other",
            "--jira-account-id", "acc-neo",
        ]) == 0
        user = auth.authenticate(store, "neo", "other")
        assert user["display_name"] == "Neo"
        assert user["jira_account_id"] == "acc-neo"

    def test_a_new_user_must_change_password(self, store):
        auth.create_user(store, "luke", "Luke", "secret")
        user = auth.authenticate(store, "luke", "secret")
        assert user["must_change_password"] is True

    def test_change_password_clears_the_first_login_flag(self, store):
        user_id = auth.create_user(store, "luke", "Luke", "secret")
        auth.change_password(store, user_id, "chosen")
        assert auth.authenticate(store, "luke", "secret") is None
        user = auth.authenticate(store, "luke", "chosen")
        assert user["must_change_password"] is False

    def test_rejects_reusing_the_temporary_password(self, store):
        user_id = auth.create_user(store, "luke", "Luke", "secret")
        with pytest.raises(ValueError, match="different password"):
            auth.change_password(store, user_id, "secret")
        assert auth.authenticate(store, "luke", "secret")["must_change_password"] is True

    def test_default_users_are_created_once(self, store):
        assert auth.ensure_default_users(store) == 3
        assert store.count_users() == 3
        for username, display_name, account_id in auth.DEFAULT_USERS:
            user = auth.authenticate(store, username, "changeme")
            assert user["display_name"] == display_name
            assert user["jira_account_id"] == account_id
            assert user["must_change_password"] is True
        auth.change_password(store, store.user_by_username("luke")["user_id"], "chosen")
        assert auth.ensure_default_users(store) == 0
        assert auth.authenticate(store, "luke", "chosen")["must_change_password"] is False
        assert auth.authenticate(store, "luke", "changeme") is None


class TestSessionToken:
    def test_a_fresh_token_returns_its_user(self, store):
        auth.create_user(store, "luke", "Luke", "secret")
        user_id = store.user_by_username("luke")["user_id"]
        user = auth.user_from_session_token(store, auth.issue_session_token(store, user_id))
        assert user["username"] == "luke"
        assert user["user_id"] == user_id

    def test_an_expired_token_is_refused(self, store):
        auth.create_user(store, "luke", "Luke", "secret")
        user_id = store.user_by_username("luke")["user_id"]
        token = auth.issue_session_token(store, user_id, now=1_000_000)
        later = 1_000_000 + auth.SESSION_SECONDS + 1
        assert auth.user_from_session_token(store, token, now=later) is None
        assert auth.user_from_session_token(store, token, now=later - 5) is not None

    def test_a_forged_or_malformed_token_is_refused(self, store):
        auth.create_user(store, "luke", "Luke", "secret")
        auth.create_user(store, "neo", "Neo", "secret")
        luke = store.user_by_username("luke")["user_id"]
        neo = store.user_by_username("neo")["user_id"]
        token = auth.issue_session_token(store, luke)
        _, expires, signature = token.split(".")
        # Same signature claimed for another user.
        assert auth.user_from_session_token(store, f"{neo}.{expires}.{signature}") is None
        # A later expiry on the same signature.
        assert auth.user_from_session_token(store, f"{luke}.{int(expires) + 99}.{signature}") is None
        for junk in ("", "abc", "1.2", "x.y.z", "1.2.3.4"):
            assert auth.user_from_session_token(store, junk) is None

    def test_changing_the_password_signs_the_user_out(self, store):
        auth.create_user(store, "luke", "Luke", "secret")
        user_id = store.user_by_username("luke")["user_id"]
        token = auth.issue_session_token(store, user_id)
        auth.change_password(store, user_id, "something-else")
        assert auth.user_from_session_token(store, token) is None

    def test_the_secret_is_stable_private_and_beside_the_database(self, store):
        auth.create_user(store, "luke", "Luke", "secret")
        user_id = store.user_by_username("luke")["user_id"]
        first = auth.issue_session_token(store, user_id, now=5)
        second = auth.issue_session_token(store, user_id, now=5)
        assert first == second
        path = os.path.join(os.path.dirname(store.db_path), ".session_secret")
        assert os.path.exists(path)
        assert os.stat(path).st_mode & 0o077 == 0
