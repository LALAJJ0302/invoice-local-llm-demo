"""Each destination names itself. Reported by JJ on 2026-10-07: the heading read "Invoice approvals"
on every page, so the only sign of the current page was the sidebar.

The product name stays, small, above the heading, and the sign-in screens still use it as their
title (test_app_tabs.py covers those), so this asserts only the dashboard.
"""
import pathlib

import pytest
from streamlit.testing.v1 import AppTest

from signed_in import sign_in

APP = str(pathlib.Path(__file__).resolve().parents[1] / "app.py")

LABELS = {"overview": "Overview", "awaiting": "Awaiting approval",
          "auto": "Approved by the system", "outbox": "Outbox", "history": "History",
          "documents": "Documents", "vendors": "Vendors", "runs": "Pipeline runs"}


@pytest.mark.parametrize("view,label", LABELS.items())
def test_the_heading_names_the_page(view, label):
    at = sign_in(AppTest.from_file(APP, default_timeout=120)).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    at.sidebar.radio[0].set_value(label if label in at.sidebar.radio[0].options else
                                  next(o for o in at.sidebar.radio[0].options
                                       if o.startswith(label))).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert at.title[0].value == label
    rendered = " ".join(m.value for m in at.markdown)
    assert "Invoice approvals" in rendered, "the product name should still appear above the page"


def test_row_actions_cannot_wrap_their_labels():
    """The one-letter-per-line collapse at about 900px came from squeezing nested columns. The
    rule that prevents it is CSS, so the test is that the rule is still there."""
    source = pathlib.Path(APP).read_text(encoding="utf-8")
    assert "white-space:nowrap" in source and "min-width:92px" in source
