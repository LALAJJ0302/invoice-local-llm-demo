"""The Overview page as rendered: six cards, what needs attention, and the recent table."""
import os
import re
import sys

import pytest
from streamlit.testing.v1 import AppTest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from signed_in import REPO_ROOT, sign_in  # noqa: E402

APP = os.path.join(REPO_ROOT, "app.py")


def overview():
    at = sign_in(AppTest.from_file(APP, default_timeout=120)).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    if not at.radio:
        pytest.skip("no documents in this database, so no Overview")
    return at, " ".join(m.value for m in at.markdown if m.value)


def test_the_six_cards_are_there_in_order():
    _, html = overview()
    labels = re.findall(r"<span class='ov-label'>([^<]+)</span>", html)
    assert labels == ["Invoices", "Total amount", "Waiting for you", "Approved by a person",
                      "Needs attention", "Approved by the system"]


def test_the_needs_attention_card_matches_the_rows_it_points_at():
    import app
    import overview_data
    df = app.load_data()
    pending = df[df["approval_status"] == "Pending"]
    expected = len(overview_data.attention_rows(pending, app.waiting_days))
    _, html = overview()
    shown = re.search(r"Needs attention</span><div class='ov-value'>"
                      r"<span class='ov-number'>(\d+)</span>", html)
    assert int(shown.group(1)) == expected


def test_every_attention_row_says_why_and_offers_review():
    at, html = overview()
    rows = [b for b in at.button if b.key and b.key.startswith("ovattn-")]
    assert all(b.label == "Review" for b in rows)
    for b in rows:
        assert not b.disabled


def test_recent_invoices_keeps_validation_and_workflow_apart_and_is_capped():
    at, html = overview()
    assert "<span>Validation</span><span>Workflow</span>" in html
    opens = [b for b in at.button if b.key and b.key.startswith("ovrec-")]
    assert 0 < len(opens) <= 10


def test_the_removed_sections_are_not_on_overview_but_their_destinations_still_exist():
    at, html = overview()
    assert "decisions a person made, including rejections" not in html
    assert at.sidebar.radio[0].options  # the sidebar still lists every destination
    assert any("History" in str(o) for o in at.sidebar.radio[0].options)


def test_the_empty_overview_shows_placeholders_and_no_number():
    """The empty database is drawn by empty_overview_html. It must never show a figure that is
    not real, so every stat is a grey placeholder and there is no ov-number at all."""
    import app
    html = app.empty_overview_html()
    labels = re.findall(r"<span class='ov-label'>([^<]+)</span>", html)
    assert labels == ["Invoices", "Total amount", "Waiting for you", "Approved by a person",
                      "Needs attention", "Approved by the system"]
    assert "ov-number" not in html
    assert html.count("ph-value") == 6
    assert "title-art" in html and "Nothing has been stored yet." in html
    assert "<code>main.py</code>" in html


def test_the_contradiction_tiles_show_stated_sum_and_difference():
    """Rule 3 of the restyle: the gap is numbers side by side, not a sentence."""
    import app
    html = app.clash_tiles(1500.0, "USD", 0.0, 0)
    assert "Stated total</span><span class='ct-num'>1,500.00" in html
    assert "Sum of line items</span><span class='ct-num'>0.00" in html and "none stored" in html
    assert "ct-risk" in html and "Difference</span><span class='ct-num'>1,500.00" in html
    # Rows that do add up leave no difference, and nothing is marked as a risk.
    ok = app.clash_tiles(1500.0, "USD", 1500.0, 3)
    assert "Difference</span><span class='ct-num'>0.00" in ok and "ct-risk" not in ok
    assert app.clash_tiles(None, "USD", 0.0, 0) == ""
