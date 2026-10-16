"""The Gmail connection: what is stored, what the listener reads, and what the sidebar says.

gmail-connect-spec.md section 8 names these. Nothing here talks to Gmail: login is a fake IMAP
class, so a refusal, a timeout and a success are all one line to set up. The one check against
real Gmail is recorded by hand, not here.
"""
import imaplib
import os
import pathlib
import socket
import stat

import pytest
from cryptography.fernet import Fernet
from streamlit.proto.TextInput_pb2 import TextInput
from streamlit.testing.v1 import AppTest

import auth
import email_listener
import mail_connection
import storage
from signed_in import sign_in
from storage import StorageManager, connect

APP = str(pathlib.Path(__file__).resolve().parents[1] / "app.py")
SECRET = "abcdefghijklmnop"


class FakeIMAP:
    """Stands in for imaplib.IMAP4_SSL. `refuse` is the text of the server's NO, if any."""

    refuse = None
    logins = []
    contexts = []

    def __init__(self, host, port, timeout=None, ssl_context=None):
        self.host, self.port, self.timeout = host, port, timeout
        FakeIMAP.contexts.append(ssl_context)

    def login(self, user, password):
        FakeIMAP.logins.append((user, password))
        if FakeIMAP.refuse:
            raise imaplib.IMAP4.error(FakeIMAP.refuse)

    def logout(self):
        pass


@pytest.fixture(autouse=True)
def fake_imap(monkeypatch):
    """imaplib is one module, so patching it here is patching it for the dialog and the listener."""
    FakeIMAP.refuse = None
    FakeIMAP.logins = []
    FakeIMAP.contexts = []
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)
    return FakeIMAP


@pytest.fixture(autouse=True)
def no_dotenv_file(monkeypatch):
    """The machine's own .env must not decide what these tests see. The environment still can."""
    monkeypatch.delenv("EMAIL_USER", raising=False)
    monkeypatch.delenv("EMAIL_PASSWORD", raising=False)
    monkeypatch.setattr(mail_connection, "dotenv_values", lambda *a, **k: {})
    monkeypatch.setattr(email_listener, "load_dotenv", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv(mail_connection.KEY_ENV, Fernet.generate_key().decode())


@pytest.fixture
def store(tmp_path):
    s = StorageManager(str(tmp_path / "mail.db"))
    auth.ensure_default_users(s)
    return s


def user_id(store):
    return store.user_by_username("neo")["user_id"]


def connected(store):
    mail_connection.connect(store, "ap@example.com", SECRET, user_id(store),
                            imap_factory=FakeIMAP)


# =====================================================================
# Encryption
# =====================================================================
class TestEncryption:
    def test_round_trip(self):
        assert mail_connection.decrypt(mail_connection.encrypt(SECRET)) == SECRET

    def test_ciphertext_does_not_contain_the_secret(self):
        assert SECRET.encode() not in mail_connection.encrypt(SECRET)

    def test_a_different_key_fails_without_leaking(self, monkeypatch):
        blob = mail_connection.encrypt(SECRET)
        monkeypatch.setenv(mail_connection.KEY_ENV, Fernet.generate_key().decode())
        with pytest.raises(mail_connection.WrongKey) as raised:
            mail_connection.decrypt(blob)
        assert SECRET not in str(raised.value)

    def test_a_key_file_is_created_private_when_no_variable_is_set(self, monkeypatch, tmp_path):
        monkeypatch.delenv(mail_connection.KEY_ENV)
        path = tmp_path / ".mail_secret.key"
        monkeypatch.setattr(mail_connection, "KEY_FILE", path)
        blob = mail_connection.encrypt(SECRET)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert mail_connection.decrypt(blob) == SECRET  # the second call reads the same file


# =====================================================================
# Checking a login
# =====================================================================
class TestCheckLogin:
    def test_a_good_login_passes(self):
        mail_connection.check_login("a@b.com", SECRET, imap_factory=FakeIMAP)
        assert FakeIMAP.logins == [("a@b.com", SECRET)]

    @pytest.mark.parametrize("server_text,expected", [
        ("[AUTHENTICATIONFAILED] Invalid credentials (Failure)", "refused this address"),
        ("[ALERT] Application-specific password required", "wants an App Password"),
        ("something unexpected", "refused the login"),
    ])
    def test_a_refusal_becomes_an_instruction(self, server_text, expected):
        FakeIMAP.refuse = server_text
        with pytest.raises(mail_connection.LoginProblem) as raised:
            mail_connection.check_login("a@b.com", SECRET, imap_factory=FakeIMAP)
        assert expected in str(raised.value)
        assert SECRET not in str(raised.value)

    def test_a_timeout_says_gmail_did_not_answer(self):
        def slow(*args, **kwargs):
            raise socket.timeout()
        with pytest.raises(mail_connection.LoginProblem, match="did not answer"):
            mail_connection.check_login("a@b.com", SECRET, imap_factory=slow)

    def test_no_network_says_gmail_could_not_be_reached(self):
        def down(*args, **kwargs):
            raise OSError("network is unreachable")
        with pytest.raises(mail_connection.LoginProblem, match="Could not reach"):
            mail_connection.check_login("a@b.com", SECRET, imap_factory=down)


# =====================================================================
# Storing the connection
# =====================================================================
class TestConnect:
    def test_success_stores_an_encrypted_secret(self, store):
        connected(store)
        row = store.mail_connection()
        assert row["email"] == "ap@example.com"
        assert row["status"] == "connected"
        assert row["connected_by_name"] == "Nattakrit Pitayasiri"
        assert mail_connection.stored_credentials(store.db_path) == ("ap@example.com", SECRET)

    def test_the_password_is_not_in_the_database_file(self, store):
        connected(store)
        with connect(store.db_path) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        raw = open(store.db_path, "rb").read()
        assert SECRET.encode() not in raw

    def test_a_refusal_stores_nothing(self, store):
        FakeIMAP.refuse = "[AUTHENTICATIONFAILED] Invalid credentials (Failure)"
        with pytest.raises(mail_connection.LoginProblem):
            connected(store)
        assert store.mail_connection() is None

    def test_spaces_in_a_pasted_app_password_are_dropped(self, store):
        mail_connection.connect(store, "ap@example.com", "abcd efgh ijkl mnop",
                                user_id(store), imap_factory=FakeIMAP)
        assert mail_connection.stored_credentials(store.db_path)[1] == "abcdefghijklmnop"

    @pytest.mark.parametrize("address,password", [("", SECRET), ("a@b.com", ""), (" ", " ")])
    def test_a_blank_field_is_refused_before_gmail_is_asked(self, store, address, password):
        with pytest.raises(mail_connection.LoginProblem):
            mail_connection.connect(store, address, password, user_id(store),
                                    imap_factory=FakeIMAP)
        assert FakeIMAP.logins == []

    def test_connecting_again_replaces_the_one_row(self, store):
        connected(store)
        mail_connection.connect(store, "other@example.com", "zzzzzzzzzzzzzzzz",
                                user_id(store), imap_factory=FakeIMAP)
        with connect(store.db_path) as conn:
            assert conn.execute("SELECT COUNT(*) c FROM mail_connection").fetchone()["c"] == 1
        assert store.mail_connection()["email"] == "other@example.com"

    def test_disconnect_removes_the_secret(self, store):
        connected(store)
        assert store.clear_mail_connection() is True
        assert store.mail_connection() is None
        assert mail_connection.stored_credentials(store.db_path) is None


class TestStoredCredentials:
    def test_none_when_the_file_is_missing(self, tmp_path):
        assert mail_connection.stored_credentials(str(tmp_path / "nope.db")) is None

    def test_none_when_the_table_is_missing(self, tmp_path):
        import sqlite3
        path = str(tmp_path / "old.db")
        sqlite3.connect(path).close()
        assert mail_connection.stored_credentials(path) is None


# =====================================================================
# The listener
# =====================================================================
class TestListenerLogin:
    def test_uses_the_dashboard_connection_first(self, store, monkeypatch):
        monkeypatch.setenv("EMAIL_USER", "env@example.com")
        monkeypatch.setenv("EMAIL_PASSWORD", "from-the-env-file")
        connected(store)
        FakeIMAP.logins.clear()
        email_listener.connect_to_gmail(store.db_path)
        assert FakeIMAP.logins == [("ap@example.com", SECRET)]

    def test_falls_back_to_the_env_file(self, store, monkeypatch):
        monkeypatch.setenv("EMAIL_USER", "env@example.com")
        monkeypatch.setenv("EMAIL_PASSWORD", "from-the-env-file")
        email_listener.connect_to_gmail(store.db_path)
        assert FakeIMAP.logins == [("env@example.com", "from-the-env-file")]

    def test_says_so_when_neither_exists(self, store, monkeypatch):
        monkeypatch.delenv("EMAIL_USER", raising=False)
        monkeypatch.delenv("EMAIL_PASSWORD", raising=False)
        monkeypatch.setattr(email_listener, "load_dotenv", lambda: None)
        with pytest.raises(ValueError, match="no Gmail is connected"):
            email_listener.connect_to_gmail(store.db_path)

    def test_a_refused_login_is_recorded_for_the_sidebar(self, store):
        connected(store)
        FakeIMAP.refuse = "[AUTHENTICATIONFAILED] Invalid credentials (Failure)"
        with pytest.raises(imaplib.IMAP4.error):
            email_listener.connect_to_gmail(store.db_path)
        row = store.mail_connection()
        assert row["status"] == "error"
        assert "refused this address" in row["last_error"]
        assert SECRET not in row["last_error"]

    def test_a_later_success_clears_the_error(self, store):
        connected(store)
        FakeIMAP.refuse = "[AUTHENTICATIONFAILED] Invalid credentials (Failure)"
        with pytest.raises(imaplib.IMAP4.error):
            email_listener.connect_to_gmail(store.db_path)
        FakeIMAP.refuse = None
        email_listener.connect_to_gmail(store.db_path)
        row = store.mail_connection()
        assert (row["status"], row["last_error"]) == ("connected", None)

    def test_a_key_that_no_longer_fits_is_reported_not_guessed_around(self, store, monkeypatch):
        connected(store)
        monkeypatch.setenv(mail_connection.KEY_ENV, Fernet.generate_key().decode())
        with pytest.raises(mail_connection.WrongKey):
            email_listener.connect_to_gmail(store.db_path)
        assert store.mail_connection()["status"] == "error"

    def test_an_env_login_that_fails_writes_nothing(self, store, monkeypatch):
        monkeypatch.setenv("EMAIL_USER", "env@example.com")
        monkeypatch.setenv("EMAIL_PASSWORD", "bad")
        FakeIMAP.refuse = "[AUTHENTICATIONFAILED] Invalid credentials (Failure)"
        with pytest.raises(imaplib.IMAP4.error):
            email_listener.connect_to_gmail(store.db_path)
        assert store.mail_connection() is None


class TestStatusWrites:
    def test_the_same_failure_on_every_poll_does_not_redraw_the_page(self, store):
        connected(store)
        mail_connection.record_failure(store.db_path, "Gmail refused the login.")
        before = store.change_token()
        for _ in range(5):
            mail_connection.record_failure(store.db_path, "Gmail refused the login.")
        assert store.change_token() == before

    def test_a_change_of_state_does_redraw_it(self, store):
        connected(store)
        before = store.change_token()
        mail_connection.record_failure(store.db_path, "Gmail refused the login.")
        assert store.change_token() != before


# =====================================================================
# The sidebar
# =====================================================================
@pytest.fixture
def ui_store():
    """The suite's isolated UI database. Left as it was found, because every UI test shares it."""
    s = StorageManager(os.environ["WORKFLOW_DB_PATH"])
    s.clear_mail_connection()
    yield s
    s.clear_mail_connection()


def render():
    at = sign_in(AppTest.from_file(APP, default_timeout=120)).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def sidebar_text(at):
    return " ".join(str(m.value) for m in at.sidebar.markdown)


def test_the_sidebar_says_when_nothing_is_connected(ui_store):
    at = render()
    assert "Gmail not connected" in sidebar_text(at)
    assert [b for b in at.sidebar.button if b.label == "Connect Gmail"]


def test_the_sidebar_shows_the_connected_address_and_never_the_password(ui_store):
    uid = ui_store.user_by_username("neo")["user_id"]
    mail_connection.connect(ui_store, "ap@example.com", SECRET, uid, imap_factory=FakeIMAP)
    at = render()
    text = sidebar_text(at)
    assert "Gmail connected" in text and "ap@example.com" in text
    assert SECRET not in text
    assert [b for b in at.sidebar.button if b.label == "Manage Gmail"]


def test_the_sidebar_shows_the_reason_when_the_login_fails(ui_store):
    uid = ui_store.user_by_username("neo")["user_id"]
    mail_connection.connect(ui_store, "ap@example.com", SECRET, uid, imap_factory=FakeIMAP)
    mail_connection.record_failure(ui_store.db_path, "Gmail refused the login.")
    at = render()
    text = sidebar_text(at)
    assert "Gmail needs attention" in text and "Gmail refused the login." in text
    assert [b for b in at.sidebar.button if b.label == "Fix Gmail connection"]


def test_a_hostile_address_is_escaped_in_the_sidebar(ui_store):
    uid = ui_store.user_by_username("neo")["user_id"]
    ui_store.save_mail_connection("<script>x</script>@e.com", mail_connection.encrypt(SECRET), uid)
    text = sidebar_text(render())
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


# =====================================================================
# The dialog
#
# Drawn on its own through gmail_panel. Streamlit's test runner cannot click inside a real
# st.dialog, so the wrapper in app.py is checked in a browser and the body is checked here.
# =====================================================================
def dialog_script():
    import os

    import gmail_panel
    from storage import StorageManager

    store = StorageManager(os.environ["WORKFLOW_DB_PATH"])
    gmail_panel.dialog_body(store, store.user_by_username("neo")["user_id"])


def draw_dialog():
    at = AppTest.from_function(dialog_script, default_timeout=60).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def fill_and_connect(at):
    fields = {t.label: t for t in at.text_input}
    fields["Gmail address"].input("ap@example.com")
    fields["App Password"].input(SECRET)
    [b for b in at.button if b.label == "Connect"][0].click()
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def test_the_connect_form_asks_for_an_address_and_an_app_password(ui_store):
    at = draw_dialog()
    assert [t.label for t in at.text_input] == ["Gmail address", "App Password"]
    assert at.text_input[1].proto.type == TextInput.PASSWORD
    assert "2-Step" in " ".join(c.value for c in at.caption)


def test_connecting_from_the_dialog_stores_the_pair(ui_store):
    at = fill_and_connect(draw_dialog())
    assert mail_connection.stored_credentials(ui_store.db_path) == ("ap@example.com", SECRET)
    assert SECRET not in " ".join(str(m.value) for m in at.markdown)


def test_a_refused_pair_shows_the_reason_and_stores_nothing(ui_store):
    FakeIMAP.refuse = "[AUTHENTICATIONFAILED] Invalid credentials (Failure)"
    at = fill_and_connect(draw_dialog())
    assert any("refused this address" in e.value for e in at.error)
    assert ui_store.mail_connection() is None
    assert SECRET not in " ".join(str(e.value) for e in at.error)


def test_a_connected_mailbox_shows_disconnect_and_no_form(ui_store):
    uid = ui_store.user_by_username("neo")["user_id"]
    mail_connection.connect(ui_store, "ap@example.com", SECRET, uid, imap_factory=FakeIMAP)
    at = draw_dialog()
    assert not at.text_input
    assert any("ap@example.com" in str(m.value) for m in at.markdown)
    assert SECRET not in " ".join(str(m.value) for m in at.markdown)


def test_disconnect_removes_the_connection(ui_store):
    uid = ui_store.user_by_username("neo")["user_id"]
    mail_connection.connect(ui_store, "ap@example.com", SECRET, uid, imap_factory=FakeIMAP)
    at = draw_dialog()
    [b for b in at.button if b.label == "Disconnect"][0].click()
    at.run()
    assert ui_store.mail_connection() is None


def test_a_failed_connection_shows_its_reason_above_the_form(ui_store):
    uid = ui_store.user_by_username("neo")["user_id"]
    mail_connection.connect(ui_store, "ap@example.com", SECRET, uid, imap_factory=FakeIMAP)
    mail_connection.record_failure(ui_store.db_path, "Gmail refused the login.")
    at = draw_dialog()
    assert any("Gmail refused the login." in e.value for e in at.error)
    assert at.text_input[0].value == "ap@example.com"


def test_an_empty_database_still_offers_the_gmail_row(monkeypatch, tmp_path):
    """A fresh install has no documents and so no sidebar, and it is the install that most needs
    to connect a mailbox. The row is drawn in the page body instead."""
    path = str(tmp_path / "empty.db")
    StorageManager(path)
    monkeypatch.setenv("WORKFLOW_DB_PATH", path)
    at = render()
    assert not at.sidebar.markdown
    assert "Gmail not connected" in " ".join(str(m.value) for m in at.main.markdown)
    assert [b for b in at.main.button if b.label == "Connect Gmail"]


def test_the_row_carries_the_gmail_mark_and_a_state_dot_for_each_state():
    import gmail_panel
    for saved, dot in ((None, "side-dot "), ({"status": "connected", "email": "a@b.com", "last_error": None}, "side-dot ok"),
                       ({"status": "error", "email": "a@b.com", "last_error": "x"}, "side-dot warn")):
        html = gmail_panel.status_row_html(saved)
        assert "<svg" in html and "aria-hidden='true'" in html
        assert dot in html


# =====================================================================
# Certificate verification (JJ's review of PR #25)
# =====================================================================
def assert_verifies(context):
    import ssl
    assert context is not None, "no TLS context was passed, so imaplib would verify nothing"
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    # A context that verifies but trusts nothing refuses Gmail itself. That is what the default
    # context does on a python.org Python for macOS, and why the bundle comes from certifi.
    assert context.cert_store_stats()["x509_ca"] > 0


def test_the_context_verifies_and_trusts_the_bundle():
    assert_verifies(mail_connection.imap_ssl_context())


def test_the_dashboard_check_verifies_gmails_certificate(store):
    connected(store)
    assert_verifies(FakeIMAP.contexts[-1])


def test_the_listener_login_verifies_gmails_certificate(store, monkeypatch):
    monkeypatch.setenv("EMAIL_USER", "env@example.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "from-the-env-file")
    email_listener.connect_to_gmail(store.db_path)
    assert_verifies(FakeIMAP.contexts[-1])


def test_a_saved_connection_login_verifies_gmails_certificate(store):
    connected(store)
    FakeIMAP.contexts.clear()
    email_listener.connect_to_gmail(store.db_path)
    assert_verifies(FakeIMAP.contexts[-1])


# =====================================================================
# The .env fallback, shown honestly (JJ's review of PR #25)
# =====================================================================
ENV_FILE = {"EMAIL_USER": "env@example.com", "EMAIL_PASSWORD": "from-the-env-file"}


class TestEnvCredentials:
    def test_none_without_both_values(self, monkeypatch):
        assert mail_connection.env_credentials() is None
        monkeypatch.setenv("EMAIL_USER", "env@example.com")
        assert mail_connection.env_credentials() is None

    def test_the_environment_counts(self, monkeypatch):
        monkeypatch.setenv("EMAIL_USER", "env@example.com")
        monkeypatch.setenv("EMAIL_PASSWORD", "x")
        assert mail_connection.env_credentials() == ("env@example.com", "x")

    def test_the_file_counts_and_the_environment_wins(self, monkeypatch):
        monkeypatch.setattr(mail_connection, "dotenv_values", lambda *a, **k: ENV_FILE)
        assert mail_connection.env_credentials() == ("env@example.com", "from-the-env-file")
        monkeypatch.setenv("EMAIL_USER", "shell@example.com")
        assert mail_connection.env_credentials()[0] == "shell@example.com"

    def test_looking_does_not_copy_the_password_into_the_process(self, monkeypatch):
        monkeypatch.setattr(mail_connection, "dotenv_values", lambda *a, **k: ENV_FILE)
        mail_connection.env_credentials()
        assert "EMAIL_PASSWORD" not in os.environ

    def test_the_listener_and_the_dashboard_agree_on_what_the_fallback_is(self, store, monkeypatch):
        monkeypatch.setattr(mail_connection, "dotenv_values", lambda *a, **k: ENV_FILE)
        email_listener.connect_to_gmail(store.db_path)
        assert FakeIMAP.logins[-1] == mail_connection.env_credentials()


def test_the_row_says_the_listener_reads_env_when_nothing_is_connected():
    import gmail_panel
    html = gmail_panel.status_row_html(None, "env@example.com")
    assert "Gmail read from .env" in html and "env@example.com" in html
    assert "side-dot env" in html
    assert "No mailbox is being read" not in html


def test_the_row_still_says_nothing_is_read_when_env_has_no_login():
    import gmail_panel
    assert "No mailbox is being read" in gmail_panel.status_row_html(None, None)


def test_a_saved_connection_outranks_the_env_wording():
    import gmail_panel
    saved = {"status": "connected", "email": "ap@example.com", "last_error": None}
    html = gmail_panel.status_row_html(saved, "env@example.com")
    assert "Gmail connected" in html and "from .env" not in html


def test_after_disconnect_the_sidebar_names_the_env_mailbox(ui_store, monkeypatch):
    """JJ's second point: on a machine whose .env is filled in, the listener goes on reading
    Gmail after Disconnect, so the sidebar must not say that no mailbox is being read."""
    monkeypatch.setattr(mail_connection, "dotenv_values", lambda *a, **k: ENV_FILE)
    uid = ui_store.user_by_username("neo")["user_id"]
    mail_connection.connect(ui_store, "ap@example.com", SECRET, uid, imap_factory=FakeIMAP)
    at = draw_dialog()
    [b for b in at.button if b.label == "Disconnect"][0].click()
    at.run()
    assert ui_store.mail_connection() is None
    text = sidebar_text(render())
    assert "Gmail read from .env" in text and "env@example.com" in text
    assert "No mailbox is being read" not in text


def test_the_connected_dialog_says_where_the_listener_goes_after_disconnect(ui_store, monkeypatch):
    uid = ui_store.user_by_username("neo")["user_id"]
    mail_connection.connect(ui_store, "ap@example.com", SECRET, uid, imap_factory=FakeIMAP)
    assert any("reads no mailbox" in c.value for c in draw_dialog().caption)
    monkeypatch.setattr(mail_connection, "dotenv_values", lambda *a, **k: ENV_FILE)
    assert any("goes back to env@example.com from .env" in c.value for c in draw_dialog().caption)


def test_the_connect_form_warns_that_it_replaces_the_env_mailbox(ui_store, monkeypatch):
    assert not draw_dialog().info
    monkeypatch.setattr(mail_connection, "dotenv_values", lambda *a, **k: ENV_FILE)
    assert any("env@example.com from .env" in i.value for i in draw_dialog().info)
