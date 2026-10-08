"""The two sentences PR #13 carried onto the approval screen.

One names why the system approved a document: score 1.00 and a verified amount.
The other names a human approval that survived a NeedsReview result, and says
re-running the pipeline does not undo it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402


def test_system_approval_note_names_the_verified_amount():
    assert "verified amount" in app.SYSTEM_APPROVAL_NOTE
    assert "verified amount" in app.SYSTEM_APPROVAL_SECTION
    assert "reads the document itself" in app.SYSTEM_APPROVAL_NOTE


def test_human_approval_note_when_a_person_approved_needs_review():
    note = app.human_approval_note({
        "approval_status": "Approved",
        "validation_status": "NeedsReview",
        "reviewed_at": "2026-09-29 10:00:00",
    })
    assert note == app.HUMAN_APPROVAL_NOTE
    assert "approved by a reviewer" in note
    assert "NeedsReview" in note
    assert "main.py" in note
    assert "Reopen" in note, "the note must point at the control that actually reopens review"


def test_human_approval_note_is_absent_otherwise():
    assert app.human_approval_note({
        "approval_status": "Approved",
        "validation_status": "NeedsReview",
        "reviewed_at": None,
    }) is None
    assert app.human_approval_note({
        "approval_status": "Approved",
        "validation_status": "Validated",
        "reviewed_at": "2026-09-29 10:00:00",
    }) is None
    assert app.human_approval_note({
        "approval_status": "Pending",
        "validation_status": "NeedsReview",
        "reviewed_at": None,
    }) is None
