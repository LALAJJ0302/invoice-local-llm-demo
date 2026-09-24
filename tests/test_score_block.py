"""The validation score reaches the screen, and reaches it saying what it is.

`fe-score-spec.md` reverses a recorded decision. `fe-screen-spec.md` §3 had excluded the raw
score from the card and §4 gave the reason, that a person reading `0.85` cannot act on it. What
these tests pin is the condition under which that reversal is safe: the number never appears
alone. It appears against the gate's threshold and beside the gate's verdict, and the sentence
saying what it measures lives in `review_signals.py` with the rest of the copy.

The threshold test is the one that matters most over time. `app.py` draws the tick at a
position, and if someone retunes `ConfidenceValidator` the screen would keep drawing it in the
old place and quietly lie about where the pass mark is.
"""
import pathlib

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import app
import review_signals

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = str(ROOT / "app.py")


def run():
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def rendered(at):
    return " ".join(m.value for m in at.markdown if m.value)


# ---------------------------------------------------------------------
# The threshold, which is mirrored rather than imported
# ---------------------------------------------------------------------

def test_the_threshold_matches_the_gate():
    """`review_signals.GATE_THRESHOLD` exists so `app.py` need not import `main.py`.

    A mirrored constant is a constant that can drift, so this is the test that stops it. It is
    the only place in the suite that asserts the screen and the gate agree on the pass mark.
    """
    from main import ConfidenceValidator

    assert review_signals.GATE_THRESHOLD == ConfidenceValidator().threshold


def test_the_app_does_not_import_main():
    """`main.py` imports ollama at module level.

    The dashboard running on a machine that never pulled a model is a real property: it is what
    lets the screen be demonstrated without `ollama serve`. Importing main for one float would
    cost that, which is why the constant is mirrored into review_signals instead.
    """
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "import main" not in source
    assert "from main import" not in source


# ---------------------------------------------------------------------
# The component itself
# ---------------------------------------------------------------------

def test_the_block_carries_the_number_the_verdict_and_the_gate():
    html = app.score_block({"validation_score": 0.85, "validation_status": "Validated"})
    assert "0.85" in html
    assert "Validated" in html
    assert "gate 0.80" in html, "the number is shown without the limit it is judged against"


def test_needs_review_reads_as_two_words():
    """`NeedsReview` is one token in the database and two words to a reader."""
    html = app.score_block({"validation_score": 0.40, "validation_status": "NeedsReview"})
    assert "Needs review" in html
    assert "NeedsReview" not in html


def test_the_fill_is_the_score_and_the_tick_is_the_threshold():
    html = app.score_block({"validation_score": 0.85, "validation_status": "Validated"})
    assert "width:85.0%" in html
    assert "left:80.0%" in html


def test_a_score_outside_the_range_cannot_overflow_the_rail():
    """The column has a CHECK constraint, so this is defence rather than a known case."""
    assert "width:100.0%" in app.score_block({"validation_score": 1.4})
    assert "width:0.0%" in app.score_block({"validation_score": -0.2})


def test_the_state_colour_never_carries_the_meaning_alone():
    """Colour-blind, greyscale and printed, the verdict must still be readable.

    The rail is the only coloured part, and the word beside it says the same thing.
    """
    passed = app.score_block({"validation_score": 1.0, "validation_status": "Validated"})
    failed = app.score_block({"validation_score": 0.4, "validation_status": "NeedsReview"})
    assert "var(--positive)" in passed and "Validated" in passed
    assert "var(--caution)" in failed and "Needs review" in failed


def test_the_track_is_a_lighter_step_of_the_fills_own_ramp():
    """Not neutral grey. A same-ramp track lets the state read across the whole bar."""
    assert "var(--positive-wash)" in app.score_block(
        {"validation_score": 1.0, "validation_status": "Validated"})
    assert "var(--caution-wash)" in app.score_block(
        {"validation_score": 0.4, "validation_status": "NeedsReview"})


@pytest.mark.parametrize("row", [
    {"validation_score": None, "validation_status": None},
    {"validation_score": pd.NA, "validation_status": "Validated"},
    {},
])
def test_a_row_that_was_never_scored_draws_no_rail(row):
    """`validation_score` is nullable. An empty track would read as a score of zero."""
    html = app.score_block(row)
    assert "score-track" not in html
    assert "not scored" in html


def test_the_block_says_which_score_it_is():
    """A bare number invites "the model is 85% sure", which is false and is the single most
    damaging misreading available on this screen. CLAUDE.md records it as a confirmed defect."""
    html = app.score_block({"validation_score": 0.85, "validation_status": "Validated"})
    assert "<span class='score-label'>Validation score</span>" in html

    # The word appears once, inside the note, as a denial. It must never be the label.
    assert "score-label'>Confidence" not in html
    assert "not the model's confidence" in html


# ---------------------------------------------------------------------
# Where it lands
# ---------------------------------------------------------------------

def test_the_card_carries_the_score():
    """SC-1. The queue is where the score was missing and where it was asked for."""
    at = run()
    at.sidebar.radio[0].set_value("Awaiting approval").run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert "Validation score" in rendered(at)


def test_the_dialog_carries_the_score():
    """SC-2 and SC-3, asserted against the rendered dialog rather than the source."""
    at = run()
    review = next(b for b in at.button if b.key and b.key.startswith("rev-"))
    review.click().run()
    assert not at.exception, [str(e.value) for e in at.exception]

    html = rendered(at)
    assert "Validation score" in html
    assert "gate 0.80" in html
    assert review_signals.SCORE_NOTE_SHORT in html, (
        "the dialog is the one surface with room for the sentence saying what the score "
        "measures, and it is not there"
    )


def test_the_score_appears_wherever_a_document_does():
    """The defect this spec fixed was the number appearing on one surface out of nine.

    Half a fix is its own defect: a reader who sees the score on some documents and not others
    learns that it means something special about the ones that have it.
    """
    at = run()
    for view in ["Overview", "Awaiting approval", "Approved by the system", "Documents"]:
        at.sidebar.radio[0].set_value(view).run()
        assert not at.exception, [str(e.value) for e in at.exception]
        assert "score" in rendered(at).lower(), f"{view} shows documents without a score"


def test_history_carries_the_score_when_it_has_rows():
    """SC-4. History is empty on this database, so the assertion is on the renderer.

    Asserting on a frame rather than the screen is deliberate. Every row in `workflow_platform.db`
    has `reviewed_at IS NULL` today, so a screen-level test would pass by rendering nothing and
    would keep passing after someone deleted the column from the query.
    """
    assert "validation_score" in app.load_history().columns
    assert "validation_status" in app.load_history().columns


def test_the_dense_row_score_is_optional():
    """SC-6 added a sixth item. Vendors and pipeline runs pass five and have no score to show:
    a vendor is a group of documents and a run is not scored at all."""
    five = app.dense_rows([("var(--positive)", "Acme", "doc", "$1", "right")])
    six = app.dense_rows([("var(--positive)", "Acme", "doc", "$1", "right", "0.85")])
    assert "dense-score" not in five
    assert "dense-score" in six and "0.85" in six
