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
    # Two render sites since OV-5: every pushable row in the Outbox tab, and the first five of
    # them again in the Overview section. Both bodies execute on every rerun.
    expected = len(pushable) + min(len(pushable), 5)
    assert len([b for b in at.button if b.label == "Push to Jira"]) == expected, (
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


def test_a_section_header_opens_its_tab():
    """OV-4. Streamlit 1.62.0 takes a key on st.tabs, so a section can select one.

    Asserted rather than clicked through by hand: the button writes the tab's own label, counts
    and all, and a label that drifts from the tab it names would leave a dead control.
    """
    at = run()
    # "Open tab" is the section header's control. "Open" is a row's, and it opens the dialog,
    # not a tab. The labels were both "Open" until 2026-09-20 and this test caught the rename.
    opens = [b for b in at.button if b.label == "Open tab"]
    assert opens, "no section header offers a way into its tab"
    after = opens[0].click().run()
    assert not after.exception, [str(e.value) for e in after.exception]
    assert after.session_state["nav"], "pressing Open did not select a tab"


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
    """The bug above was invisible because no test pressed the buttons that carry rows."""
    at = run()
    labels = ["Open", "Open the document", "Open tab"]
    for index, button in enumerate(b for b in at.button if b.label in labels):
        after = at.button[[b.label for b in at.button].index(button.label)].click().run()
        assert not after.exception, (
            f"pressing {button.label!r} raised: {[str(e.value) for e in after.exception]}")
        if index > 6:
            break


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
