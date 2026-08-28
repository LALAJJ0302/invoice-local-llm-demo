"""Tests for the validation gate's amount verification.

The defect these exist for: before 2026-08-28 the gate never compared the extracted total
against the document. A hallucinated 999,999.99 on a $1,500 invoice scored 1.00 and passed
as Validated, identically to the correct value.

See validation-gate-spec.md.
"""

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from main import ConfidenceValidator, ExtractedInvoice  # noqa: E402


# A document shaped like the ones the generator produces: the grand total sits on the line
# after its label, and a line item happens to carry a different amount.
DOCUMENT = """Apex Cloud Solutions Pty Ltd
Invoice Number: INV-2026-001
Date: 2026-08-10

Description
Qty
Unit Price
Total
Dedicated Cloud Compute
2
USD 450.00
USD 900.00
Managed Database Service
1
USD 240.00
USD 240.00
Grand Total
USD 1500.00
Payment Terms: Net 15 days.
"""

BASE = dict(
    invoice_number="INV-2026-001",
    vendor_name="Apex Cloud Solutions Pty Ltd",
    date="2026-08-10",
    currency="USD",
)


@pytest.fixture
def gate():
    return ConfidenceValidator(threshold=0.80)


def invoice(total):
    return ExtractedInvoice(**BASE, total_amount=total)


# =====================================================================
# The defect itself
# =====================================================================
class TestHallucinatedTotal:
    def test_a_total_that_appears_nowhere_is_not_validated(self, gate):
        """The regression this whole change exists for."""
        score, status = gate.evaluate(invoice(999999.99), DOCUMENT)
        assert status == "NeedsReview"
        assert score < 0.80

    def test_the_true_total_still_validates(self, gate):
        """The fix must not simply block everything."""
        score, status = gate.evaluate(invoice(1500.00), DOCUMENT)
        assert status == "Validated"
        assert score == 1.00

    def test_the_two_no_longer_score_identically(self, gate):
        true_score, _ = gate.evaluate(invoice(1500.00), DOCUMENT)
        fake_score, _ = gate.evaluate(invoice(999999.99), DOCUMENT)
        assert true_score > fake_score


# =====================================================================
# Presence is not enough
# =====================================================================
class TestProximityToALabel:
    def test_a_line_item_amount_is_present_but_not_verified(self, gate):
        """900.00 is in the document, as a line item. Presence alone must not pass it."""
        detail = gate.explain(invoice(900.00), DOCUMENT)
        assert detail["amount_state"] == "present"
        assert detail["status"] == "NeedsReview"

    def test_the_hard_rule_blocks_even_above_the_threshold(self, gate):
        """The reason the status is a rule and not a weighting.

        This document scores 0.88 on the line-item amount, comfortably over the 0.80
        threshold, and must still be refused. A weighting that merely happened to land
        below the threshold would break the moment anyone retuned it.
        """
        detail = gate.explain(invoice(900.00), DOCUMENT)
        assert detail["score"] >= 0.80
        assert detail["status"] == "NeedsReview"

    def test_the_grand_total_is_verified(self, gate):
        assert gate.verify_amount(1500.00, DOCUMENT) == "verified"

    def test_an_absent_amount_is_absent(self, gate):
        assert gate.verify_amount(999999.99, DOCUMENT) == "absent"

    def test_a_missing_amount_is_absent(self, gate):
        assert gate.verify_amount(0.0, DOCUMENT) == "absent"
        assert gate.verify_amount(None, DOCUMENT) == "absent"


# =====================================================================
# Number formatting
# =====================================================================
class TestNumberNormalisation:
    def test_thousands_separators_are_ignored(self, gate):
        """Documents write 1,500.00 while the model returns 1500.0."""
        assert gate.verify_amount(1500.00, "Grand Total\nUSD 1,500.00") == "verified"

    def test_currency_symbols_are_ignored(self, gate):
        assert gate.verify_amount(1500.00, "Amount Due\n$1500.00") == "verified"

    def test_a_near_miss_is_not_a_match(self, gate):
        """1500.01 is a different amount from 1500.00 and must not be accepted."""
        assert gate.verify_amount(1500.01, DOCUMENT) == "absent"

    def test_an_amount_on_the_same_line_as_its_label_is_verified(self, gate):
        assert gate.verify_amount(2650.00, "Grand Total: AUD 2,650.00") == "verified"


# =====================================================================
# The reason carried into the task queue
# =====================================================================
class TestReason:
    def test_an_absent_total_says_so(self, gate):
        assert "does not appear" in gate.explain(invoice(999999.99), DOCUMENT)["reason"]

    def test_a_line_item_total_explains_the_distinction(self, gate):
        reason = gate.explain(invoice(900.00), DOCUMENT)["reason"]
        assert "line item" in reason

    def test_a_passing_document_says_it_awaits_approval(self, gate):
        assert "approval" in gate.explain(invoice(1500.00), DOCUMENT)["reason"].lower()

    def test_no_total_extracted_is_reported_separately(self, gate):
        assert "No total amount" in gate.explain(invoice(0.0), DOCUMENT)["reason"]


# =====================================================================
# The interface run_eval.py depends on
# =====================================================================
class TestInterface:
    def test_evaluate_still_returns_exactly_two_values(self, gate):
        """evaluation/run_eval.py unpacks `score, status = validator.evaluate(...)`."""
        result = gate.evaluate(invoice(1500.00), DOCUMENT)
        assert isinstance(result, tuple) and len(result) == 2

    def test_evaluate_agrees_with_explain(self, gate):
        score, status = gate.evaluate(invoice(900.00), DOCUMENT)
        detail = gate.explain(invoice(900.00), DOCUMENT)
        assert (score, status) == (detail["score"], detail["status"])

    def test_the_threshold_is_configurable(self):
        """Lowering the threshold must not unlock an unverified amount."""
        lenient = ConfidenceValidator(threshold=0.10)
        assert lenient.explain(invoice(900.00), DOCUMENT)["status"] == "NeedsReview"
