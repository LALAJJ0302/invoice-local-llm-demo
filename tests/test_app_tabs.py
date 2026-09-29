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

from signed_in import sign_in

APP = str(pathlib.Path(__file__).resolve().parents[1] / "app.py")


def run():
    at = sign_in(AppTest.from_file(APP, default_timeout=120)).run()
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
    # One render site since the tab bar was replaced by the sidebar on 2026-09-20. Every
    # destination used to execute on every rerun because st.tabs renders all of its bodies;
    # only the chosen one runs now, so the Overview section and the Outbox destination are
    # counted separately rather than added together.
    at = run()
    assert len([b for b in at.button if b.label == "Push to Jira"]) == min(len(pushable), 5), (
        "the Overview outbox section should offer one button per pushable row, up to five")

    at = run()
    at.sidebar.radio[0].set_value("outbox").run()
    assert len([b for b in at.button if b.label == "Push to Jira"]) == len(pushable), (
        "a row without a transport has grown a Push button, or a pushable one has lost it")


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


def test_no_control_claims_to_open_a_tab():
    """OV-4 was removed on 2026-09-20 because it never worked, and this keeps it removed.

    `st.tabs` in Streamlit 1.62.0 takes a `key` and a `default`, and neither selects a tab from
    code. The key records what the user picked; writing it changes the session value and the
    frontend keeps its own selection. Measured in a real browser, which is the only place it can
    be measured: aria-selected stayed on Overview before and after the button was pressed, with
    both the key and the default tried.

    The first version of this test asserted that the session key had been written, saw that it
    had, and passed for two commits while the button did nothing. Asserting the mechanism
    instead of the outcome is how a dead control survives a green suite.

    `st.segmented_control` can be driven this way, measured the same way. Swapping the tab bar
    for one is the fix, and it is a change to the page's navigation rather than a patch.
    """
    at = run()
    dead = [b for b in at.button if b.label in ("Open tab", "Open the tab")]
    assert not dead, "a control is offering to open a tab, which st.tabs cannot do"


def test_the_card_carries_everything_the_approved_design_carries():
    """OV-1 and OV-2. Three of the five things in the approved card's footer were missing."""
    import app
    df = app.load_data()
    if df.empty:
        return
    at = run()
    rendered = " ".join(m.value for m in at.markdown if m.value)
    assert "Covering email" in rendered, "the covering email chip is missing from the card"
    assert [b for b in at.button if b.label == "Open the document"] or \
           [b for b in at.button if b.label == "Review document"], "no card action rendered"


def test_the_signal_explanation_comes_from_the_module():
    """OV-3. The sentence is rendered, and app.py does not contain it."""
    import app
    from review_signals import DETAIL, risk_detail
    df = app.load_data()
    # Only documents drawn as cards carry the strip. A document a person has already decided on
    # appears in History as a row, which has no signal on it.
    on_a_card = df[(df["approval_status"] == "Pending")
                   | ((df["approval_status"] == "Approved") & (df["reviewed_at"].isna()))]
    flagged = [r for _, r in on_a_card.iterrows() if risk_detail(r)]
    if not flagged:
        return
    at = run()
    rendered = " ".join(m.value for m in at.markdown if m.value)
    assert risk_detail(flagged[0]) in rendered
    assert not any(d in pathlib.Path(APP).read_text(encoding="utf-8") for d in DETAIL.values())


def test_a_history_row_carries_what_the_dialog_reads():
    """Regression, 2026-09-20. Opening a History row raised KeyError: 'id'.

    The lookup used `load_data().set_index("id")`, which moves the id out of the row and into
    the index, and `review_dialog` reads `row["id"]` on every path that records a decision. The
    tests all passed: none of them opened a History row, because the only Open button any test
    pressed was a section header's.
    """
    import app
    decided = app.load_data()
    history = app.load_history()
    if history.empty or decided.empty:
        return
    for _, h in history.iterrows():
        match = decided[decided["id"] == h["invoice_id"]]
        assert not match.empty, f"history row {h['invoice_number']} has no invoice behind it"
        row = match.iloc[0]
        # Exactly what review_dialog reads before it renders anything.
        for column in ("id", "vendor_name", "invoice_number", "document_type", "total_amount",
                       "currency", "archive_path"):
            assert column in row.index, f"{column} is missing from the row the dialog receives"


def test_every_open_button_can_be_pressed():
    """The bug above was invisible because no test pressed the buttons that carry rows.

    Buttons are found by key rather than by label now. Two different controls are labelled
    Open: the row actions, keyed ovauto-/ovhist-, and the section headers, keyed open-. Looking
    a button up by its label picked whichever came first, and a section header press navigates
    away, so the next lookup failed on a page that no longer had that button.
    """
    at = run()
    keys = [b.key for b in at.button
            if b.label in ("Open", "Open the document") and not b.key.startswith("open-")]
    for key in keys[:7]:
        fresh = run()
        after = next(b for b in fresh.button if b.key == key).click().run()
        assert not after.exception, (
            f"pressing {key!r} raised: {[str(e.value) for e in after.exception]}")


def test_a_section_header_opens_its_destination():
    """OV-4, asserted by outcome.

    The first version of this control was built on st.tabs, which cannot be driven from code.
    A test then asserted that the session key had been written, saw that it had, and passed for
    two commits while the button did nothing. So this asserts the thing a person would notice:
    the sidebar's selection moves.
    """
    for key, destination in (("open-awaiting", "awaiting"), ("open-outbox", "outbox"),
                             ("open-history", "history")):
        at = run()
        assert at.sidebar.radio[0].value == "overview"
        next(b for b in at.button if b.key == key).click().run()
        assert at.sidebar.radio[0].value == destination, (
            f"{key} did not move the sidebar to {destination}")


def test_the_review_note_replaces_the_checkbox():
    """Migration 013. The tick box wrote is_done, which nothing read. A note is read: it is
    shown to whoever opens the document next."""
    import app
    from storage import StorageManager
    df = app.load_data()
    assert "review_note" in df.columns, "load_data does not carry the note to the screen"
    assert "review_note_at" in df.columns

    store = StorageManager(app.DB_PATH)
    invoice_id = int(df.iloc[0]["id"])
    before = df.iloc[0]["review_note"]
    try:
        store.set_review_note(invoice_id, "  spaces are stripped  ")
        row = app.load_data().set_index("id").loc[invoice_id]
        assert row["review_note"] == "spaces are stripped"
        assert row["review_note_at"], "a note without a timestamp is a state nothing means"

        store.set_review_note(invoice_id, "")
        row = app.load_data().set_index("id").loc[invoice_id]
        assert row["review_note"] is None and row["review_note_at"] is None, (
            "clearing a note must clear its timestamp, so 'has a note' stays one condition")
    finally:
        store.set_review_note(invoice_id, "" if before is None or str(before) == "nan" else before)


# =====================================================================
# Reviewer tracking and the product name. Reported by Luke, 2026-09-28.
# =====================================================================

def test_history_shows_who_decided_and_not_only_when():
    """The name was stored by record_decision and rendered nowhere.

    History carried the timestamp of a decision and not the person who made it, which makes
    `reviewed_by` a column that costs a migration and answers nothing. Asserted on the frame
    rather than the screen because every row in this database has `reviewed_at IS NULL`, so a
    screen-level test would pass by rendering no rows at all.
    """
    import app

    assert "reviewer_name" in app.load_history().columns, (
        "History cannot reach the reviewer's name, so it can only ever show the time")

    source = pathlib.Path(app.__file__).read_text(encoding="utf-8")
    assert "hist-who" in source, "the name is available to History and still not rendered"


def test_the_human_jira_dispatch_names_the_reviewer():
    """An issue labelled human-reviewed and attributed to nobody is worse than no label.

    `task_dispatch._reviewer_for_jira` returns an empty reviewer and no reporter account when
    `reviewer_user_id` is None, so the approval path says a person was involved while the one
    field naming that person stays blank.
    """
    import re

    import app

    source = pathlib.Path(app.__file__).read_text(encoding="utf-8")
    call = re.search(r"dispatch_task_to_jira\((.*?)\n            \)", source, re.S)
    assert call, "the human dispatch call could not be found"
    assert "approval_path=\"human\"" in call.group(1)
    assert "reviewer_user_id=" in call.group(1), (
        "the human approval path dispatches to Jira without saying which human")


def test_the_auth_screens_carry_the_product_name():
    """Luke's call, 2026-09-28: Invoice approvals on both, no lightning bolt.

    Scoped to `app.py` deliberately. The Payables sidebar, the design HTML and the briefing
    keep their own wording, and a test that forbade the old name everywhere would fail on
    files he asked to be left alone.
    """
    import app

    source = pathlib.Path(app.__file__).read_text(encoding="utf-8")
    assert "Enterprise AI Workflow Automation Dashboard" not in source
    assert "⚡" not in source

    # Asserted per function rather than by counting. The title also appears on the
    # empty-database notice and on the page itself, so a global count says nothing about
    # whether the two screens Luke named are the ones that carry it.
    import inspect

    for screen in (app.require_login, app.require_password_change):
        body = inspect.getsource(screen)
        assert 'st.title("Invoice approvals")' in body, screen.__name__
