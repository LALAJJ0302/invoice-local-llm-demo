"""The fixes from fe-ui-review-spec.md, pinned.

Each one was found by screenshotting the running app on 2026-10-12, and several are copy, which
is the kind of defect that comes back quietly: someone reads the old sentence in git history,
likes it, and restores it. These fail when that happens.
"""
import pathlib

from streamlit.testing.v1 import AppTest

from signed_in import sign_in

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = str(ROOT / "app.py")
SOURCE = (ROOT / "app.py").read_text()


def view(name):
    at = sign_in(AppTest.from_file(APP, default_timeout=120))
    at.query_params["v"] = name
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def page_html(at) -> str:
    return " ".join(str(m.value) for m in at.markdown)


def test_waiting_is_shown_only_for_a_document_that_is_waiting():
    """F8. 'waiting 35d' was printed on documents the system had already approved."""
    import app
    base = {"invoice_number": "INV-1", "invoice_date": "2026-08-10",
            "email_received_at": "2026-08-10 00:00:00"}
    assert "waiting" in app.dialog_meta({**base, "approval_status": "Pending"})
    assert "waiting" not in app.dialog_meta({**base, "approval_status": "Approved"})
    assert "waiting" not in app.dialog_meta({**base, "approval_status": "Rejected"})


def test_the_three_notes_no_longer_repeat_their_subtitles():
    """F7. Documents, Vendors and Pipeline runs each said the same sentence twice."""
    import app
    for name in ("documents", "vendors", "runs"):
        subtitle = app.PAGE_HEADINGS[name][1]
        notes = [str(m.value) for m in view(name).markdown if "tab-note" in str(m.value)]
        assert notes, name
        assert all(subtitle not in note for note in notes), name


def test_the_developer_commentary_is_gone():
    """F7. Two of the notes talked about the project rather than to the reader. Checked on the
    rendered page: the source splits these sentences across string literals, so a search of the
    file would pass whether or not they were still there."""
    shown = {"vendors": "the arithmetic this project watches the model for",
             "documents": "which is not the same as them being worth hiding"}
    for name, phrase in shown.items():
        assert phrase not in page_html(view(name)), name


def test_documents_has_a_labelled_score_column_and_the_gate():
    """F3, F4. A bare 0.85 breaks the three conditions of fe-score-spec.md."""
    from review_signals import DOCUMENTS_SCORE_NOTE, GATE_THRESHOLD
    html = page_html(view("documents"))
    assert "Validation score" in html
    assert "dense-head" in html
    assert DOCUMENTS_SCORE_NOTE in html
    assert f"{GATE_THRESHOLD:.2f}" in DOCUMENTS_SCORE_NOTE
    assert "confidence" in DOCUMENTS_SCORE_NOTE.lower()  # says what it is not


def test_documents_status_is_a_word_as_well_as_a_dot():
    """F4. Colour must not be the only signal."""
    html = page_html(view("documents"))
    assert "dense-status" in html
    assert "Waiting" in html or "Decided" in html


def test_a_score_above_the_gate_with_a_failed_check_is_explained():
    """F1. 0.87 beside 'Needs review' read as a contradiction with nothing joining the two."""
    from review_signals import DETAIL
    detail = DETAIL["Did not pass the validation gate"]
    assert "clear the score and still fail a rule" in detail


def test_the_waiting_tile_says_why_it_has_no_total():
    """F6."""
    assert "in more than one currency, so no single total" in SOURCE
    assert '"more than one currency"' not in SOURCE


def test_the_two_dialog_panels_have_headings_that_can_be_seen():
    """F13. They were 12px muted grey and Neo could not find them."""
    css = SOURCE[SOURCE.index(".panel-label {"):]
    rule = css[:css.index("}")]
    assert "font-size:15px" in rule and "font-weight:600" in rule
    assert "var(--text-muted)" not in rule


def test_the_dialog_vendor_row_shows_the_vendor():
    """F9. It showed 'read from the document' in a panel titled 'What the model stored'."""
    assert 'field_row("Vendor", "read from the document"' not in SOURCE
    assert 'field_row("Vendor", escape(' in SOURCE


def test_the_contradiction_heading_names_what_it_counts():
    """F10."""
    assert "The document shows {clash['count']} line item" in SOURCE
