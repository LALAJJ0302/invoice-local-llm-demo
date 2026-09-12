"""Tests for the extraction error taxonomy.

A taxonomy is only worth having if two errors that look alike land in different classes, so
these tests are mostly about the boundaries: the rule ordering, and the pairs that a careless
implementation collapses. 'None' and an invented company name are both strings the model made
up, and they are not the same failure.

The last group runs the classifier over the real saved results, because the whole point of the
tool is a claim about those files and a green unit test on synthetic input would not check it.
"""

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "evaluation"))

import error_taxonomy as taxonomy  # noqa: E402

INVOICE = ("Apex Cloud Solutions Pty Ltd\nTAX INVOICE\nInvoice No: INV-2026-001\n"
           "Date: 2026-08-10\nCloud hosting      1,500.00\nTotal Due   USD 1,500.00")


# -- one per class -----------------------------------------------------------

def test_null_is_omitted():
    assert taxonomy.classify("date", "2026-08-10", None, INVOICE)[0] == "omitted"


def test_no_value_string_is_placeholder():
    assert taxonomy.classify("currency", "USD", "Unknown", INVOICE)[0] == "placeholder"


def test_zero_total_is_placeholder_not_a_number():
    """0.0 is the Optional schema's default, not an amount anyone billed."""
    assert taxonomy.classify("total_amount", 1500.0, 0.0, INVOICE)[0] == "placeholder"


def test_right_date_wrong_format_is_malformed():
    klass, _ = taxonomy.classify("date", "2026-03-12", "12 March 2026", "Date: 12 March 2026")
    assert klass == "malformed"


def test_caption_plus_value_is_caption():
    klass, _ = taxonomy.classify(
        "vendor_name", "Apex Cloud Solutions Pty Ltd",
        "Vendor: Apex Cloud Solutions Pty Ltd", INVOICE)
    assert klass == "caption"


def test_prefix_of_the_expected_value_is_truncated():
    klass, _ = taxonomy.classify("vendor_name", "Apex Cloud Solutions Pty Ltd",
                                 "Apex Cloud", INVOICE)
    assert klass == "truncated"


def test_real_value_in_the_wrong_field_is_mislocated():
    klass, _ = taxonomy.classify("invoice_number", None, "Table 4",
                                 "Corner Cafe\nTable 4\nTotal  9.50")
    assert klass == "mislocated"


def test_value_absent_from_the_document_is_invented():
    klass, _ = taxonomy.classify("total_amount", None, 2000.0,
                                 "Opening balance 1,200.00\nPayments received 800.00")
    assert klass == "invented"


# -- ordering, which is where a careless implementation goes wrong -----------

def test_none_string_is_placeholder_not_invented():
    """'None' appears in no document, so rule 7 would claim it. Rule 2 must fire first.

    Getting this wrong inflates the most serious class with the model's honest refusals.
    """
    klass, _ = taxonomy.classify("vendor_name", None, "None", INVOICE)
    assert klass == "placeholder"


def test_caption_beats_mislocated():
    """A caption is also text in the document, so rule 6 would claim it too."""
    klass, _ = taxonomy.classify(
        "vendor_name", "Apex Cloud Solutions Pty Ltd",
        "Vendor: Apex Cloud Solutions Pty Ltd", "Vendor: Apex Cloud Solutions Pty Ltd")
    assert klass == "caption"


def test_malformed_beats_mislocated():
    """The correctly-valued, wrongly-formatted date is on the page in that wrong format."""
    klass, _ = taxonomy.classify("date", "2026-03-12", "12 March 2026",
                                 "TAX INVOICE\nDate: 12 March 2026")
    assert klass == "malformed"


# -- the normalising the rules depend on -------------------------------------

def test_iso_ground_truth_is_not_reordered():
    """Regression. dateutil with dayfirst=True reads '2026-03-12' as 3 December.

    Every expected date in ground_truth.json is ISO, so without the ISO branch the taxonomy
    corrupts its own ground truth and calls correctly-extracted dates mislocated.
    """
    assert taxonomy.canonical("date", "2026-03-12") == "2026-03-12"


def test_australian_dates_are_day_first():
    assert taxonomy.canonical("date", "03/04/2026") == "2026-04-03"


def test_comma_grouped_numbers_are_found_in_the_document():
    """2000.0 must match a document that writes it as 2,000.00, or it is called invented."""
    assert taxonomy.appears_in(2000.0, "Amount Due   AUD 2,000.00")


def test_a_number_genuinely_absent_is_not_found():
    assert not taxonomy.appears_in(2000.0, "Opening balance 1,200.00\nPayments 800.00")


def test_unparseable_date_does_not_crash_the_rule():
    assert taxonomy.canonical("date", "whenever") is None


def test_money_from_a_string_with_symbols():
    assert taxonomy.canonical("total_amount", "$1,500.00") == 1500.0


# -- over the real saved results ---------------------------------------------

@pytest.fixture(scope="module")
def arms():
    return {arm["arm"]: arm for arm in taxonomy.classify_arms(taxonomy.load_arms())}


def test_every_error_gets_exactly_one_class(arms):
    for arm in arms.values():
        assert sum(arm["counts"].values()) == arm["total"]
        for error in arm["errors"]:
            assert error["class"] in taxonomy.CLASSES


def test_the_three_not_admitted_values_split_two_ways(arms):
    """The finding this tool exists for.

    sentinel_comparison.py reports `invented: 3` for the required schema. Two of those three
    values are printed on the document and one is not, and the fixes differ.
    """
    required = arms["sentinel: required schema"]
    assert required["counts"]["invented"] == 1
    assert required["counts"]["mislocated"] == 2

    by_document = {(e["document"], e["field"]): e["class"] for e in required["errors"]}
    assert by_document[("no_total", "total_amount")] == "invented"
    assert by_document[("cafe_receipt", "invoice_number")] == "mislocated"
    assert by_document[("sparse_note", "currency")] == "mislocated"


def test_the_optional_schema_fails_by_omitting(arms):
    """The Optional arms cannot invent, because they answer null. That is the trade being made."""
    optional = arms["ORIGINAL prompt + Optional schema"]
    assert optional["counts"]["omitted"] == optional["total"]
    assert optional["counts"]["invented"] == 0


def test_the_2x2_alone_would_populate_one_class(arms):
    """Why the 2x2 is the wrong source for a taxonomy, asserted rather than claimed."""
    cells = [a for name, a in arms.items() if "prompt +" in name]
    populated = {k for cell in cells for k, v in cell["counts"].items() if v}
    assert populated == {"omitted"}


def test_duplicate_arms_are_reported_so_totals_are_not_summed_blind(arms):
    duplicates = taxonomy._duplicate_arms(list(arms.values()))
    pairs = {(later, first) for later, first in duplicates}
    assert ("2026-08-26 baseline, repairs on", "2026-08-26 baseline, repairs off") in pairs
    assert ("sentinel: sentinel schema", "sentinel: required schema") in pairs


def test_caption_and_truncated_are_empty_in_the_saved_results(arms):
    """Reported as zero, not dropped. If a future run populates them, this test should fail
    and the report's claim that they were never observed has to be rewritten."""
    for klass in ("caption", "truncated"):
        assert sum(arm["counts"][klass] for arm in arms.values()) == 0
