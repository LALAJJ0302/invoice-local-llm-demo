"""Reading scanned PDFs: every shape of page image, quickly, and in page order.

The tests that read text call the real Tesseract program and are skipped when it is not
installed. The rest replace pytesseract and run anywhere.
"""

import os
import shutil
import threading

import pypdfium2 as pdfium
import pytest

import main
import scan_fixtures as scans


def tesseract_installed():
    if os.environ.get("TESSERACT_CMD") or shutil.which("tesseract"):
        return True
    windows = os.path.join(
        os.environ.get("ProgramFiles", r"C:\Program Files"), "Tesseract-OCR", "tesseract.exe"
    )
    return os.path.isfile(windows)


needs_tesseract = pytest.mark.skipif(
    not tesseract_installed(), reason="the Tesseract program is not installed"
)


def corner_pixel(pdf_path, strip):
    """The colour at a blank corner of page 1, after the background step if `strip`."""
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        page = pdf[0]
        if strip:
            main.strip_scan_background(page)
        image = page.render(scale=50 / 72).to_pil().convert("RGB")
        return image.getpixel((5, 5))
    finally:
        pdf.close()


WHITE = (255, 255, 255)


# --------------------------------------------------------------------------------------
# The coloured background of a layered scan is dropped. Nothing else is.
# --------------------------------------------------------------------------------------


def test_a_layered_scan_loses_its_coloured_background(tmp_path):
    path = scans.layered_scan(tmp_path / "layered.pdf")

    assert corner_pixel(path, strip=False) != WHITE
    assert corner_pixel(path, strip=True) == WHITE


@pytest.mark.parametrize("kind", ["jpeg", "grey", "bilevel"])
def test_a_one_picture_scan_is_left_alone(tmp_path, kind):
    path = scans.plain_scan(tmp_path / f"{kind}.pdf", kind=kind)

    assert corner_pixel(path, strip=True) == corner_pixel(path, strip=False)


def test_a_small_stamp_does_not_make_a_photo_a_background(tmp_path):
    # The stamp is 1-bit like a text mask, but the text is in the photo underneath.
    path = scans.stamped_scan(tmp_path / "stamped.pdf")

    assert corner_pixel(path, strip=True) == corner_pixel(path, strip=False) != WHITE


def test_the_function_reports_whether_it_changed_the_page(tmp_path):
    layered = scans.layered_scan(tmp_path / "layered.pdf")
    plain = scans.plain_scan(tmp_path / "plain.pdf")

    results = []
    for path in (layered, plain):
        pdf = pdfium.PdfDocument(str(path))
        try:
            results.append(main.strip_scan_background(pdf[0]))
        finally:
            pdf.close()

    assert results == [True, False]


# --------------------------------------------------------------------------------------
# Every shape of scan is read, total included. Real Tesseract.
# --------------------------------------------------------------------------------------


SHAPES = {
    "colour photo": lambda p: scans.plain_scan(p, kind="jpeg"),
    "grey photo": lambda p: scans.plain_scan(p, kind="grey"),
    "black and white": lambda p: scans.plain_scan(p, kind="bilevel"),
    "rotated page": lambda p: scans.plain_scan(p, kind="jpeg", rotate=90),
    "layered": lambda p: scans.layered_scan(p),
    "photo with a stamp": lambda p: scans.stamped_scan(p),
    # A layered file whose text is in the background and whose mask is empty. Dropping
    # the background finds nothing, so the page has to be read again in full.
    "layered with the text in the background": lambda p: scans.layered_scan(
        p, background_text=scans.INVOICE_LINES
    ),
}


@needs_tesseract
@pytest.mark.parametrize("shape", list(SHAPES))
def test_every_shape_of_scan_is_read_with_its_total(tmp_path, shape):
    path = SHAPES[shape](tmp_path / "scan.pdf")

    text = main.DocumentExtractor().ocr_pdf(str(path))

    assert "$279.40" in text
    assert "INV-2026-4412" in text


@needs_tesseract
def test_pages_come_back_in_page_order(tmp_path):
    names = ["ALPHA", "BRAVO", "CHARLIE", "DELTA"]
    pages = [
        {"layers": [("jpeg", scans.text_image([f"PAGE {name}"], font_size=24), None)]}
        for name in names
    ]
    path = scans.write_scan_pdf(tmp_path / "four.pdf", pages)

    text = main.DocumentExtractor().ocr_pdf(str(path))

    positions = [text.find(f"PAGE {name}") for name in names]
    assert -1 not in positions, text
    assert positions == sorted(positions)


# --------------------------------------------------------------------------------------
# Pages are read at the same time, each by a single-threaded Tesseract.
# --------------------------------------------------------------------------------------


def three_page_scan(tmp_path):
    image = scans.text_image(["x"])
    pages = [{"layers": [("jpeg", image, None)]} for _ in range(3)]
    return scans.write_scan_pdf(tmp_path / "three.pdf", pages)


@pytest.fixture
def fake_tesseract(monkeypatch):
    """Replace the call into Tesseract. Records the environment each call saw."""
    import pytesseract

    seen = {"omp": [], "barrier": None}

    def image_to_string(image, lang=None, config=""):
        seen["omp"].append(os.environ.get("OMP_THREAD_LIMIT"))
        if seen["barrier"] is not None:
            # Waits for every page to arrive. Reading one page after another never
            # gets there and fails with BrokenBarrierError.
            seen["barrier"].wait(timeout=5)
        return "page text"

    monkeypatch.setattr(pytesseract, "image_to_string", image_to_string)
    monkeypatch.setattr(pytesseract.pytesseract, "tesseract_cmd", "tesseract")
    monkeypatch.setenv("TESSERACT_CMD", "tesseract")
    return seen


def test_pages_are_read_at_the_same_time(tmp_path, monkeypatch, fake_tesseract):
    monkeypatch.setattr(os, "cpu_count", lambda: 4)
    fake_tesseract["barrier"] = threading.Barrier(3)

    text = main.DocumentExtractor().ocr_pdf(str(three_page_scan(tmp_path)))

    assert text.count("page text") == 3


def test_each_tesseract_runs_on_one_thread(tmp_path, monkeypatch, fake_tesseract):
    monkeypatch.delenv("OMP_THREAD_LIMIT", raising=False)

    main.DocumentExtractor().ocr_pdf(str(three_page_scan(tmp_path)))

    assert fake_tesseract["omp"] == ["1", "1", "1"]


def test_a_thread_limit_already_set_is_kept(tmp_path, monkeypatch, fake_tesseract):
    monkeypatch.setenv("OMP_THREAD_LIMIT", "4")

    main.DocumentExtractor().ocr_pdf(str(three_page_scan(tmp_path)))

    assert fake_tesseract["omp"] == ["4", "4", "4"]
