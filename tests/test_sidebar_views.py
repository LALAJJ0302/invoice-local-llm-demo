"""The sidebar, which is now the only navigation on the screen.

The tab bar was removed on 2026-09-20 and the two saved views went with it, because both
returned exactly the rows a destination already showed. What is left to protect is that every
destination is reachable, that the counts beside them agree with what they open, and that the
search still filters.
"""
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from signed_in import sign_in

import app

DESTINATIONS = ["overview", "awaiting", "auto", "outbox", "history",
                "documents", "vendors", "runs"]
# AppTest reports a radio's options as the strings a person sees, not the keys behind them.
LABELS = ["Overview", "Awaiting approval", "Approved by the system", "Outbox", "History",
          "Documents", "Vendors", "Pipeline runs"]

NOW = pd.Timestamp.now()
BASE = [
    {"id": 1, "approval_status": "Pending", "reviewed_at": None,
     "system_processed_at": str(NOW), "vendor_name": "Apex Cloud Solutions Pty Ltd",
     "invoice_number": "INV-2026-001", "file_name": "apex.pdf", "currency": "USD"},
    {"id": 2, "approval_status": "Approved", "reviewed_at": None,
     "system_processed_at": str(NOW - pd.Timedelta(days=1)), "vendor_name": "NextGen Hardware",
     "invoice_number": "INV-2026-002", "file_name": "nextgen.pdf", "currency": "AUD"},
    {"id": 3, "approval_status": "Approved", "reviewed_at": None,
     "system_processed_at": str(NOW - pd.Timedelta(days=40)), "vendor_name": "Old Vendor",
     "invoice_number": "INV-2025-900", "file_name": "old.pdf", "currency": "USD"},
]


def run():
    at = sign_in(AppTest.from_file(str(app.__file__), default_timeout=120)).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def test_the_sidebar_offers_every_destination_in_order():
    """The five states of a document, then the three other things to look at."""
    assert run().sidebar.radio[0].options == LABELS


@pytest.mark.parametrize("destination", DESTINATIONS)
def test_every_destination_renders(destination):
    at = run()
    at.sidebar.radio[0].set_value(destination).run()
    assert not at.exception, [str(e.value) for e in at.exception]


def test_the_counts_beside_a_destination_match_what_it_opens():
    """A number in the navigation that disagrees with the page under it is worse than none."""
    frame = app.load_data()
    pending = frame[frame["approval_status"] == "Pending"]
    auto = frame[(frame["approval_status"] == "Approved") & (frame["reviewed_at"].isna())]
    outbox, history = app.load_outbox(), app.load_history()
    dest = app.destinations(frame, pending, auto, outbox, history)

    assert dest["overview"]["count"] is None, "Overview summarises the others and counts nothing"
    assert dest["awaiting"]["count"] == len(pending)
    assert dest["auto"]["count"] == len(auto)
    assert dest["outbox"]["count"] == len(outbox)
    assert dest["history"]["count"] == len(history)
    assert dest["documents"]["count"] == len(frame)
    assert dest["vendors"]["count"] == frame["vendor_name"].nunique()


def test_no_destination_repeats_another():
    """The reason the tab bar went.

    Two of the sidebar's old rows returned exactly the rows of two tabs, so the screen carried
    two navigation systems over one dataset. Nothing here may collapse into anything else.
    """
    assert len(set(DESTINATIONS)) == len(DESTINATIONS)
    frame = app.load_data()
    pending = frame[frame["approval_status"] == "Pending"]
    auto = frame[(frame["approval_status"] == "Approved") & (frame["reviewed_at"].isna())]
    labels = [d["label"] for d in app.destinations(
        frame, pending, auto, app.load_outbox(), app.load_history()).values()]
    assert len(set(labels)) == len(labels), "two destinations carry the same name"


@pytest.mark.parametrize("query,expected", [
    ("Apex", [1]),
    ("apex", [1]),                 # a person types lowercase
    ("INV-2026-002", [2]),         # by invoice number
    ("nextgen.pdf", [2]),          # by file name
    ("", [1, 2, 3]),               # an empty search is not a filter
    ("   ", [1, 2, 3]),            # nor is whitespace
    ("zzz-no-such-vendor", []),
])
def test_search_covers_the_three_fields_a_person_would_type(query, expected):
    assert list(app.matches(pd.DataFrame(BASE), query)["id"]) == expected


def test_search_survives_a_null_vendor():
    """`vendor_name` is nullable and a null must not raise or match everything."""
    rows = pd.DataFrame(BASE + [{**BASE[0], "id": 4, "vendor_name": None,
                                 "invoice_number": None, "file_name": None}])
    assert list(app.matches(rows, "Apex")["id"]) == [1]


def test_the_empty_state_does_not_claim_finished_work_under_a_filter():
    """`empty_queue` says everything has been decided. Under a search that is a lie."""
    assert "No documents match the current view." in open(app.__file__).read()
