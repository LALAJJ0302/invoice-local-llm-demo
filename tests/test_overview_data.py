"""overview_data.py: what the Overview page says, apart from how it is drawn.

fe-overview-v2-spec.md section 6. Plain frames and rows, no database and no Streamlit.
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import overview_data as od  # noqa: E402


def frame(**rows):
    return pd.DataFrame(rows)


class TestTotalsByCurrency:
    def test_currencies_are_listed_separately_and_never_added(self):
        df = frame(currency=["USD", "USD", "AUD"], total_amount=[1500.0, 2350.0, 2650.0])
        assert od.totals_by_currency(df) == [("USD", 3850.0), ("AUD", 2650.0)]

    def test_a_document_without_a_currency_or_amount_is_left_out(self):
        df = frame(currency=["USD", None, "AUD"], total_amount=[100.0, 5.0, None])
        assert od.totals_by_currency(df) == [("USD", 100.0)]

    def test_an_empty_frame_is_an_empty_list(self):
        assert od.totals_by_currency(pd.DataFrame(columns=["currency", "total_amount"])) == []


def row(**kw):
    base = {"validation_status": "Validated", "reconciliation": "exact", "total_source": "model",
            "vendor_source": "model", "email_id": 1}
    base.update(kw)
    return base


class TestAttentionReasons:
    def test_a_clean_document_has_no_reason(self):
        assert od.attention_reasons(row(), 0) == []

    def test_a_failed_document_leads(self):
        reasons = od.attention_reasons(row(validation_status="Failed"), 0)
        assert reasons[0] == "Processing failed"

    def test_the_gate_concern_is_a_reason(self):
        assert "Did not pass the validation gate" in od.attention_reasons(
            row(validation_status="NeedsReview"), 0)

    def test_missing_line_items_is_a_reason(self):
        assert "No line items to check the total against" in od.attention_reasons(
            row(reconciliation="unknown"), 0)

    def test_the_mild_tax_or_shipping_note_is_not_a_reason(self):
        assert od.attention_reasons(row(reconciliation="plausible"), 0) == []

    def test_no_covering_email_is_a_reason(self):
        assert "No matching email" in od.attention_reasons(row(email_id=None), 0)
        assert "No matching email" in od.attention_reasons(row(email_id=float("nan")), 0)

    def test_waiting_is_a_reason_only_at_the_threshold(self):
        just_under = od.WAITING_TOO_LONG_DAYS - 1
        assert od.attention_reasons(row(), just_under) == []
        assert od.attention_reasons(row(), od.WAITING_TOO_LONG_DAYS) == [
            f"Waiting {od.WAITING_TOO_LONG_DAYS} days"]

    def test_unknown_waiting_time_is_not_a_reason(self):
        assert od.attention_reasons(row(), None) == []


class TestAttentionRows:
    def test_only_documents_with_a_reason_are_listed_longest_waiting_first(self):
        pending = pd.DataFrame([
            {"id": 1, **row(), "total_amount": 10.0},
            {"id": 2, **row(email_id=None), "total_amount": 10.0},
            {"id": 3, **row(email_id=None), "total_amount": 99.0},
        ])
        days = {1: 0, 2: 1, 3: 1}
        found = od.attention_rows(pending, lambda r: days[r["id"]])
        assert [item["row"]["id"] for item in found] == [3, 2]
        assert all(item["reasons"] for item in found)


class TestWorkflowStatus:
    def test_pending_is_awaiting_approval(self):
        assert od.workflow_status({"approval_status": "Pending"}, None) == "Awaiting approval"

    def test_rejected_stays_rejected_whatever_jira_says(self):
        assert od.workflow_status({"approval_status": "Rejected"}, "Sent") == "Rejected"

    def test_approved_by_a_person_or_by_the_system(self):
        assert od.workflow_status(
            {"approval_status": "Approved", "reviewed_at": "2026-10-01"}, None
        ) == "Approved by a person"
        assert od.workflow_status(
            {"approval_status": "Approved", "reviewed_at": None}, None
        ) == "Approved by the system"

    def test_the_jira_state_takes_over_once_there_is_one(self):
        approved = {"approval_status": "Approved", "reviewed_at": None}
        assert od.workflow_status(approved, "Queued") == "Queued for Jira"
        assert od.workflow_status(approved, "Sent") == "Sent to Jira"


class TestJiraStateByInvoice:
    def test_sent_beats_queued_and_withdrawn_rows_do_not_count(self):
        outbox = pd.DataFrame([
            {"invoice_id": 1, "channel": "Jira", "state": "Pending", "withdrawn": False},
            {"invoice_id": 1, "channel": "Jira", "state": "Sent", "withdrawn": False},
            {"invoice_id": 2, "channel": "Jira", "state": "Pending", "withdrawn": True},
            {"invoice_id": 3, "channel": "Teams", "state": "Pending", "withdrawn": False},
            {"invoice_id": 4, "channel": "Jira", "state": "Failed", "withdrawn": False},
        ])
        assert od.jira_state_by_invoice(outbox) == {1: "Sent", 4: "Queued"}

    def test_an_empty_outbox_is_an_empty_dict(self):
        assert od.jira_state_by_invoice(pd.DataFrame()) == {}
