"""What the dashboard draws for the Gmail connection. See gmail-connect-spec.md.

Two pieces: the sidebar row that says what state the mailbox is in, and the body of the dialog
that connects or disconnects it. They live here, apart from app.py, so a test can draw them on
their own. `app.py` is a 2,800-line script that stops at the login form, and Streamlit's test
runner cannot click inside a dialog, so neither is testable from there.

`app.py` owns the `st.dialog` wrapper and the sidebar placement. This module owns what is in them.
"""

from __future__ import annotations

from html import escape
from typing import Optional

import streamlit as st

import mail_connection


def env_address() -> Optional[str]:
    """The address the listener would read from .env, or None when .env gives it no login."""
    credentials = mail_connection.env_credentials()
    return credentials[0] if credentials else None


def row_state(saved, env=None) -> tuple:
    """(dot class, title, one line of detail, button label).

    With no saved connection the listener still logs in from .env when that has a login, so
    "not connected" is only true when it has none either. Saying so is what keeps Disconnect
    honest on a machine whose .env is filled in.
    """
    if saved is None and env:
        return "env", "Gmail read from .env", env, "Connect Gmail"
    if saved is None:
        return "", "Gmail not connected", "No mailbox is being read", "Connect Gmail"
    if saved["status"] == "error":
        return ("warn", "Gmail needs attention", saved["last_error"] or "The last login failed",
                "Fix Gmail connection")
    return "ok", "Gmail connected", saved["email"], "Manage Gmail"


# The Gmail "M" mark, one colour, on a 24 unit grid. Decorative: the row's own text says Gmail.
GMAIL_MARK = (
    "<svg width='20' height='20' viewBox='0 0 24 24' aria-hidden='true' focusable='false'>"
    "<path fill='#EA4335' d='M24 5.457v13.909c0 .904-.732 1.636-1.636 1.636h-3.819V11.73L12 "
    "16.64l-6.545-4.91v9.273H1.636A1.636 1.636 0 0 1 0 19.366V5.457c0-2.023 2.309-3.178 "
    "3.927-1.964L5.455 4.64 12 9.548l6.545-4.91 1.528-1.145C21.69 2.28 24 3.434 24 5.457z'/>"
    "</svg>"
)


def status_row_html(saved, env=None) -> str:
    dot, title, sub, _ = row_state(saved, env)
    return (
        f"<div class='side-mail'><span class='side-gmail'>{GMAIL_MARK}"
        f"<span class='side-dot {dot}'></span></span>"
        f"<span class='side-name'><span class='side-who'>{escape(title)}</span>"
        f"<span class='side-sub' title='{escape(sub, quote=True)}'>{escape(sub)}</span></span></div>"
    )


def dialog_body(store, user_id: Optional[int], env: Optional[str] = None) -> None:
    """Connect form when nothing works, status and Disconnect when it does.

    The App Password is checked against Gmail before anything is stored and is never drawn back
    onto the page. `dialog_open` is held for the same reason the review dialog holds it: a live
    refresh while someone is typing would close the dialog and lose what they typed.
    """
    st.session_state["dialog_open"] = True
    saved = store.mail_connection()
    env = env_address() if env is None else env

    if saved is not None and saved["status"] == "connected":
        st.markdown(f"Connected as **{escape(saved['email'])}**.")
        by = saved["connected_by_name"] or "someone"
        st.caption(f"Connected by {by}. The listener uses this mailbox on its next check.")
        st.caption(
            f"After you disconnect, the listener goes back to {env} from .env." if env else
            "After you disconnect, the listener reads no mailbox until one is connected."
        )
        if st.button("Disconnect", key="gmail_disconnect", width="stretch"):
            store.clear_mail_connection()
            st.session_state["dialog_open"] = False
            st.rerun()
        return

    if saved is not None:
        st.error(saved["last_error"] or "The last login failed.")
    elif env:
        st.info(f"The listener is reading {env} from .env. Connecting here replaces it.")
    with st.form("gmail_connect", border=False):
        address = st.text_input("Gmail address", value=saved["email"] if saved else "")
        app_password = st.text_input("App Password", type="password")
        st.caption(
            "Use a Google App Password, not the account password. The account needs 2-Step "
            "Verification turned on, and an organisation account may have App Passwords "
            "turned off by its admin. Create one at myaccount.google.com/apppasswords."
        )
        submitted = st.form_submit_button("Connect", type="primary", width="stretch")
    if submitted:
        try:
            mail_connection.connect(store, address, app_password, user_id)
        except mail_connection.LoginProblem as problem:
            st.error(str(problem))
            return
        st.session_state["dialog_open"] = False
        st.rerun()
