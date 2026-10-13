"""The extraction fallbacks must stay linear on text a sender controls.

CodeQL flagged `TOTAL_AMOUNT_PATTERN` and `INVOICE_NUMBER_PATTERN` as polynomial ReDoS on PDF
text (codeql-fixes-spec.md). Measured on the original patterns: "Amount Due" followed by 2,000
spaces took 25.6 s, and the time grew eightfold with each doubling. The total cases use 1,500
spaces, which the original pattern takes about 9 s to reject, so they fail without hanging the
suite. The invoice-number pattern was only quadratic, so it needs 20,000 spaces to show.

The date pattern was dismissed in the first triage because spaces satisfy its `{6,25}` group
and match at once. Tabs and newlines do not: 32,000 of them took 9.8 s.
"""
import time

import pytest

from main import DATE_LABEL_PATTERN, INVOICE_NUMBER_PATTERN, TOTAL_AMOUNT_PATTERN

LIMIT_SECONDS = 0.25


@pytest.mark.parametrize("pattern, label, spaces", [
    (TOTAL_AMOUNT_PATTERN, "Amount Due", 1_500),
    (TOTAL_AMOUNT_PATTERN, "Grand Total", 1_500),
    (INVOICE_NUMBER_PATTERN, "Ref #", 20_000),
    (INVOICE_NUMBER_PATTERN, "Invoice No.", 20_000),
], ids=["amount-due", "grand-total", "ref", "invoice-no"])
def test_a_long_run_of_whitespace_after_a_label_is_rejected_quickly(pattern, label, spaces):
    text = label + " " * spaces + "!"
    started = time.perf_counter()
    assert pattern.search(text) is None
    assert time.perf_counter() - started < LIMIT_SECONDS


@pytest.mark.parametrize("whitespace", ["\t", "\n"], ids=["tabs", "newlines"])
def test_the_date_pattern_rejects_a_long_run_of_tabs_or_newlines_quickly(whitespace):
    text = "Date" + whitespace * 32_000 + "!"
    started = time.perf_counter()
    assert DATE_LABEL_PATTERN.search(text) is None
    assert time.perf_counter() - started < LIMIT_SECONDS


@pytest.mark.parametrize("text, expected", [
    ("Amount Due: $1,500.00", "1,500.00"),
    ("Grand Total AUD 2,650.00", "2,650.00"),
    ("Balance Due - 2350.00", "2350.00"),
    ("Total Payable:\n  $ 99.00", "99.00"),
    ("Amount Due £12.50", "12.50"),
    ("Total Amount Due USD1,234.56", "1,234.56"),
    ("Amount Due:AUD 5.00", "5.00"),
    ("Grand Total 75.00", "75.00"),
])
def test_the_total_pattern_still_captures_every_label_format(text, expected):
    assert TOTAL_AMOUNT_PATTERN.search(text).group(1) == expected


@pytest.mark.parametrize("text, expected", [
    ("Invoice Number: INV-2024-001", "INV-2024-001"),
    ("Tax Invoice No. ABC123", "ABC123"),
    ("Ref # X-77/9", "X-77/9"),
    ("Order #  12345", "12345"),
    ("Invoice No.: TI-889", "TI-889"),
    ("Receipt Number - R-0042", "R-0042"),
])
def test_the_invoice_number_pattern_still_captures_every_label_format(text, expected):
    assert INVOICE_NUMBER_PATTERN.search(text).group(1) == expected


@pytest.mark.parametrize("text, expected", [
    ("Invoice Date: 12 March 2026", "12 March 2026"),
    ("Date of Issue - 2026-03-12", "2026-03-12"),
    ("Date 03/12/2026", "03/12/2026"),
    ("Bill Date:\n 12/03/26 Due", "12/03/26 Due"),
    ("Transaction Date:2026.03.12", "2026.03.12"),
    ("Date   2026-01-01", "2026-01-01"),
])
def test_the_date_pattern_still_captures_every_label_format(text, expected):
    assert DATE_LABEL_PATTERN.search(text).group(1) == expected
