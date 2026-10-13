"""A total printed on the same line as its label outranks every other amount.

The defect these exist for, found 2026-10-13 on a scanned Sydney Water bill. The bill opens
with a summary table whose labels sit on one line and whose amounts sit on the next:

    Last bill  Payments  Balance  This bill  Total amount due
    $268.47    $268.47   $0.00    $279.40    $279.40

OCR kept that layout faithfully. llama3.2 took the first amount under the label line, the
previous bill's 268.47, and stored it as the total. Further down the same page the bill says
"Total amount due $279.40" on one line, which leaves no doubt.

Nothing caught it. The gate called 268.47 'verified' because it sits within two lines of a
line containing "total", and the regex fallback would have chosen the same number, because
its `\\s*` crosses the line break after "Total amount due".

The fixture is the OCR text of that bill, shortened, with the customer's name, address and
account numbers replaced. The layout, labels and amounts are as Tesseract read them.
"""
import os
import sys
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from main import (  # noqa: E402
    ConfidenceValidator,
    DocumentExtractor,
    ExtractedInvoice,
    stated_total_cents,
)

WATER_BILL = """Sydney

Telephone Payments (See below) General Enquiries 13 20 92
Account Balance 1300 000 000 Faults and Leaks 13 20 90
Website: sydneywater.com.au 24 hours
Last bill Payments Balance This bill Total amount due
$268.47 $268.47 $0.00 $279.40 $279.40
Please pay by
09/09/26
A CUSTOMER
PO BOX 1 1234 567
SYDNEY NSW 2000

Account for strata unit U 1/1 Example St Sydney
Fixed charges - GST free 1 Jul 26 - 30 Sept 26 $
Water service 26.65
Wastewater (sewerage) service 189.88
Usage charges - GST free 23 May 26 - 19 Aug 26
Water 23/05 - 30/06 8 kL at $3.1700 a kL 25.36
01/07 - 19/08 11 kL at $3.4100 a kL See over for details 37.51
Total amount due $279.40

SYDNEY WATER CORPORATION ABN 49 776 225 038 TAX INVOICE Date of issue 19 August 2026
Go to sydneywater.com.au/pay Total amount due

OCR ID RECALLID § CUSTOMER REFERENCE NUMBER $279.40

Page 2
Total water used in 89 days was 19 kilolitres
"""

# The shape the generator produces: label and amount on separate lines. No line carries both,
# so nothing here may change how these documents are read.
GENERATED = """Apex Cloud Solutions Pty Ltd
Invoice Number: INV-2026-001
Description
Qty
Unit Price
Total
Dedicated Cloud Compute
2
USD 450.00
USD 900.00
Grand Total
USD 1500.00
"""

FIELDS = dict(invoice_number="5674", vendor_name="Sydney Water Corporation",
              date="2026-08-19", currency="AUD")


@pytest.fixture
def extractor():
    return DocumentExtractor(model_name="llama3.2")


@pytest.fixture
def gate():
    return ConfidenceValidator(threshold=0.80)


# =====================================================================
# Reading the stated total
# =====================================================================
class TestStatedTotal:
    def test_the_bill_states_one_total(self):
        assert stated_total_cents(WATER_BILL) == [27940]

    def test_a_label_without_an_amount_on_its_line_states_nothing(self):
        """The summary table's header line is a label line with no amount on it."""
        assert stated_total_cents("Last bill This bill Total amount due\n$268.47 $279.40") == []

    def test_generated_invoices_state_nothing_on_one_line(self):
        assert stated_total_cents(GENERATED) == []

    def test_a_subtotal_is_not_the_payable_amount(self):
        text = "Subtotal $100.00\nGST $10.00\nTotal Due $110.00"
        assert stated_total_cents(text) == [11000]

    def test_a_bare_total_counts_only_at_the_start_of_a_line(self):
        """'Total: $X' is a total; 'Line total 50.00' inside a table row is not."""
        assert stated_total_cents("Total: AUD 1,234.50") == [123450]
        assert stated_total_cents("Widget line total 50.00") == []

    def test_a_tax_line_is_not_the_total(self):
        text = "Total excl GST 100.00\r\nTotal GST 10.00\r\nTotal incl GST 110.00"
        assert stated_total_cents(text) == [11000]

    def test_a_quantity_beside_a_total_is_not_money(self):
        assert stated_total_cents("Total water used in 89 days was 19 kilolitres") == []

    def test_the_amount_must_follow_the_label(self):
        assert stated_total_cents("$268.47 Amount due") == []

    def test_a_long_line_is_read_quickly(self):
        """Sender-controlled text, so linear time matters (codeql-fixes-spec.md)."""
        text = "Amount Due " + "1" * 200_000 + " " * 200_000 + "!"
        started = time.perf_counter()
        stated_total_cents(text)
        assert time.perf_counter() - started < 0.5


# =====================================================================
# The extraction repair
# =====================================================================
class TestTotalCleaner:
    def test_the_previous_bill_is_replaced_by_the_stated_total(self, extractor):
        data = ExtractedInvoice(total_amount=268.47, **FIELDS)
        assert extractor._clean_total_amount(data, WATER_BILL).total_amount == 279.40

    def test_a_correct_answer_is_untouched(self, extractor):
        data = ExtractedInvoice(total_amount=279.40, **FIELDS)
        assert extractor._clean_total_amount(data, WATER_BILL).total_amount == 279.40

    def test_documents_without_a_one_line_total_are_untouched(self, extractor):
        data = ExtractedInvoice(total_amount=900.00, **FIELDS)
        assert extractor._clean_total_amount(data, GENERATED).total_amount == 900.00

    def test_two_different_stated_totals_are_left_to_a_person(self, extractor):
        text = "Amount due $100.00\nBalance due $40.00"
        data = ExtractedInvoice(total_amount=60.00, **FIELDS)
        assert extractor._clean_total_amount(data, text).total_amount == 60.00

    def test_a_missing_total_is_filled_from_the_stated_one_not_the_table(self, extractor):
        """The regex alone returns 268.47 here: its \\s* crosses the line break."""
        data = ExtractedInvoice(total_amount=0.0, **FIELDS)
        filled = extractor._infer_missing_fields_fallback(data, WATER_BILL)
        assert filled.total_amount == 279.40

    def test_the_harness_switch_covers_the_cleaner(self, extractor):
        sys.path.insert(0, os.path.join(REPO_ROOT, "evaluation"))
        import run_eval

        assert run_eval.FALLBACK_PATTERN.match("_clean_total_amount")
        data = ExtractedInvoice(total_amount=268.47, **FIELDS)
        disabled = run_eval._neutraliser("_clean_total_amount")
        assert disabled(data, WATER_BILL).total_amount == 268.47


# =====================================================================
# The gate
# =====================================================================
class TestGate:
    def test_the_previous_bill_is_not_verified(self, gate):
        assert gate.verify_amount(268.47, WATER_BILL) != "verified"

    def test_the_stated_total_is_verified(self, gate):
        assert gate.verify_amount(279.40, WATER_BILL) == "verified"

    def test_the_reason_names_the_stated_total(self, gate):
        detail = gate.explain(ExtractedInvoice(total_amount=268.47, **FIELDS), WATER_BILL)
        assert detail["status"] == "NeedsReview"
        assert "279.40" in detail["reason"]

    def test_generated_invoices_are_gated_as_before(self, gate):
        assert gate.verify_amount(1500.00, GENERATED) == "verified"
        assert gate.verify_amount(999999.99, GENERATED) == "absent"
