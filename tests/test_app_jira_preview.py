"""What the review dialog says about Jira.

`fe-backlog.md` FE-12 put the Jira task title in the dialog footer. It was built, and what it
said was wrong: it promised an issue "immediately" on a system where `dispatch_task_to_jira`
returns without contacting anything unless Jira is configured. It was corrected, then removed on
2026-10-12 (fe-ui-review-spec.md F11): the footer holds Reject and Approve and nothing else, and
the fact lives on the Overview footnotes and the Outbox note.

These tests pin what must stay true now that the sentence is gone: the footer carries no Jira
copy and no config variable name, nothing anywhere promises an immediate issue, and the title
format the dispatcher builds is still the one it builds.
"""
import pathlib

from task_dispatch import _build_summary

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = (ROOT / "app.py").read_text()


def dialog_source() -> str:
    start = APP.index("def review_dialog")
    end = APP.index("\ndef ", start + 1)
    return APP[start:end]


def test_the_dialog_footer_carries_no_jira_copy():
    """Reject and Approve only. A config variable name does not belong in front of a reviewer."""
    dialog = dialog_source()
    assert "JIRA_ENABLED" not in dialog
    assert "queues it for Jira" not in dialog
    assert "task_title" not in dialog


def test_the_title_is_the_one_jira_would_get():
    assert (_build_summary("Payment", "invoice_001.pdf", "Apex Cloud Solutions Pty Ltd")
            == "Payment: invoice_001.pdf - Apex Cloud Solutions Pty Ltd")


def test_nothing_promises_an_immediate_issue():
    """The original defect. Approving writes a local task and an outbox row; Jira is another step."""
    assert "Approving creates a Jira issue immediately" not in APP


def test_follow_up_type_per_document():
    """An invoice still has to be paid, a receipt only has to be filed."""
    import app
    assert app.follow_up_for("Invoice") == "Payment"
    assert app.follow_up_for("Receipt") == "File"
    assert app.follow_up_for("Something else") == "Review"
