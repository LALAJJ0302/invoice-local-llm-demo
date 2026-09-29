"""The line that warns a person before they approve has to be true.

`fe-backlog.md` FE-12 asks for the name of the Jira task to be on screen before the button is
pressed. It was built, and what it said was wrong: it promised an issue "immediately" on a
system where `dispatch_task_to_jira` returns without contacting anything unless Jira is
configured. Approving still does something, a local task and a queued outbox row, so the fix is
to say that rather than to remove the line.

These tests pin the two things that could quietly rot: the title drifting from the issue that
gets created, and the unconfigured copy being softened back into a promise.
"""
import pathlib

from task_dispatch import _build_summary

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = (ROOT / "app.py").read_text()


def test_preview_uses_the_dispatchers_own_summary():
    """The dialog must not re-spell the title format.

    Two copies of `f"{task_type}: {file_name} - {vendor}"` drift the moment either side is
    edited, and the screen would go on showing a title no issue ever carries.
    """
    assert "task_dispatch._build_summary(" in APP


def test_the_title_is_the_one_jira_would_get():
    assert (_build_summary("Payment", "invoice_001.pdf", "Apex Cloud Solutions Pty Ltd")
            == "Payment: invoice_001.pdf - Apex Cloud Solutions Pty Ltd")


def test_unconfigured_copy_says_nothing_is_sent():
    """The whole point of the correction. If this sentence goes, the screen overstates again."""
    assert "Jira is not configured, so nothing is sent" in APP


def test_configured_and_unconfigured_copy_both_exist():
    """One branch each. A single unconditional sentence cannot be true in both states."""
    assert "Approving creates a Jira issue immediately" in APP
    assert "queues it for Jira" in APP


def test_follow_up_type_per_document():
    """An invoice still has to be paid, a receipt only has to be filed."""
    import app
    assert app.follow_up_for("Invoice") == "Payment"
    assert app.follow_up_for("Receipt") == "File"
    assert app.follow_up_for("Something else") == "Review"
