"""The pipeline says, in red, when a re-sent document keeps a person's earlier decision.

Reported 2026-10-13. A rejected water bill was sent again to be reviewed afresh. The upsert kept
the rejection, by design (storage.save_invoice never overwrites a human decision), so no review
task opened and the bill did not reappear. The console said only "Updated invoice_id=51", which
read as if the bill had vanished. The rule stays; the pipeline now says what it did and how to
undo it.
"""
import io
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from main import kept_decision_notice, red  # noqa: E402


class FakeTerminal(io.StringIO):
    def isatty(self):
        return True


def test_a_kept_rejection_is_explained():
    notice = kept_decision_notice({"invoice_id": 51, "kept_decision": "Rejected"})
    assert "invoice_id=51" in notice
    assert "Rejected" in notice
    assert "History" in notice and "Reopen" in notice


def test_nothing_is_said_when_no_decision_was_kept():
    assert kept_decision_notice({"invoice_id": 7, "kept_decision": None}) is None
    assert kept_decision_notice({"invoice_id": 7}) is None


def test_red_on_a_terminal(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert red("kept", FakeTerminal()) == "\033[31mkept\033[0m"


def test_plain_when_output_is_redirected(monkeypatch):
    """A log file full of escape codes is harder to read than one without colour."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert red("kept", io.StringIO()) == "kept"


def test_no_color_is_respected(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert red("kept", FakeTerminal()) == "kept"
