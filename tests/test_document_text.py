"""The review dialog shows a scan by reading it the same way the pipeline does.

A text layer is returned as-is. A page with none used to leave the panel blank and
say the pipeline could not read scans, which was no longer true.
"""
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

import app


def write_text_pdf(path):
    pdf = canvas.Canvas(str(path), pagesize=A4)
    pdf.drawString(72, 800, "Invoice Number: INV-2026-9001")
    pdf.drawString(72, 780, "Amount Due: $10.00")
    pdf.save()


def write_textless_pdf(path):
    pdf = canvas.Canvas(str(path), pagesize=A4)
    pdf.rect(72, 400, 200, 80, stroke=1, fill=0)
    pdf.save()


def test_a_text_layer_is_shown_without_loading_the_model_pipeline(tmp_path):
    path = tmp_path / "invoice.pdf"
    write_text_pdf(path)

    text = app.document_text(str(path))
    assert "INV-2026-9001" in text
    assert "10.00" in text


def test_a_scan_is_read_by_the_pipeline_ocr(tmp_path, monkeypatch):
    path = tmp_path / "scan.pdf"
    write_textless_pdf(path)
    seen = {}

    def read(file_path, mtime):
        seen["path"] = file_path
        seen["mtime"] = mtime
        return "Sydney Water\nAmount due 268.47"

    monkeypatch.setattr(app, "_cached_document_text", read)
    assert "268.47" in app.document_text(str(path))
    assert seen["path"] == str(path)
    assert seen["mtime"] == path.stat().st_mtime


def test_a_missing_file_stays_blank(tmp_path):
    assert app.document_text("") == ""
    assert app.document_text(None) == ""
    assert app.document_text(str(tmp_path / "gone.pdf")) == ""
