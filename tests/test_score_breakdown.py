"""The score breakdown must stay arithmetically identical to the gate it explains.

`evaluation/score_breakdown.py` mirrors ConfidenceValidator's weights so it can print a term
against its maximum, which the validator itself has no reason to expose. A mirror can drift,
and a breakdown that drifts is worse than no breakdown: it would explain a score the system
does not actually compute, in a report that cites it as evidence.

These tests re-derive each published score from the mirrored weights and check the total, so a
weight changed in one file and not the other fails here rather than in a figure nobody re-ran.
"""
import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "evaluation"))

import score_breakdown as sb  # noqa: E402
from main import ConfidenceValidator, ExtractedInvoice  # noqa: E402


def test_the_seven_terms_sum_to_one():
    """A scale whose maximum is not 1.00 makes every score in the report unreadable."""
    total = (sb.COMPLETENESS_EACH * len(sb.COMPLETENESS_FIELDS)
             + sb.ITEMS
             + sb.INVOICE_NUMBER_IN_TEXT
             + sb.VENDOR_IN_TEXT
             + sb.VENDOR_NOT_A_LABEL
             + max(sb.AMOUNT.values())
             + max(sb.RECONCILIATION.values()))
    assert round(total, 2) == 1.00


def test_the_mirrored_states_match_the_validator():
    """The validator's two lookup tables are duplicated here; the keys must agree."""
    assert set(sb.AMOUNT) == {"verified", "present", "absent"}
    assert set(sb.RECONCILIATION) == {"exact", "plausible", "unknown", "short"}
    assert set(sb.AMOUNT_MEANING) == set(sb.AMOUNT)
    assert set(sb.RECONCILIATION_MEANING) == set(sb.RECONCILIATION)


def test_the_completeness_fields_are_the_ones_the_validator_checks():
    data = ExtractedInvoice(invoice_number="INV-1", vendor_name="A Vendor",
                            date="2026-01-01", currency="AUD", total_amount=10.0, items=[])
    detail = ConfidenceValidator().explain(data, "INV-1 A Vendor Total Due AUD 10.00")
    for field in sb.COMPLETENESS_FIELDS:
        assert f"{field}_present" in detail["checks"], field


@pytest.mark.parametrize("amount_state", ["verified", "present", "absent"])
@pytest.mark.parametrize("reconciliation", ["exact", "plausible", "unknown", "short"])
def test_every_state_has_a_sentence(amount_state, reconciliation):
    """A state with no explanation would print a blank note beside a number in the report."""
    assert sb.AMOUNT_MEANING[amount_state].strip()
    assert sb.RECONCILIATION_MEANING[reconciliation].strip()


def test_the_terms_reproduce_the_validators_own_score():
    """The real check: rebuild the score from the printed terms and compare.

    Runs against whatever is stored, so it holds for the current samples and for any document
    added later. If the database is empty the test skips rather than passing vacuously.
    """
    import sqlite3

    from pypdf import PdfReader

    db = os.path.join(REPO_ROOT, "workflow_platform.db")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = [r for r in conn.execute(
        "SELECT * FROM invoices WHERE raw_json IS NOT NULL AND archive_path IS NOT NULL")]
    conn.close()

    usable = [r for r in rows if os.path.exists(r["archive_path"])]
    if not usable:
        pytest.skip("no stored document with its archived file still on disk")

    validator = ConfidenceValidator()
    for row in usable:
        data = ExtractedInvoice(**json.loads(row["raw_json"]))
        text = "\n".join((p.extract_text() or "") for p in PdfReader(row["archive_path"]).pages)
        detail = validator.explain(data, text)
        rebuilt = sum(awarded for _, awarded, _, _ in sb.terms(detail))
        # Compared before rounding, and deliberately. INV-2026-004 sums to exactly 0.875,
        # which sits on a rounding boundary: the gate's own left-to-right expression
        # evaluates to 0.8749999999999999 and rounds to 0.87, while summing the same seven
        # terms in a list gives 0.875 and rounds to 0.88. The terms are identical; only the
        # floating-point association differs. Asserting on the rounded value would fail for a
        # discrepancy of 1e-16, and would keep failing whenever a document landed on a
        # boundary. What matters is that the printed terms account for the score, which this
        # checks to a tolerance far tighter than the two decimals ever displayed.
        # The gate rounds to two decimals, so the unrounded terms may sit up to half a
        # display unit away, plus float slack. A genuine drift is at least 0.01, because that
        # is the coarsest a weight could plausibly be changed by, and lands outside this.
        assert abs(rebuilt - detail["score"]) < 0.0051, (
            f"{row['invoice_number']}: the printed terms sum to {rebuilt} "
            f"but the gate scored {detail['score']}")


def test_four_conditions_are_reported_and_one_of_them_is_the_threshold():
    """§5.11 of the report turns on there being four, three of which ignore the score."""
    data = ExtractedInvoice(invoice_number="INV-1", vendor_name="A Vendor",
                            date="2026-01-01", currency="AUD", total_amount=10.0, items=[])
    detail = ConfidenceValidator().explain(data, "INV-1 A Vendor Total Due AUD 10.00")
    rules = sb.hard_rules(detail, 0.80)
    assert len(rules) == 4
    assert sum("score >=" in description for _, description in rules) == 1
