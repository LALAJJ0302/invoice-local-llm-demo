"""Local dashboard login.

Usernames and password hashes live in the users table. Create an account with:

    ./.venv/bin/python auth.py add --username luke --display-name Luke --password changeme

The plaintext password is hashed before it is written. This is a three-person demo
gate, not a production identity system: no signup page, no reset, and the session
is Streamlit's tab session.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
from typing import Any, Dict, Optional

from storage import DEFAULT_DB_PATH, StorageManager

_ITERATIONS = 200_000
_ALGO = "pbkdf2_sha256"
DEFAULT_PASSWORD = "changeme"
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
    return {
        "user_id": int(row["user_id"]),
        "username": row["username"],
        "display_name": row["display_name"],
        "jira_account_id": row["jira_account_id"],
        "must_change_password": bool(row["must_change_password"]),
    }


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
