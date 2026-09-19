"""The tab bar's promises, asserted rather than looked at.

fe-tabs-spec.md §8 commits to these by name. The dispatch path itself is already covered by
tests/test_task_dispatch.py, including the retry this screen's Push button depends on, so what
is left is what the screen does with it.

These run against the working database rather than a fixture, deliberately. Every assertion
here is about two parts of the screen agreeing with each other, so whatever the data happens to
be, they must still agree.
"""
import pathlib
import re

from streamlit.testing.v1 import AppTest

APP = str(pathlib.Path(__file__).resolve().parents[1] / "app.py")


def run():
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def tab_labels(at):
    return [label for block in at.get("tabs") for label in getattr(block, "labels", [])] or None


def test_the_app_renders_without_exceptions():
    run()


def test_push_is_disabled_while_jira_is_unconfigured():
    """A button that looks live and silently does nothing is worse than one that explains."""
    import app  # noqa: F401  (imported for jira_ready)
    at = run()
    pushes = [b for b in at.button if b.label == "Push to Jira"]
    from jira_client import JiraClient
    if JiraClient().is_configured():
        return  # a configured machine is allowed to have live buttons
    assert pushes, "no Push button rendered; the Outbox should offer one per pushable Jira row"
    assert all(b.disabled for b in pushes), "Push is enabled while Jira is not configured"


def test_push_appears_only_for_rows_that_have_a_transport():
    """Teams rows can never be sent, so they get no button. Nothing has written one since
    2026-09-08: queue_outbound is called from one place and it passes Jira."""
    import app
    outbox = app.load_outbox()
    if outbox.empty:
        return
    jira = outbox[outbox["channel"] == "Jira"]
    pushable = jira[jira["state"].isin(["Pending", "Failed"])]
    at = run()
    assert len([b for b in at.button if b.label == "Push to Jira"]) == len(pushable)


def test_overview_agrees_with_the_tab_labels():
    """Overview duplicates by design. The point of it is that the duplicate agrees.

    Rewritten 2026-09-20 with the page. It used to read four counters out of a 2x2 grid; the
    page now leads with three tiles and reports the outbox split in a section heading, so that
    is what is checked.
    """
    import app
    df = app.load_data()
    pending = df[df["approval_status"] == "Pending"]
    auto = df[(df["approval_status"] == "Approved") & (df["reviewed_at"].isna())]
    outbox = app.load_outbox()

    at = run()
    rendered = " ".join(m.value for m in at.markdown if m.value)

    tiles = [int(n) for n in re.findall(r"<span class='ov-number'>(\d+)</span>", rendered)]
    assert tiles[:2] == [len(pending), len(auto)], (
        f"the tiles show {tiles[:2]}, the tabs count {[len(pending), len(auto)]}")

    jira = outbox[outbox["channel"] == "Jira"]
    pushable = len(jira[jira["state"].isin(["Pending", "Failed"])])
    frozen = len(outbox[outbox["channel"] != "Jira"])
    sent = int((outbox["state"] == "Sent").sum())
    assert f"{pushable} ready to push, {frozen} with no transport, {sent} sent" in rendered


def test_overview_shows_the_waiting_documents_and_not_only_a_count():
    """The complaint that produced the page: four boxes with numbers in them answered nothing."""
    import app
    df = app.load_data()
    pending = df[df["approval_status"] == "Pending"]
    at = run()
    rendered = " ".join(m.value for m in at.markdown if m.value)
    if pending.empty:
        assert "Nothing is waiting for you" in rendered
    else:
        top = pending.sort_values("email_received_at", na_position="last").iloc[0]
        assert str(top["vendor_name"]) in rendered, (
            "Overview reports a count but does not show the document behind it")


def test_history_holds_only_decisions_a_person_made():
    import app
    history = app.load_history()
    assert history["reviewed_at"].notna().all(), (
        "History contains a row the system decided; reviewed_at is the column that separates them")


def test_no_total_is_summed_across_currencies():
    """The rule that produced `3 · 6,500` from USD 1,500 + USD 2,350 + AUD 2,650."""
    import app
    import pandas as pd
    mixed = pd.DataFrame({"currency": ["USD", "AUD"], "total_amount": [1.0, 2.0]})
    same = pd.DataFrame({"currency": ["USD", "USD"], "total_amount": [1.0, 2.0]})
    assert app.single_currency_total(mixed) is None
    assert app.single_currency_total(same) == "USD 3"
    assert app.single_currency_total(pd.DataFrame({"currency": [], "total_amount": []})) is None


def test_the_tab_bar_no_longer_says_notifications():
    at = run()
    labels = " ".join(str(getattr(t, "label", "")) for t in at.get("tab"))
    rendered = labels + " ".join(m.value for m in at.markdown if m.value)
    assert not re.search(r"\bNotifications\s+\d", rendered), "a tab is still labelled Notifications"
