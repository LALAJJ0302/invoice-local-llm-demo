"""Tests for email_listener.py. IMAP and sleep are mocked; nothing contacts Gmail."""

import imaplib
import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import email_listener  # noqa: E402
import storage  # noqa: E402

RAW_INVOICE = (
    b"From: vendor@example.com\r\n"
    b"To: invoices@example.com\r\n"
    b"Subject: invoice 1042\r\n"
    b"Message-ID: <one@example.com>\r\n"
    b"Date: Tue, 1 Sep 2026 00:00:00 +0000\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"Please see the invoice.\r\n"
)


def _mailbox(search_payload):
    mail = MagicMock()
    mail.search.return_value = ("OK", [search_payload])
    mail.fetch.return_value = ("OK", [(b"1 (RFC822)", RAW_INVOICE)])
    return mail


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ATTACHMENT_DIR", str(tmp_path / "inbox"))
    return str(tmp_path / "mail.db")


def test_search_is_unread_invoice_mail_only(isolated, monkeypatch):
    mail = _mailbox(b"")
    monkeypatch.setattr(email_listener, "connect_to_gmail", lambda db_path=None: mail)

    email_listener.download_attachments(db_path=isolated)

    assert mail.search.call_args.args == (None, '(UNSEEN SUBJECT "invoice")')
    criteria = mail.search.call_args.args[1]
    assert criteria.startswith("(UNSEEN")
    assert "ALL" not in criteria
    mail.logout.assert_called_once()


def test_marks_seen_only_after_the_message_is_recorded(isolated, monkeypatch):
    mail = _mailbox(b"1")
    order = []
    original = storage.StorageManager.record_email

    def record(self, *args, **kwargs):
        order.append("record")
        return original(self, *args, **kwargs)

    def store_flag(*args, **kwargs):
        order.append("seen")
        return ("OK", None)

    monkeypatch.setattr(storage.StorageManager, "record_email", record)
    monkeypatch.setattr(email_listener, "connect_to_gmail", lambda db_path=None: mail)
    mail.store.side_effect = store_flag

    email_listener.download_attachments(db_path=isolated)

    assert order == ["record", "seen"]
    mail.store.assert_called_once_with(b"1", "+FLAGS", "\\Seen")


def test_fetch_failure_leaves_the_message_unread(isolated, monkeypatch):
    mail = _mailbox(b"1")
    mail.fetch.return_value = ("NO", [None])
    monkeypatch.setattr(email_listener, "connect_to_gmail", lambda db_path=None: mail)

    email_listener.download_attachments(db_path=isolated)

    mail.store.assert_not_called()


def test_record_failure_leaves_the_message_unread(isolated, monkeypatch):
    mail = _mailbox(b"1")
    monkeypatch.setattr(email_listener, "connect_to_gmail", lambda db_path=None: mail)

    def boom(self, *args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(storage.StorageManager, "record_email", boom)

    with pytest.raises(RuntimeError, match="db down"):
        email_listener.download_attachments(db_path=isolated)

    mail.store.assert_not_called()
    mail.logout.assert_called_once()


def test_poll_searches_more_than_once_then_stops(isolated, tmp_path, monkeypatch):
    mail = _mailbox(b"")
    monkeypatch.setattr(email_listener, "connect_to_gmail", lambda db_path=None: mail)
    sleeps = {"n": 0}

    def fake_sleep(seconds):
        sleeps["n"] += 1
        assert seconds == 12
        if sleeps["n"] >= 2:
            raise KeyboardInterrupt

    email_listener.poll_inbox(
        db_path=isolated,
        interval=12,
        sleep_fn=fake_sleep,
        inbox_dir=tmp_path / "inbox",
    )

    assert mail.search.call_count == 2
    assert all(
        call.args == (None, email_listener.UNREAD_INVOICE_SEARCH)
        for call in mail.search.call_args_list
    )


def test_failed_check_still_claims_a_file_already_in_inbox(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "already.pdf").write_bytes(b"x")
    calls = {"n": 0}
    claimed = []

    def fake_download(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise imaplib.IMAP4.error("connection reset")
        raise KeyboardInterrupt

    def spawn(process_fn, batch, active):
        claimed.append(sorted(path.name for path in Path(batch).iterdir()))

    sleeps = []
    monkeypatch.setattr(email_listener, "download_attachments", fake_download)

    email_listener.poll_inbox(
        interval=60,
        sleep_fn=lambda seconds: sleeps.append(seconds),
        spawn_fn=spawn,
        inbox_dir=inbox,
    )

    assert calls["n"] == 2
    assert claimed == [["already.pdf"]]
    assert sleeps == [60]
    assert not (inbox / "already.pdf").exists()


def test_poll_stops_when_sleep_is_interrupted(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_download(**kwargs):
        calls["n"] += 1

    def interrupt(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(email_listener, "download_attachments", fake_download)

    email_listener.poll_inbox(
        interval=60,
        sleep_fn=interrupt,
        inbox_dir=tmp_path / "inbox",
    )

    assert calls["n"] == 1


def test_default_cli_polls_and_once_does_not(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        email_listener, "poll_inbox", lambda **kwargs: seen.setdefault("poll", kwargs)
    )
    monkeypatch.setattr(
        email_listener, "download_attachments", lambda **kwargs: seen.setdefault("once", True)
    )

    assert email_listener.main([]) == 0
    assert seen["poll"]["interval"] is None
    assert "once" not in seen

    seen.clear()
    assert email_listener.main(["--once"]) == 0
    assert seen.get("once") is True
    assert "poll" not in seen


def test_interval_env_override_and_floor(monkeypatch):
    monkeypatch.setenv("EMAIL_POLL_INTERVAL", "45")
    assert email_listener.poll_interval_seconds() == 45
    assert email_listener.poll_interval_seconds(10) == 10

    monkeypatch.setenv("EMAIL_POLL_INTERVAL", "nope")
    assert email_listener.poll_interval_seconds() == 10
    assert email_listener.poll_interval_seconds(0) == 1


def test_poll_processes_inbox_after_each_check_before_sleep(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    order = []

    def fake_download(**kwargs):
        order.append("download")
        (inbox / f"file-{order.count('download')}.pdf").write_bytes(b"x")

    def spawn(process_fn, batch, active):
        order.append("process")
        process_fn(str(batch))

    def fake_sleep(seconds):
        order.append(("sleep", seconds))
        if len(order) >= 6:
            raise KeyboardInterrupt

    monkeypatch.setattr(email_listener, "download_attachments", fake_download)

    email_listener.poll_inbox(
        interval=10,
        sleep_fn=fake_sleep,
        process_fn=lambda batch: None,
        spawn_fn=spawn,
        inbox_dir=inbox,
    )

    assert order == [
        "download",
        "process",
        ("sleep", 10),
        "download",
        "process",
        ("sleep", 10),
    ]


def test_second_poll_claims_only_files_still_in_the_inbox(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for name in ("one.pdf", "two.pdf", "three.pdf"):
        (inbox / name).write_bytes(b"x")
    claimed = []
    downloads = {"n": 0}

    def fake_download(**kwargs):
        downloads["n"] += 1
        if downloads["n"] == 2:
            (inbox / "four.pdf").write_bytes(b"4")

    def spawn(process_fn, batch, active):
        claimed.append(sorted(path.name for path in Path(batch).iterdir()))

    def fake_sleep(seconds):
        if len(claimed) >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(email_listener, "download_attachments", fake_download)

    email_listener.poll_inbox(
        interval=10,
        sleep_fn=fake_sleep,
        spawn_fn=spawn,
        inbox_dir=inbox,
    )

    assert claimed == [
        ["one.pdf", "three.pdf", "two.pdf"],
        ["four.pdf"],
    ]
    assert not (inbox / "one.pdf").exists()


def test_blocking_pipeline_does_not_delay_the_next_sleep(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "a.pdf").write_bytes(b"a")
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    errors = []

    def blocking(batch):
        entered.set()
        if not release.wait(timeout=2):
            errors.append("pipeline blocked the next sleep")
        finished.set()

    def sleep_fn(seconds):
        release.set()
        raise KeyboardInterrupt

    monkeypatch.setattr(email_listener, "download_attachments", lambda **kwargs: None)
    started = time.monotonic()
    email_listener.poll_inbox(
        inbox_dir=inbox,
        interval=10,
        sleep_fn=sleep_fn,
        process_fn=blocking,
    )
    elapsed = time.monotonic() - started

    assert elapsed < 1
    assert entered.wait(timeout=2)
    assert finished.wait(timeout=2)
    assert errors == []


def test_pipeline_error_returns_the_file_and_polling_continues(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "stuck.pdf").write_bytes(b"pdf")
    processes = {"n": 0}

    def process(batch):
        processes["n"] += 1
        if processes["n"] == 1:
            raise RuntimeError("ollama down")

    def sleep_fn(seconds):
        if processes["n"] >= 2:
            raise KeyboardInterrupt

    def inline(process_fn, batch, active):
        email_listener.finish_batch(batch, process_fn, active)

    monkeypatch.setattr(email_listener, "download_attachments", lambda **kwargs: None)

    email_listener.poll_inbox(
        interval=10,
        sleep_fn=sleep_fn,
        process_fn=process,
        spawn_fn=inline,
        inbox_dir=inbox,
    )

    assert processes["n"] == 2
    assert (inbox / "stuck.pdf").is_file()
    processing = inbox / ".processing"
    assert not processing.exists() or list(processing.iterdir()) == []


def test_empty_inbox_starts_no_worker(tmp_path, monkeypatch):
    started = []

    def spawn(process_fn, batch, active):
        started.append(batch)

    def sleep_fn(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(email_listener, "download_attachments", lambda **kwargs: None)

    email_listener.poll_inbox(
        inbox_dir=tmp_path / "inbox",
        interval=10,
        sleep_fn=sleep_fn,
        spawn_fn=spawn,
    )

    assert started == []


def test_startup_recovers_a_stranded_batch_before_the_first_claim(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    stranded = inbox / ".processing" / "20261012-000000"
    stranded.mkdir(parents=True)
    (stranded / "left.pdf").write_bytes(b"x")
    claimed = []

    def spawn(process_fn, batch, active):
        claimed.append(sorted(path.name for path in Path(batch).iterdir()))

    def sleep_fn(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(email_listener, "download_attachments", lambda **kwargs: None)

    email_listener.poll_inbox(
        inbox_dir=inbox,
        interval=10,
        sleep_fn=sleep_fn,
        spawn_fn=spawn,
    )

    assert claimed == [["left.pdf"]]
    assert not stranded.exists()


def test_recover_leaves_a_live_batch_alone(tmp_path):
    inbox = tmp_path / "inbox"
    live = inbox / ".processing" / "live"
    dead = inbox / ".processing" / "dead"
    live.mkdir(parents=True)
    dead.mkdir()
    (live / "busy.pdf").write_bytes(b"b")
    (dead / "old.pdf").write_bytes(b"o")

    email_listener.recover_stranded_batches(inbox, active={live.resolve()})

    assert (live / "busy.pdf").is_file()
    assert (inbox / "old.pdf").read_bytes() == b"o"
    assert not dead.exists()


def test_claim_moves_only_real_files(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "a.pdf").write_bytes(b"a")
    (inbox / ".hidden").write_bytes(b"h")
    (inbox / "subdir").mkdir()
    monkeypatch.setattr(email_listener.time, "strftime", lambda fmt: "20261012-000000")

    batch = email_listener.claim_inbox(inbox)

    assert batch.name == "20261012-000000"
    assert sorted(path.name for path in batch.iterdir()) == ["a.pdf"]
    assert (inbox / ".hidden").is_file()
    assert (inbox / "subdir").is_dir()
    assert email_listener.claim_inbox(inbox) is None

    (inbox / "b.pdf").write_bytes(b"b")
    second = email_listener.claim_inbox(inbox)
    assert second.name == "20261012-000000-1"
    assert (second / "b.pdf").is_file()


def test_release_does_not_overwrite_a_newer_inbox_file(tmp_path):
    inbox = tmp_path / "inbox"
    batch = inbox / ".processing" / "batch"
    batch.mkdir(parents=True)
    (batch / "same.pdf").write_bytes(b"old")
    (inbox / "same.pdf").write_bytes(b"new")

    email_listener.release_batch(batch)

    assert (inbox / "same.pdf").read_bytes() == b"new"
    assert (inbox / "same_1.pdf").read_bytes() == b"old"
    assert not batch.exists()


def test_once_does_not_run_the_pipeline(monkeypatch):
    called = []
    monkeypatch.setattr(
        email_listener, "download_attachments", lambda **kwargs: called.append("download")
    )
    monkeypatch.setattr(
        email_listener, "claim_inbox", lambda *args, **kwargs: called.append("claim")
    )
    monkeypatch.setattr(
        email_listener, "run_pipeline", lambda *args, **kwargs: called.append("process")
    )

    assert email_listener.main(["--once"]) == 0
    assert called == ["download"]
