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


ITEMS = [
    {"description": "Dedicated Cloud Compute", "quantity": 2,
     "unit_price": 450.00, "total": 900.00},
    {"description": "Managed Database Service", "quantity": 1,
     "unit_price": 240.00, "total": 240.00},
]


def invoice(total, **overrides):
    """An extraction with no line items. Complete on its header fields only."""
    return ExtractedInvoice(**{**BASE, "total_amount": total, **overrides})


def complete_invoice(total, **overrides):
    """An extraction with nothing empty, which is what a 1.00 score means."""
    return ExtractedInvoice(**{**BASE, "total_amount": total, "items": ITEMS, **overrides})


# =====================================================================
# The defect itself
# =====================================================================
class TestHallucinatedTotal:
    def test_a_total_that_appears_nowhere_is_not_validated(self, gate):
        """The regression this whole change exists for."""
        score, status = gate.evaluate(invoice(999999.99), DOCUMENT)
        assert status == "NeedsReview"
        assert score < 0.80

    def test_a_complete_extraction_of_the_true_total_scores_one(self, gate):
        """1.00 means nothing is empty. Anything missing has to cost something."""
        score, status = gate.evaluate(complete_invoice(1500.00), DOCUMENT)
        assert status == "Validated"
        assert score == 1.00

    def test_the_true_total_still_validates(self, gate):
        """The fix must not simply block everything."""
        score, status = gate.evaluate(invoice(1500.00), DOCUMENT)
        assert status == "Validated"
        # Below 1.00 because this extraction returned no line items.
        assert 0.80 <= score < 1.00

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

        This extraction is complete, its total is verified against the grand-total
        label, and it scores well over the 0.80 threshold. It is still refused, because
        the vendor is a caption rather than a company. A weighting that merely happened
        to land below the threshold would break the moment anyone retuned it.
        """
        detail = gate.explain(
            complete_invoice(1500.00, vendor_name="Vendor: Apex Cloud Solutions Pty Ltd"),
            DOCUMENT)
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


# =====================================================================
# Completeness: 1.00 means nothing is empty
# =====================================================================
class TestCompleteness:
    """The gate scored 1.00 on an extraction with no line items on 2026-09-03.

    A perfect score has to mean a perfect extraction, or it means nothing.
    """

    def test_missing_line_items_cost_score(self, gate):
        with_items = gate.explain(complete_invoice(1500.00), DOCUMENT)["score"]
        without = gate.explain(invoice(1500.00), DOCUMENT)["score"]
        assert without < with_items == 1.00

    def test_the_empty_fields_are_named(self, gate):
        assert gate.explain(invoice(1500.00), DOCUMENT)["empty"] == ["items"]

    def test_nothing_empty_means_an_empty_list(self, gate):
        assert gate.explain(complete_invoice(1500.00), DOCUMENT)["empty"] == []

    def test_a_missing_date_costs_score(self, gate):
        full = gate.explain(complete_invoice(1500.00), DOCUMENT)["score"]
        detail = gate.explain(complete_invoice(1500.00, date=None), DOCUMENT)
        assert detail["score"] < full
        assert "date" in detail["empty"]

    def test_an_unknown_currency_costs_score(self, gate):
        """'Unknown' is the extractor's default, so it is empty however it reads."""
        detail = gate.explain(complete_invoice(1500.00, currency="Unknown"), DOCUMENT)
        assert "currency" in detail["empty"]

    def test_an_incomplete_extraction_can_still_pass(self, gate):
        """Reduced, not blocked. A single-line bill legitimately has no items."""
        detail = gate.explain(invoice(1500.00), DOCUMENT)
        assert detail["status"] == "Validated"
        assert detail["score"] < 1.00
        assert "not extracted" in detail["reason"]


# =====================================================================
# A vendor that is really a field caption
# =====================================================================
class TestVendorLabel:
    """The model returned 'Vendor: Apex Cloud Solutions Pty Ltd' and scored 1.00.

    The substring check passed *because* the caption was copied too: the more of the
    document you copy, the easier it is to satisfy. A lazier extraction scored higher.
    """

    LABELLED = "Vendor: Apex Cloud Solutions Pty Ltd"

    def test_a_labelled_vendor_is_refused(self, gate):
        detail = gate.explain(complete_invoice(1500.00, vendor_name=self.LABELLED), DOCUMENT)
        assert detail["status"] == "NeedsReview"
        assert detail["checks"]["vendor_is_not_a_label"] is False

    def test_the_reason_says_which_value_is_wrong(self, gate):
        reason = gate.explain(
            complete_invoice(1500.00, vendor_name=self.LABELLED), DOCUMENT)["reason"]
        assert "field label" in reason and "Vendor:" in reason

    def test_a_clean_vendor_is_not_penalised(self, gate):
        assert gate.explain(complete_invoice(1500.00), DOCUMENT)["checks"][
            "vendor_is_not_a_label"] is True

    def test_every_known_caption_is_caught(self, gate):
        for label in ConfidenceValidator.VENDOR_LABELS:
            assert ConfidenceValidator._looks_like_a_label(f"{label} Apex Cloud")

    def test_an_unrecognised_caption_is_not_penalised(self, gate):
        """Fails safe: an unknown caption behaves exactly as it did before this check."""
        assert ConfidenceValidator._looks_like_a_label("Trading As: Apex Cloud") is False

    def test_a_vendor_that_is_only_a_caption_is_not_a_vendor(self, gate):
        assert ConfidenceValidator._looks_like_a_label("Vendor:")


# =====================================================================
# Line items that contradict the total
# =====================================================================
class TestReconciliation:

    def test_line_items_exceeding_the_total_are_refused(self, gate):
        """'short'. Neither tax nor shipping explains a total below its own items."""
        detail = gate.explain(complete_invoice(100.00), DOCUMENT)
        assert detail["reconciliation"] == "short"
        assert detail["status"] == "NeedsReview"

    def test_a_total_above_the_line_sum_is_normal(self, gate):
        """'plausible' is what GST and freight look like, and must not be penalised."""
        detail = gate.explain(complete_invoice(1500.00), DOCUMENT)
        assert detail["reconciliation"] == "plausible"
        assert detail["status"] == "Validated"
        assert detail["score"] == 1.00

    def test_no_line_items_reconciles_to_unknown(self, gate):
        assert gate.explain(invoice(1500.00), DOCUMENT)["reconciliation"] == "unknown"


# =====================================================================
# The vendor cleaner, and its visibility to the harness
# =====================================================================
class TestVendorCleaner:
    """Extraction repairs must stay switchable, or fixing a defect deletes its evidence."""

    @pytest.fixture
    def extractor(self):
        from main import DocumentExtractor
        return DocumentExtractor(model_name="llama3.2")

    def test_a_caption_is_stripped(self, extractor):
        assert extractor._clean_vendor_label(
            "Vendor: Apex Cloud Solutions Pty Ltd") == "Apex Cloud Solutions Pty Ltd"

    def test_a_clean_value_is_untouched(self, extractor):
        assert extractor._clean_vendor_label("Apex Cloud") == "Apex Cloud"

    def test_a_caption_with_nothing_after_it_becomes_none(self, extractor):
        assert extractor._clean_vendor_label("Vendor:") is None

    def test_none_survives(self, extractor):
        assert extractor._clean_vendor_label(None) is None

    def test_the_harness_switch_covers_the_cleaner(self, extractor):
        """The 2026-09-03 defect was a repair the switch did not know about.

        If this fails, the harness can report accuracy that our code produced while
        claiming the repairs were disabled.
        """
        sys.path.insert(0, os.path.join(REPO_ROOT, "evaluation"))
        import run_eval

        discovered = [n for n in dir(extractor) if run_eval.FALLBACK_PATTERN.match(n)]
        assert "_clean_vendor_label" in discovered
        assert "_infer_vendor_fallback" in discovered
        assert "_infer_missing_fields_fallback" in discovered

    def test_a_disabled_cleaner_returns_the_value_unchanged(self, extractor):
        """A transformer neutralised to None would wipe the field, not preserve it."""
        sys.path.insert(0, os.path.join(REPO_ROOT, "evaluation"))
        import run_eval

        run_eval.disable_fallbacks(extractor)
        assert extractor._clean_vendor_label("Vendor: Apex") == "Vendor: Apex"
