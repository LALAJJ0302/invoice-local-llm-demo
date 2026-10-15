"""The Gmail connection the dashboard manages, and the listener reads.

A signed-in reviewer enters a Gmail address and an App Password in the dashboard. This module
checks the pair against Gmail, stores it encrypted, and hands it back to `email_listener.py`.
See gmail-connect-spec.md. There is no OAuth here: that needs a Google Cloud project and a
consent screen, which the group has not set up.

The App Password is encrypted with Fernet before it reaches the database. The key is
`MAIL_SECRET_KEY` from the environment, or a file `.mail_secret.key` beside this module,
created with mode 0600 on first use. What that protects is a copy of the database on its own:
a backup, a screenshot or an accidental commit no longer holds a usable password. It does not
protect anyone who has the database and the key file on the same machine.
"""

from __future__ import annotations

import imaplib
import os
import socket
import ssl
from pathlib import Path
from typing import Callable, Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken

import storage

KEY_ENV = "MAIL_SECRET_KEY"
KEY_FILE = Path(__file__).resolve().parent / ".mail_secret.key"
DEFAULT_HOST = "imap.gmail.com"
DEFAULT_PORT = 993
CHECK_TIMEOUT_SECONDS = 10


class WrongKey(RuntimeError):
    """The stored password cannot be decrypted with the key that is available now."""


class LoginProblem(Exception):
    """A check that failed, with a sentence a person can act on. Never carries the secret."""


# =====================================================================
# Encryption
# =====================================================================
def _read_or_create_key_file(path: Path) -> bytes:
    if path.exists():
        return path.read_bytes().strip()
    key = Fernet.generate_key()
    try:
        # O_EXCL so two processes starting together cannot each write a different key.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_bytes().strip()
    with os.fdopen(fd, "wb") as handle:
        handle.write(key)
    return key


def _fernet() -> Fernet:
    key = os.getenv(KEY_ENV)
    material = key.encode("ascii") if key else _read_or_create_key_file(KEY_FILE)
    return Fernet(material)


def encrypt(secret: str) -> bytes:
    return _fernet().encrypt(secret.encode("utf-8"))


def decrypt(blob: bytes) -> str:
    try:
        return _fernet().decrypt(bytes(blob)).decode("utf-8")
    except InvalidToken as error:
        raise WrongKey(
            "The saved Gmail password cannot be read with this machine's key. "
            "Connect Gmail again."
        ) from error


# =====================================================================
# Checking a login
# =====================================================================
def _host_port() -> Tuple[str, int]:
    return (os.getenv("EMAIL_HOST", DEFAULT_HOST), int(os.getenv("EMAIL_PORT", str(DEFAULT_PORT))))


def explain_refusal(error: Exception) -> str:
    """Turns Gmail's refusal into what the person should do. The server text is read, never kept."""
    text = str(error)
    if "Application-specific password required" in text:
        return ("Gmail wants an App Password, not the account password. Turn on 2-Step "
                "Verification, then create an App Password for Mail.")
    if "AUTHENTICATIONFAILED" in text or "Invalid credentials" in text:
        return ("Gmail refused this address and App Password. Check the address and that the "
                "16-character App Password was copied whole. If the account belongs to an "
                "organisation, its admin may have turned App Passwords off.")
    if "IMAP" in text and "disabled" in text.lower():
        return "IMAP access is turned off for this Gmail account."
    return "Gmail refused the login."


def check_login(
    email: str,
    app_password: str,
    *,
    imap_factory: Optional[Callable[..., imaplib.IMAP4]] = None,
    timeout: int = CHECK_TIMEOUT_SECONDS,
) -> None:
    """Logs in to Gmail and logs out again. Raises LoginProblem when it cannot.

    The factory is looked up here and not in the signature: a default of `imaplib.IMAP4_SSL`
    is bound when the module loads, so a test that patches imaplib would still reach Gmail.
    """
    imap_factory = imap_factory or imaplib.IMAP4_SSL
    host, port = _host_port()
    try:
        mail = imap_factory(host, port, timeout=timeout)
    except (socket.timeout, TimeoutError) as error:
        raise LoginProblem("Gmail did not answer in time. Check the connection and try again.") from error
    except (OSError, ssl.SSLError) as error:
        raise LoginProblem("Could not reach Gmail. Check the connection and try again.") from error
    try:
        mail.login(email, app_password)
    except imaplib.IMAP4.error as error:
        raise LoginProblem(explain_refusal(error)) from error
    except (socket.timeout, TimeoutError, OSError) as error:
        raise LoginProblem("Lost the connection to Gmail. Try again.") from error
    finally:
        try:
            mail.logout()
        except Exception:
            pass


# =====================================================================
# The stored connection
# =====================================================================
def connect(store: storage.StorageManager, email: str, app_password: str, user_id: int,
            *, imap_factory: Optional[Callable[..., imaplib.IMAP4]] = None) -> None:
    """Checks the pair with Gmail and stores it. Stores nothing when the check fails."""
    email = (email or "").strip()
    if not email or not app_password:
        raise LoginProblem("Enter the Gmail address and the App Password.")
    # Google shows the App Password in four groups with spaces. Nobody needs those spaces.
    app_password = "".join(app_password.split())
    check_login(email, app_password, imap_factory=imap_factory)
    store.save_mail_connection(email, encrypt(app_password), user_id)


def stored_credentials(db_path: str) -> Optional[Tuple[str, str]]:
    """(address, App Password) from the database, or None when nothing is connected.

    A database that is not at the current schema has no table yet, which also means nothing is
    connected, so the listener falls back to .env instead of failing.
    """
    row = storage.read_mail_connection(db_path)
    if row is None:
        return None
    return row["email"], decrypt(row["secret_encrypted"])


def record_success(db_path: str) -> None:
    storage.write_mail_status(db_path, "connected", None)


def record_failure(db_path: str, reason: str) -> None:
    storage.write_mail_status(db_path, "error", reason)
