"""Fresh invoices change every run, and only two of them can score 1.00.

The score itself is applied later, by ConfidenceValidator. What this file can
guarantee is the layout that validator reads: a Grand Total beside an amount
that matches the line items, or no total label at all.
"""

import os
import random

import pytest
from pypdf import PdfReader

from generate_fresh_invoices import RESERVED_INVOICE_NUMBERS, generate_fresh_invoices


def pdf_text(path: str) -> str:
    reader = PdfReader(path)
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def test_a_run_writes_two_complete_invoices_and_one_that_cannot_score_1(tmp_path):
    written = generate_fresh_invoices(str(tmp_path), rng=random.Random(1))

    pdfs = [name for name in os.listdir(tmp_path) if name.endswith(".pdf")]
    assert len(pdfs) == 3
    assert len(written) == 3

    complete = [row for row in written if row["role"] == "complete"]
    review = [row for row in written if row["role"] == "review"]
    assert len(complete) == 2
    assert len(review) == 1

    numbers = [row["invoice_number"] for row in written]
    assert len(set(numbers)) == 3
    assert RESERVED_INVOICE_NUMBERS.isdisjoint(numbers)

    for row in complete:
        text = pdf_text(row["path"])
        assert "Grand Total" in text
        assert sum(item["total"] for item in row["items"]) == pytest.approx(row["total_amount"])
        assert f"{row['total_amount']:.2f}" in text
        for item in row["items"]:
            assert f"{item['total']:.2f}" in text

    review_text = pdf_text(review[0]["path"])
    assert "total" not in review_text.lower()
    assert "amount due" not in review_text.lower()
    assert "balance due" not in review_text.lower()
    assert f"{review[0]['total_amount']:.2f}" in review_text


def test_a_second_run_uses_different_invoice_numbers(tmp_path):
    first = generate_fresh_invoices(str(tmp_path / "first"), rng=random.Random(1))
    second = generate_fresh_invoices(str(tmp_path / "second"), rng=random.Random(2))

    first_numbers = {row["invoice_number"] for row in first}
    second_numbers = {row["invoice_number"] for row in second}
    assert first_numbers.isdisjoint(second_numbers)
