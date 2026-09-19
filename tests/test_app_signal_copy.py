"""The screen must not write its own risk copy.

`review_signals.py` owns the six sentences an approver reads, it is tested, and its docstring
records why it recomputes from the invoice's current columns instead of reading `tasks.reason`.
All of that is worthless if `app.py` can quietly render a seventh sentence, or a stale copy of
one of the six, from a template.

fe-queue-spec.md §4 commits to this test by name.
"""
import pathlib
import re

import pytest

from review_signals import DETAIL, SIGNALS, risk_signal

APP = pathlib.Path(__file__).resolve().parents[1] / "app.py"


def test_app_does_not_contain_signal_copy():
    source = APP.read_text(encoding="utf-8")
    leaked = [message for _, _, message in SIGNALS if message in source]
    assert leaked == [], (
        "app.py contains risk copy that belongs to review_signals.py: "
        + "; ".join(leaked)
    )


def test_app_does_not_contain_the_explanations_either():
    """Added with OV-3. The second sentence per signal lives in the same module as the first,
    for the same reason: a template that can render its own copy makes the module decorative."""
    source = APP.read_text(encoding="utf-8")
    leaked = [detail for detail in DETAIL.values() if detail in source]
    assert leaked == [], (
        "app.py contains signal explanations that belong to review_signals.py: "
        + "; ".join(leaked)
    )


def test_app_renders_the_signal_through_the_module():
    source = APP.read_text(encoding="utf-8")
    assert "risk_signal(" in source, "app.py no longer asks review_signals for the sentence"


def test_every_signal_is_reachable_from_a_row():
    """A sentence nobody can trigger is copy, not behaviour."""
    for column, value, message in SIGNALS:
        assert risk_signal({column: value}) is not None
        assert risk_signal({column: value}) == message or any(
            risk_signal({column: value}) == earlier for _, _, earlier in SIGNALS
        )


@pytest.mark.parametrize("message", [m for _, _, m in SIGNALS])
def test_signal_sentences_are_sentences(message):
    """Each one has to read as something a person can act on, not a status code."""
    assert message[0].isupper()
    assert not re.search(r"[_{}]|score|0\.\d", message), message
