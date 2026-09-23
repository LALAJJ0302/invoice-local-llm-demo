"""The document-side row check, measured against the three real files."""
import pathlib

import pytest

from line_item_check import contradiction, rows_in

ROOT = pathlib.Path(__file__).resolve().parent.parent
pypdf = pytest.importorskip("pypdf")

CASES = [
    ("sample_invoice_1_INV-2026-001.pdf", 3, 1500.00),
    ("sample_invoice_2_INV-2026-002.pdf", 2, 2650.00),
    ("sample_invoice_3_INV-2026-003.pdf", 2, 2350.00),
]


def text_of(name):
    path = ROOT / "archive" / name
    if not path.exists():
        pytest.skip(f"{name} is not in archive/")
    reader = pypdf.PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


@pytest.mark.parametrize("name,expected_rows,expected_sum", CASES)
def test_rows_and_sums_match_the_documents(name, expected_rows, expected_sum):
    rows = rows_in(text_of(name))
    assert len(rows) == expected_rows
    assert sum(r.total_value for r in rows) == pytest.approx(expected_sum, abs=0.005)


def test_the_sum_equals_the_total_the_model_read():
    """The fact the dialog is built on.

    Every one of the three documents sums its own rows to the total the pipeline stored. That
    is what makes "the model stored none of them" undeniable rather than merely odd.
    """
    for name, _, total in CASES:
        found = contradiction(text_of(name), stored_count=0, stored_total=total)
        assert found is not None
        assert found["matches_stored_total"] is True


def test_silent_when_the_model_got_them():
    """A document whose rows were read correctly must produce no warning at all."""
    assert contradiction(text_of(CASES[0][0]), stored_count=3, stored_total=1500.00) is None
    assert contradiction(text_of(CASES[0][0]), stored_count=9, stored_total=1500.00) is None


def test_silent_on_a_document_with_no_rows():
    assert rows_in("TAX INVOICE\nGrand Total\nUSD 90.00") == []
    assert contradiction("", 0, 90.0) is None


def test_a_bare_amount_is_not_a_row():
    """pypdf puts each cell on its own line, so amounts alone are not evidence of a row."""
    assert rows_in("USD 450.00\nUSD 900.00\nUSD 120.00\nUSD 360.00") == []


def test_the_dialog_draws_the_contradiction_rather_than_only_exposing_it():
    """FE-11. The two facts used to sit side by side with nothing connecting them.

    Asserted against the rendered dialog rather than the source, because the sentence is built
    from the document and the stored row count at render time and could silently stop appearing
    if either input changed shape.
    """
    import re

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    review = next(b for b in at.button if b.key and b.key.startswith("rev-"))
    review.click().run()
    assert not at.exception, [str(e.value) for e in at.exception]

    rendered = " ".join(m.value for m in at.markdown if m.value)
    assert "The document shows 3" in rendered, "the count of rows found is not on screen"
    assert "The model stored none of them" in rendered
    assert "the exact total the model did read" in rendered, (
        "the strongest version of the sentence is available and not being used")
    assert re.search(r"sum of the rows", rendered), "the arithmetic is not shown"
    # The class name appears once more than the rows do: the stylesheet that defines it is
    # rendered into the same markdown stream. Count the markup, not the mention.
    assert rendered.count("class='doc-missed'") == 3, (
        "the rows are not marked in the document panel")
