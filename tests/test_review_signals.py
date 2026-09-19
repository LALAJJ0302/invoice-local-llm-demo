"""Tests for the sentence an approver reads instead of a score.

This is the only logic on the approval screen, and it is the thing a person acts on, so it is
the thing worth testing. The rest of app.py is layout.
"""

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from review_signals import SIGNALS, has_signal, risk_signal  # noqa: E402


def invoice(**overrides):
    """A document with nothing wrong with it."""
    row = {
        "reconciliation": "exact",
        "validation_status": "Validated",
        "total_source": "model",
        "vendor_source": "model",
    }
    row.update(overrides)
    return row


class TestWhatIsSaid:
    def test_a_clean_document_says_nothing(self):
        assert risk_signal(invoice()) is None
        assert has_signal(invoice()) is False

    @pytest.mark.parametrize("column,value,expected", [
        ("reconciliation", "short", "Total is less than the line items add up to"),
        ("validation_status", "NeedsReview", "Did not pass the validation gate"),
        ("reconciliation", "unknown", "No line items to check the total against"),
        ("total_source", "fallback",
         "Total was recovered by our code, not read from the document"),
        ("vendor_source", "fallback",
         "Vendor name was inferred, not read from the document"),
        ("reconciliation", "plausible",
         "Total exceeds the line items; tax or shipping would explain it"),
    ])
    def test_each_condition_has_its_sentence(self, column, value, expected):
        assert risk_signal(invoice(**{column: value})) == expected


class TestOrdering:
    """A row shows one sentence. Which one it shows is a decision, not an accident."""

    def test_short_beats_everything(self):
        """Tax and shipping can push a total above its line items. Nothing legitimate pushes it
        below, so `short` is the only genuine anomaly and it must never be hidden."""
        row = invoice(reconciliation="short", validation_status="NeedsReview",
                      total_source="fallback", vendor_source="fallback")
        assert risk_signal(row) == "Total is less than the line items add up to"

    def test_failing_the_gate_beats_a_missing_comparison(self):
        row = invoice(validation_status="NeedsReview", reconciliation="unknown")
        assert risk_signal(row) == "Did not pass the validation gate"

    def test_a_missing_comparison_beats_a_repaired_total(self):
        row = invoice(reconciliation="unknown", total_source="fallback")
        assert risk_signal(row) == "No line items to check the total against"

    def test_a_repaired_total_beats_a_repaired_vendor(self):
        """Money is worth more attention than a name."""
        row = invoice(total_source="fallback", vendor_source="fallback")
        assert risk_signal(row) == "Total was recovered by our code, not read from the document"

    def test_plausible_is_last(self):
        """It is a note, not a warning: tax explains it most of the time."""
        row = invoice(reconciliation="plausible", vendor_source="fallback")
        assert risk_signal(row) == "Vendor name was inferred, not read from the document"


class TestRobustness:
    def test_a_missing_column_does_not_raise(self):
        """The dataframe gains and loses columns as the query changes. A screen that crashes
        because one is absent is worse than a screen that says nothing."""
        assert risk_signal({}) is None
        assert risk_signal({"reconciliation": "short"}) == \
            "Total is less than the line items add up to"

    def test_none_values_are_not_matched(self):
        assert risk_signal(invoice(reconciliation=None, validation_status=None,
                                   total_source=None, vendor_source=None)) is None

    def test_an_unknown_value_says_nothing_rather_than_guessing(self):
        assert risk_signal(invoice(reconciliation="something-new")) is None


class TestAgainstTheRealDatabase:
    """The sentences have to match documents that actually exist, not only invented rows."""

    def test_the_pending_document_gets_the_sentence_the_spec_predicts(self):
        from storage import connect
        db = os.path.join(REPO_ROOT, "workflow_platform.db")
        if not os.path.exists(db):
            pytest.skip("no local database")
        with connect(db) as conn:
            row = conn.execute(
                "SELECT reconciliation, validation_status, total_source, vendor_source "
                "FROM invoices WHERE approval_status = 'Pending' LIMIT 1").fetchone()
        if row is None:
            pytest.skip("nothing pending")
        assert risk_signal(dict(row)) == "No line items to check the total against"

    def test_every_signal_names_a_value_the_schema_allows(self):
        """A sentence keyed on a value the CHECK constraint rejects could never fire."""
        import storage
        allowed = {
            "reconciliation": storage.VALID_RECONCILIATIONS,
            "validation_status": storage.VALID_VALIDATION_STATUSES,
            "total_source": storage.VALID_TOTAL_SOURCES,
            "vendor_source": storage.VALID_TOTAL_SOURCES,
        }
        for column, value, _ in SIGNALS:
            assert value in allowed[column], f"{column} can never be {value!r}"


class TestDetailPairsWithSignal:
    """DETAIL is a parallel mapping, and two structures that must agree can drift.

    It is parallel rather than a fourth element in SIGNALS because three modules unpack those
    tuples as triples. The cost of that choice is this test. See fe-overview-spec.md §3.
    """

    def test_every_signal_has_exactly_one_explanation(self):
        from review_signals import DETAIL, SIGNALS
        missing = [message for _, _, message in SIGNALS if message not in DETAIL]
        assert missing == [], f"signals with no explanation: {missing}"

    def test_no_explanation_is_orphaned(self):
        from review_signals import DETAIL, SIGNALS
        known = {message for _, _, message in SIGNALS}
        orphans = [message for message in DETAIL if message not in known]
        assert orphans == [], f"explanations for signals that do not exist: {orphans}"

    def test_detail_follows_the_signal_that_wins(self):
        """First match wins in SIGNALS, so the explanation must follow the same row."""
        from review_signals import DETAIL, risk_detail, risk_signal
        row = {"reconciliation": "short", "validation_status": "NeedsReview"}
        assert risk_signal(row) == "Total is less than the line items add up to"
        assert risk_detail(row) == DETAIL["Total is less than the line items add up to"]

    def test_no_signal_means_no_detail(self):
        from review_signals import risk_detail
        assert risk_detail({"reconciliation": "exact"}) is None

    def test_explanations_read_as_sentences(self):
        """Same rule the signals themselves are held to: no scores, no field names."""
        import re
        from review_signals import DETAIL
        for message, detail in DETAIL.items():
            assert detail[0].isupper(), message
            assert detail.endswith("."), message
            assert not re.search(r"[_{}]|0\.\d", detail), message
