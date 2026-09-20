"""The sidebar's two saved views and its search, which have to be real queries.

`fe-backlog.md` FE-15 says the sidebar carries only what resolves. The version of that rule
worth testing is narrower: a control that does not change what is on screen is the defect
removed in a5864d5, and these are the controls most likely to quietly become that.
"""
import pandas as pd
import pytest

import app


def frame(rows):
    return pd.DataFrame(rows)


NOW = pd.Timestamp.now()


BASE = [
    {"id": 1, "approval_status": "Pending", "reviewed_at": None,
     "system_processed_at": str(NOW), "vendor_name": "Apex Cloud Solutions Pty Ltd",
     "invoice_number": "INV-2026-001", "file_name": "apex.pdf",
     "validation_score": 0.85, "reconciliation": "unknown", "total_amount": 1500.0,
     "total_source": "model", "currency": "USD", "invoice_date": "2026-08-10"},
    {"id": 2, "approval_status": "Approved", "reviewed_at": None,
     "system_processed_at": str(NOW - pd.Timedelta(days=1)), "vendor_name": "NextGen Hardware",
     "invoice_number": "INV-2026-002", "file_name": "nextgen.pdf",
     "validation_score": 1.0, "reconciliation": "exact", "total_amount": 2650.0,
     "total_source": "model", "currency": "AUD", "invoice_date": "2026-08-12"},
    {"id": 3, "approval_status": "Approved", "reviewed_at": None,
     "system_processed_at": str(NOW - pd.Timedelta(days=40)), "vendor_name": "Old Vendor",
     "invoice_number": "INV-2025-900", "file_name": "old.pdf",
     "validation_score": 1.0, "reconciliation": "exact", "total_amount": 100.0,
     "total_source": "model", "currency": "USD", "invoice_date": "2025-01-01"},
]


def test_cleared_this_week_actually_means_this_week():
    """The row this replaces defined the view as every document ever cleared.

    `fe-backlog.md` wrote it as `approval_status = 'Approved' AND reviewed_at IS NULL`, which
    has no week in it. A label promising a week and returning all time is the same
    overstatement as the approve line fixed in 8750a8c.
    """
    views = app.saved_views(frame(BASE))
    cleared = views["cleared"][1]
    assert list(cleared["id"]) == [2], "the 40-day-old row is not from this week"


def test_flagged_is_pending_only_and_comes_from_review_signals():
    views = app.saved_views(frame(BASE))
    flagged = views["flagged"][1]
    assert set(flagged["approval_status"]) <= {"Pending"}
    assert list(flagged["id"]) == [1]


def test_all_documents_changes_nothing():
    rows = frame(BASE)
    assert len(app.saved_views(rows)["all"][1]) == len(rows)


@pytest.mark.parametrize("query,expected", [
    ("Apex", [1]),
    ("apex", [1]),              # a person types lowercase
    ("INV-2026-002", [2]),      # by invoice number
    ("nextgen.pdf", [2]),       # by file name
    ("", [1, 2, 3]),            # an empty search is not a filter
    ("   ", [1, 2, 3]),         # nor is whitespace
    ("zzz-no-such-vendor", []),
])
def test_search_covers_the_three_fields_a_person_would_type(query, expected):
    assert list(app.matches(frame(BASE), query)["id"]) == expected


def test_search_survives_a_null_vendor():
    """`vendor_name` is nullable and a null must not raise or match everything."""
    rows = frame(BASE + [{**BASE[0], "id": 4, "vendor_name": None,
                          "invoice_number": None, "file_name": None}])
    assert list(app.matches(rows, "Apex")["id"]) == [1]


def test_the_empty_state_does_not_claim_finished_work_under_a_filter():
    """`empty_queue` says everything has been decided. Under a filter that is a lie."""
    source = (app.__file__ and open(app.__file__).read()) or ""
    assert "No documents match the current view." in source
