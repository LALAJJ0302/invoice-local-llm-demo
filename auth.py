"""Local dashboard login.

Usernames and password hashes live in the users table. Create an account with:

    ./.venv/bin/python auth.py add --username luke --display-name Luke --password changeme

The plaintext password is hashed before it is written. This is a three-person demo
gate, not a production identity system: no signup page, no reset.

A login survives a browser refresh through a signed token the dashboard keeps in the page
URL as `?s=...`. Streamlit's own session dies on refresh, and a database table for sessions
would have meant a schema bump that breaks every teammate's database on `git pull` until they
run a migration. So the token is stateless instead: it names the user and an expiry, and is
signed with a secret kept in `.session_secret` beside the database. The signature also covers
the user's password hash, so changing a password signs that user out everywhere.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import time
from typing import Any, Dict, Optional

from storage import DEFAULT_DB_PATH, StorageManager

_ITERATIONS = 200_000
_ALGO = "pbkdf2_sha256"
DEFAULT_PASSWORD = "changeme"
SESSION_PARAM = "s"
SESSION_SECONDS = 8 * 60 * 60
_SECRET_FILE = ".session_secret"
# Present on every fresh database. Missing usernames are inserted; an existing
# username is left alone so a password chosen at first login is not reset.
DEFAULT_USERS = (
    ("luke", "Tanut Pue-on", "712020:222c9879-2ae5-47e7-bac6-97a0919e6ce0"),
    ("neo", "Nattakrit Pitayasiri", "712020:9e3fc8c5-824a-434d-90df-6241b8aa7e12"),
    ("jj", "Chun-Jie Hsieh", "712020:8f20147f-80e8-48b1-9952-cce6a90cfcdb"),
)


def hash_password(password: str, *, salt: Optional[bytes] = None) -> str:
    """Returns pbkdf2_sha256$iterations$salt_hex$hash_hex."""
    if salt is None:
        salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return f"{_ALGO}${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of a password against a hash from hash_password."""
    try:
        algo, iterations, salt_hex, digest_hex = stored.split("$")
        if algo != _ALGO:
            return False
        salt = bytes.fromhex(salt_hex)
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate.hex(), digest_hex)


def create_user(
    store: StorageManager,
    username: str,
    display_name: str,
    password: str,
    jira_account_id: Optional[str] = None,
) -> int:
    """Stores one account. The same username updates the password and display name."""
    if not password:
        raise ValueError("password is required")
    return store.upsert_user(
        username,
        display_name,
        hash_password(password),
        jira_account_id,
    )


def ensure_default_users(store: StorageManager) -> int:
    """Inserts luke, neo and jj when those usernames are absent. Returns how many were added."""
    added = 0
    for username, display_name, jira_account_id in DEFAULT_USERS:
        if store.user_by_username(username) is None:
            create_user(store, username, display_name, DEFAULT_PASSWORD, jira_account_id)
            added += 1
    return added


def authenticate(
    store: StorageManager, username: str, password: str
) -> Optional[Dict[str, Any]]:
    """Returns the user dict on a match, otherwise None. Wrong password is None."""
    row = store.user_by_username(username)
    if row is None or not verify_password(password, row["password_hash"]):
        return None
    return _public(row)


def _public(row) -> Dict[str, Any]:
    return {
        "user_id": int(row["user_id"]),
        "username": row["username"],
        "display_name": row["display_name"],
        "jira_account_id": row["jira_account_id"],
        "must_change_password": bool(row["must_change_password"]),
    }


def _secret(store: StorageManager) -> bytes:
    """The signing key, created on first use beside the database and never committed."""
    path = os.path.join(os.path.dirname(os.path.abspath(store.db_path)), _SECRET_FILE)
    try:
        with open(path, "rb") as handle:
            key = handle.read().strip()
        if key:
            return key
    except FileNotFoundError:
        pass
    key = os.urandom(32).hex().encode("ascii")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(key)
    return key


def _sign(store: StorageManager, payload: str, password_hash: str) -> str:
    message = f"{payload}.{password_hash}".encode("utf-8")
    return hmac.new(_secret(store), message, hashlib.sha256).hexdigest()


def issue_session_token(
    store: StorageManager, user_id: int, *, now: Optional[float] = None
) -> str:
    """A token that proves this user signed in, valid for SESSION_SECONDS."""
    row = store.user_by_id(user_id)
    if row is None:
        raise ValueError(f"unknown user {user_id}")
    current = store.user_by_username(row["username"])
    expires = int((time.time() if now is None else now) + SESSION_SECONDS)
    payload = f"{int(user_id)}.{expires}"
    return f"{payload}.{_sign(store, payload, current['password_hash'])}"


def user_from_session_token(
    store: StorageManager, token: str, *, now: Optional[float] = None
) -> Optional[Dict[str, Any]]:
    """The user a token belongs to, or None if it is malformed, forged, expired or stale.

    Stale means the password changed since the token was issued, because the signature
    covers the password hash that was current then.
    """
    try:
        user_id_text, expires_text, signature = token.split(".")
        user_id, expires = int(user_id_text), int(expires_text)
    except (ValueError, AttributeError):
        return None
    if expires < (time.time() if now is None else now):
        return None
    row = store.user_by_id(user_id)
    if row is None:
        return None
    current = store.user_by_username(row["username"])
    if current is None:
        return None
    expected = _sign(store, f"{user_id}.{expires}", current["password_hash"])
    if not hmac.compare_digest(expected, signature):
        return None
    return _public(current)


def change_password(store: StorageManager, user_id: int, new_password: str) -> None:
    """Replaces the temporary password. The new one must differ from the current one."""
    if not new_password:
        raise ValueError("password is required")
    row = store.user_by_id(user_id)
    if row is None:
        raise ValueError(f"unknown user {user_id}")
    current = store.user_by_username(row["username"])
    if current is not None and verify_password(new_password, current["password_hash"]):
        raise ValueError("Choose a different password from the temporary one.")
    store.replace_password(user_id, hash_password(new_password))


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Create a dashboard login in the database.")
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="Add or update a user")
    add.add_argument("--username", required=True)
    add.add_argument("--display-name", required=True)
    add.add_argument("--password", required=True)
    add.add_argument("--jira-account-id", default=None)
    args = parser.parse_args(argv)

    store = StorageManager(args.db)
    user_id = create_user(
        store,
        args.username,
        args.display_name,
        args.password,
        args.jira_account_id,
    )
    print(f"Stored user {args.username!r} as user_id={user_id}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
