"""One precondition, stated once: the app under test has somebody signed in.

`app.py` calls `require_login()`, which ends in `st.stop()`. Under `AppTest` that means a test
which does not sign in renders a login form and then asserts against a page that was never
drawn. Sixty tests across four files render the app, so the precondition lives here rather than
sixty times over.

**Seeded, not bypassed.** The alternative considered was an environment variable in `app.py`
that skips `require_login()`. That was refused: it would put a code path in the shipped
application whose only purpose is to skip authentication, in a file three people read. A test
that states its own precondition costs one line at the call site and leaves the application
with exactly one way in.

The user is looked up rather than hardcoded. `auth.ensure_default_users` inserts luke, neo and
jj when they are absent, and their ids depend on insertion order, so `user_id = 1` would be an
assumption about which of them was created first.
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import auth  # noqa: E402
from storage import DEFAULT_DB_PATH, StorageManager  # noqa: E402


def sign_in(at, username: str = "neo"):
    """Puts a real user in `AppTest`'s session. Returns the same AppTest, for chaining.

    Call it before `.run()`. Afterwards is too late: `require_login()` runs during the script,
    and a session written after the fact is a session the page never saw.

    `must_change_password` is forced False. These tests are about the approval screen, and a
    first-login password prompt is a different screen with its own coverage in `test_auth.py`.
    """
    store = StorageManager(os.path.join(REPO_ROOT, DEFAULT_DB_PATH))
    auth.ensure_default_users(store)
    user = store.user_by_username(username)
    assert user is not None, f"{username!r} is missing after ensure_default_users"

    at.session_state["user_id"] = user["user_id"]
    at.session_state["display_name"] = user["display_name"]
    at.session_state["must_change_password"] = False
    return at
