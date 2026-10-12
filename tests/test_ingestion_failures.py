"""A scanned PDF is quarantined once. A temporary failure is retried, then stopped.

Nothing here calls Ollama. Extraction is either refused or replaced with a fixed invoice.
"""

import email_listener
import main
from main import ExtractedInvoice, InvoiceItem
from storage import StorageManager, connect


def write_textless_pdf(path):
    """A one-page PDF with a drawn box and no text operators."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    pdf = canvas.Canvas(str(path), pagesize=A4)
    pdf.rect(72, 400, 200, 80, stroke=1, fill=0)
    pdf.showPage()
    pdf.save()


def write_text_pdf(path):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    pdf = canvas.Canvas(str(path), pagesize=A4)
    pdf.drawString(72, 800, "Invoice INV-2026-9001")
    pdf.drawString(72, 780, "Vendor: Harbour Utilities")
    pdf.drawString(72, 760, "Grand Total AUD 10.00")
    pdf.showPage()
    pdf.save()


def sample_invoice():
    return ExtractedInvoice(
        invoice_number="INV-2026-9001",
        vendor_name="Harbour Utilities",
        date="2026-10-01",
        total_amount=10.0,
        currency="AUD",
        items=[InvoiceItem(description="Usage", quantity=1, unit_price=10.0, total=10.0)],
    )


def refuse_model(self, *args, **kwargs):
    raise AssertionError("the model was called")


def stub_ocr(monkeypatch, text="", calls=None):
    """Replace page-image OCR so the suite does not need the Tesseract program."""

    def ocr(self, pdf_path):
        if calls is not None:
            calls.append(pdf_path)
        return text

    monkeypatch.setattr(main.DocumentExtractor, "ocr_pdf", ocr)


def stub_models(monkeypatch, extract):
    monkeypatch.setattr(main.DocumentExtractor, "extract_invoice_data", extract)
    monkeypatch.setattr(
        main.DocumentIntelligenceRunner, "analyse", lambda self, file_name, raw_text: None
    )
    monkeypatch.setattr(main.ollama, "chat", refuse_model)


def run_polls(inbox, process, cycles, monkeypatch):
    """Drive poll_inbox inline for `cycles` sleeps, then stop."""
    seen = {"n": 0}

    def sleep_fn(seconds):
        seen["n"] += 1
        if seen["n"] >= cycles:
            raise KeyboardInterrupt

    def inline(process_fn, batch, active):
        email_listener.finish_batch(batch, process_fn, active)

    monkeypatch.setattr(email_listener, "download_attachments", lambda **kwargs: None)
    email_listener.poll_inbox(
        inbox_dir=inbox,
        interval=10,
        sleep_fn=sleep_fn,
        process_fn=process,
        spawn_fn=inline,
    )
    return seen["n"]


def orchestrator_for(batch, archive, db):
    return main.WorkflowOrchestrator(
        inbox_dir=str(batch),
        archive_dir=str(archive),
        db_path=str(db),
    )


def test_image_only_pdf_is_quarantined_and_not_claimed_again(tmp_path, monkeypatch, capsys):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    archive = tmp_path / "archive"
    db = tmp_path / "workflow.db"
    write_textless_pdf(inbox / "scan.pdf")
    stub_ocr(monkeypatch, text="")
    stub_models(monkeypatch, refuse_model)
    calls = {"n": 0}

    def process(batch):
        calls["n"] += 1
        return orchestrator_for(batch, archive, db).run()

    run_polls(inbox, process, cycles=2, monkeypatch=monkeypatch)

    quarantined = inbox / ".failed" / "needs_ocr" / "scan.pdf"
    assert quarantined.is_file()
    assert not (inbox / "scan.pdf").exists()
    assert calls["n"] == 1
    assert email_listener.claim_inbox(inbox) is None
    with connect(str(db)) as conn:
        rows = list(conn.execute("SELECT * FROM ingestion_failures"))
        invoices = conn.execute("SELECT COUNT(*) c FROM invoices").fetchone()["c"]
        runs = conn.execute("SELECT COUNT(*) c FROM processing_runs").fetchone()["c"]
    assert len(rows) == 1
    assert rows[0]["status"] == "needs_ocr"
    assert rows[0]["attempt_count"] == 1
    assert rows[0]["reason"] == "PDF has no text layer"
    assert rows[0]["resolved_at"] is None
    assert invoices == 0
    assert runs == 1
    assert "[Needs OCR] Moved scan.pdf to inbox/.failed/needs_ocr/" in capsys.readouterr().out


def test_a_text_pdf_beside_a_scan_is_still_stored(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    archive = tmp_path / "archive"
    db = tmp_path / "workflow.db"
    write_textless_pdf(inbox / "scan.pdf")
    write_text_pdf(inbox / "text.pdf")
    seen = []
    ocr_calls = []
    stub_ocr(monkeypatch, text="", calls=ocr_calls)

    def extract(self, raw_text, file_name, use_rag=None):
        seen.append(file_name)
        return sample_invoice()

    stub_models(monkeypatch, extract)

    def process(batch):
        return orchestrator_for(batch, archive, db).run()

    run_polls(inbox, process, cycles=1, monkeypatch=monkeypatch)

    assert seen == ["text.pdf"]
    assert len(ocr_calls) == 1
    assert (archive / "text.pdf").is_file()
    assert (inbox / ".failed" / "needs_ocr" / "scan.pdf").is_file()
    with connect(str(db)) as conn:
        invoices = conn.execute("SELECT file_name FROM invoices").fetchall()
        failures = conn.execute(
            "SELECT file_name, status FROM ingestion_failures"
        ).fetchall()
    assert [row["file_name"] for row in invoices] == ["text.pdf"]
    assert [(row["file_name"], row["status"]) for row in failures] == [("scan.pdf", "needs_ocr")]


def test_text_pdf_is_stored_without_a_failure_row(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    archive = tmp_path / "archive"
    db = tmp_path / "workflow.db"
    write_text_pdf(inbox / "text.pdf")
    ocr_calls = []
    stub_ocr(monkeypatch, text="should not be read", calls=ocr_calls)

    def extract(self, raw_text, file_name, use_rag=None):
        return sample_invoice()

    stub_models(monkeypatch, extract)

    outcomes = orchestrator_for(inbox, archive, db).run()
    email_listener.dispose_outcomes(inbox, outcomes)

    assert ocr_calls == []
    assert [outcome.status for outcome in outcomes] == ["stored"]
    assert (archive / "text.pdf").is_file()
    assert not (inbox / "text.pdf").exists()
    assert not (inbox / ".failed").exists()
    with connect(str(db)) as conn:
        assert conn.execute("SELECT COUNT(*) c FROM invoices").fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) c FROM ingestion_failures").fetchone()["c"] == 0


def test_ocr_text_is_stored_on_the_normal_path(tmp_path, monkeypatch, capsys):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    archive = tmp_path / "archive"
    db = tmp_path / "workflow.db"
    write_textless_pdf(inbox / "scan.pdf")
    ocr_text = "Invoice INV-2026-9001\nVendor: Harbour Utilities\nGrand Total AUD 10.00"
    stub_ocr(monkeypatch, text=ocr_text)

    def extract(self, raw_text, file_name, use_rag=None):
        assert "INV-2026-9001" in raw_text
        return sample_invoice()

    stub_models(monkeypatch, extract)

    outcomes = orchestrator_for(inbox, archive, db).run()
    email_listener.dispose_outcomes(inbox, outcomes)

    assert [outcome.status for outcome in outcomes] == ["stored"]
    assert (archive / "scan.pdf").is_file()
    assert not (inbox / ".failed").exists()
    with connect(str(db)) as conn:
        assert conn.execute("SELECT COUNT(*) c FROM invoices").fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) c FROM ingestion_failures").fetchone()["c"] == 0
    assert f"[OCR] Read {len(ocr_text)} characters from scan.pdf" in capsys.readouterr().out


def test_missing_tesseract_is_retried(tmp_path, monkeypatch):
    import pytesseract

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    archive = tmp_path / "archive"
    db = tmp_path / "workflow.db"
    write_textless_pdf(inbox / "scan.pdf")

    def ocr(self, pdf_path):
        raise pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(main.DocumentExtractor, "ocr_pdf", ocr)
    stub_models(monkeypatch, refuse_model)

    outcomes = orchestrator_for(inbox, archive, db).run()
    email_listener.dispose_outcomes(inbox, outcomes)

    assert [outcome.status for outcome in outcomes] == ["retryable_failure"]
    assert outcomes[0].attempt_count == 1
    assert (inbox / "scan.pdf").is_file()
    assert not (inbox / ".failed" / "needs_ocr").exists()
    with connect(str(db)) as conn:
        row = conn.execute("SELECT status, attempt_count FROM ingestion_failures").fetchone()
    assert row["status"] == "retryable_failure"
    assert row["attempt_count"] == 1


def test_temporary_failure_returns_then_quarantines_at_three(tmp_path, monkeypatch, capsys):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    archive = tmp_path / "archive"
    db = tmp_path / "workflow.db"
    write_text_pdf(inbox / "text.pdf")

    def extract(self, raw_text, file_name, use_rag=None):
        return None

    monkeypatch.setattr(main.DocumentExtractor, "extract_invoice_data", extract)
    monkeypatch.setattr(
        main.DocumentIntelligenceRunner, "analyse", lambda self, file_name, raw_text: None
    )
    monkeypatch.setattr(main.ollama, "chat", refuse_model)
    calls = {"n": 0}

    def process(batch):
        calls["n"] += 1
        return orchestrator_for(batch, archive, db).run()

    run_polls(inbox, process, cycles=4, monkeypatch=monkeypatch)

    assert calls["n"] == 3
    assert (inbox / ".failed" / "retry_exhausted" / "text.pdf").is_file()
    assert not (inbox / "text.pdf").exists()
    assert email_listener.claim_inbox(inbox) is None
    with connect(str(db)) as conn:
        row = conn.execute("SELECT * FROM ingestion_failures").fetchone()
        invoices = conn.execute("SELECT COUNT(*) c FROM invoices").fetchone()["c"]
        runs = conn.execute("SELECT COUNT(*) c FROM processing_runs").fetchone()["c"]
    assert row["status"] == "retry_exhausted"
    assert row["attempt_count"] == 3
    assert row["resolved_at"] is None
    assert invoices == 0
    assert runs == 3
    output = capsys.readouterr().out
    assert "[Retry] text.pdf attempt 1/3, returning to inbox" in output
    assert "[Retry exhausted] Moved text.pdf to inbox/.failed/retry_exhausted/" in output


def test_the_same_file_hash_updates_one_failure_row(tmp_path):
    store = StorageManager(str(tmp_path / "workflow.db"))
    digest = "a" * 64

    first = store.record_ingestion_failure("scan.pdf", digest, "needs_ocr", "PDF has no text layer")
    second = store.record_ingestion_failure(
        "scan-again.pdf", digest, "needs_ocr", "PDF has no text layer"
    )

    assert second["failure_id"] == first["failure_id"]
    assert second["attempt_count"] == 2
    assert second["status"] == "needs_ocr"
    assert second["first_seen_at"] == first["first_seen_at"]
    assert second["file_name"] == "scan-again.pdf"
    assert store.resolve_ingestion_failure(digest) == 1

    with connect(store.db_path) as conn:
        resolved = conn.execute(
            "SELECT resolved_at FROM ingestion_failures WHERE source_sha256 = ?",
            (digest,),
        ).fetchone()
        count = conn.execute("SELECT COUNT(*) c FROM ingestion_failures").fetchone()["c"]
    assert resolved["resolved_at"] is not None
    assert count == 1

    reopened = store.record_ingestion_failure(
        "scan.pdf", digest, "retryable_failure", "AI extraction failed"
    )
    assert reopened["failure_id"] == first["failure_id"]
    assert reopened["attempt_count"] == 3
    assert reopened["status"] == "retry_exhausted"
    assert reopened["resolved_at"] is None
    with connect(store.db_path) as conn:
        row = conn.execute("SELECT status, resolved_at FROM ingestion_failures").fetchone()
        count = conn.execute("SELECT COUNT(*) c FROM ingestion_failures").fetchone()["c"]
    assert row["status"] == "retry_exhausted"
    assert row["resolved_at"] is None
    assert count == 1
