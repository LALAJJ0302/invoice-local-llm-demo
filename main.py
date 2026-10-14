import argparse
import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
from dateutil import parser as date_parser
from pydantic import BaseModel, Field
from pypdf import PdfReader
import ollama

import email_ai
import rag_retrieval
import retrieval
import storage
import task_dispatch
from storage import StorageManager

# =====================================================================
# 0. Regex Patterns for Deterministic Fallback Field Extraction
# =====================================================================
# Small local LLMs are unreliable at pulling highly patterned, labeled
# fields out of documents. These regexes backfill fields the model
# missed (returned null / 0.0 / "Unknown") using the same "fallback
# only fills gaps" approach as the existing vendor-name heuristic.
INVOICE_NUMBER_PATTERN = re.compile(
    r"(?:Invoice\s*(?:Number|No\.?|#)|Tax\s*Invoice\s*(?:Number|No\.?)|Receipt\s*(?:Number|No\.?)|Order\s*#|Ref\s*#)"
    r"\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9\-\/]{2,})",
    re.IGNORECASE
)
DATE_LABEL_PATTERN = re.compile(
    r"(?:Date\s*of\s*Issue|Invoice\s*Date|Bill\s*Date|Transaction\s*Date|Date)"
    r"\s*[:\-]?\s*([A-Za-z0-9,\/\-\. ]{6,25})",
    re.IGNORECASE
)
TOTAL_AMOUNT_PATTERN = re.compile(
    r"(?:Grand\s*Total|Total\s*Amount\s*Due|Amount\s*Due|Balance\s*Due|Total\s*Payable)"
    r"\s*[:\-]?\s*(?:[A-Z]{3}|\$|€|£)?\s*([\d,]+\.\d{2})",
    re.IGNORECASE
)
CURRENCY_CODE_PATTERN = re.compile(r"\b(AUD|USD|EUR|GBP|CAD|NZD|TWD|JPY|SGD|HKD|CNY|INR)\b", re.IGNORECASE)
INVOICE_CODE_TOKEN_PATTERN = re.compile(r"^[A-Za-z]{2,5}-?\d+", re.IGNORECASE)

# =====================================================================
# 1. Enhanced Data Schema Definitions
# =====================================================================
class InvoiceItem(BaseModel):
    """Schema for individual line items."""
    description: str = Field(description="Description or item name")
    quantity: Optional[float] = Field(default=1.0, description="Quantity")
    unit_price: Optional[float] = Field(default=0.0, description="Unit price")
    total: Optional[float] = Field(default=0.0, description="Line item total amount")

class ExtractedInvoice(BaseModel):
    """Schema for structured invoice output."""
    invoice_number: Optional[str] = Field(default=None, description="Invoice, Tax Invoice, or Receipt number")
    vendor_name: Optional[str] = Field(default=None, description="Vendor, Supplier, Seller, or Billed-From company name")
    date: Optional[str] = Field(default=None, description="Invoice issue date, bill date, or transaction date in YYYY-MM-DD format if possible")
    total_amount: Optional[float] = Field(default=0.0, description="Grand total amount, net payable, or balance due as a numeric float")
    currency: Optional[str] = Field(default="Unknown", description="Detected currency code (e.g. AUD, USD, EUR, TWD) or 'Unknown'")
    items: List[InvoiceItem] = Field(default=[], description="List of line items")

class ProcessedRecord(BaseModel):
    """Workflow processing record structure."""
    file_name: str
    status: str
    confidence_score: float
    extracted_data: ExtractedInvoice
    archive_path: str
    processed_at: str

# =====================================================================
# 2. Document Extraction Engine with Heuristic Fallbacks
# =====================================================================
class DocumentRead:
    """Text taken from a file, or why that file cannot be sent to the model.

    status is None when text is usable. Otherwise it is needs_ocr, unsupported,
    or retryable_failure, which is the same vocabulary WorkflowOrchestrator returns.
    """

    def __init__(self, text: str = "", status: Optional[str] = None, reason: str = "",
                 extraction_source: Optional[str] = None):
        self.text = text
        self.status = status
        self.reason = reason
        # Runtime provenance for evaluation and diagnostics. `embedded` means the PDF's
        # native text layer, `ocr` means page images, and `hybrid` means both were used.
        self.extraction_source = extraction_source


@dataclass
class FileOutcome:
    """What one file did in a pipeline run.

    status is stored, retryable_failure, needs_ocr, or unsupported.
    attempt_count is 0 when the file was stored. A failure count comes from
    ingestion_failures and is how the listener decides whether to try again.
    """

    file_name: str
    status: str
    reason: str
    source_sha256: Optional[str]
    attempt_count: int


def _clipped_area(bounds, width, height):
    left, bottom, right, top = bounds
    return max(0.0, min(right, width) - max(left, 0.0)) * max(
        0.0, min(top, height) - max(bottom, 0.0)
    )


def strip_scan_background(page) -> bool:
    """Drop the coloured background picture from a layered scan. True if anything went.

    Some scanners, and the print houses behind utility bills, write each page as a
    low-resolution colour photo with the text laid on top as sharp 1-bit masks. The
    masks are what OCR should read; the photo behind them only adds tint and speckle.

    A photo is dropped only when it covers nearly the whole page and 1-bit images cover
    at least half of it. One photo per page, a black-and-white scan, or a photo with a
    small 1-bit stamp on it is left exactly as it is, because there the photo is the
    text. `page` is a pypdfium2 page; the change is in memory only, never saved.
    """
    import pypdfium2.raw as pdfium_raw

    width, height = page.get_size()
    page_area = width * height
    if page_area <= 0:
        return False

    masks, photos = [], []
    for obj in page.get_objects(max_depth=1):
        if obj.type != pdfium_raw.FPDF_PAGEOBJ_IMAGE:
            continue
        area = _clipped_area(obj.get_bounds(), width, height)
        if obj.get_metadata().bits_per_pixel == 1:
            masks.append(area)
        elif area >= 0.9 * page_area:
            photos.append(obj)

    if not photos or sum(masks) < 0.5 * page_area:
        return False
    for obj in photos:
        page.remove_obj(obj)
    page.gen_content()
    return True


class DocumentExtractor:
    """Extracts text from PDF and performs structured inference using Ollama."""

    def __init__(
        self,
        model_name: str = "llama3.2",
        use_rag: bool = False,
        rag_limit: int = 1,
        rag_examples_path: str = rag_retrieval.DEFAULT_EXAMPLES_PATH,
    ):
        self.model_name = model_name
        self.use_rag = use_rag
        self.rag_limit = rag_limit
        self.rag_examples_path = rag_examples_path
        self.last_retrieval: list[dict] = []
        self.last_error: Optional[BaseException] = None

    # A scanned page has no text operators. 300 DPI is enough for a utility bill
    # and still cheap on a two-page invoice. 72 is PDF's own point size.
    OCR_DPI = 300

    def extract_text_from_pdf(self, pdf_path: str) -> str:
        """Reads text from all pages of a PDF."""
        try:
            reader = PdfReader(pdf_path)
            full_text = ""
            for page in reader.pages:
                full_text += (page.extract_text() or "") + "\n"
            return full_text.strip()
        except Exception as error:
            print(f"  [Error] Failed to read PDF {pdf_path}: {error}")
            return ""

    @staticmethod
    def _usable_ocr_text(text: str) -> bool:
        """Reject output that is non-empty but too small to be meaningful.

        Tesseract can return a stray character from dust, a stamp, or a page number. Passing
        that to the LLM turns an OCR failure into a plausible-looking extraction failure. The
        deliberately small gate accepts short receipts while rejecting obvious noise.
        """
        text = (text or "").strip()
        characters = re.findall(r"[A-Za-z0-9]", text)
        words = re.findall(r"\b[A-Za-z0-9]{2,}\b", text)
        return len(characters) >= 20 and len(words) >= 4

    def _ocr_pages(self, pdf_path: str, page_indexes=None) -> dict[int, str]:
        """OCR selected zero-based pages and return their text keyed by page number."""
        import pypdfium2 as pdfium
        import pytesseract

        command = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")
        if not command:
            windows = os.path.join(
                os.environ.get("ProgramFiles", r"C:\Program Files"),
                "Tesseract-OCR",
                "tesseract.exe",
            )
            if os.path.isfile(windows):
                command = windows
        if command:
            pytesseract.pytesseract.tesseract_cmd = command

        os.environ.setdefault("OMP_THREAD_LIMIT", "1")

        def read(image):
            return pytesseract.image_to_string(image, lang="eng")

        pdf = pdfium.PdfDocument(pdf_path)
        try:
            selected = list(range(len(pdf))) if page_indexes is None else sorted(set(page_indexes))
            if any(index < 0 or index >= len(pdf) for index in selected):
                raise IndexError("OCR page index is outside the PDF")
            workers = max(1, min(len(selected) or 1, os.cpu_count() or 1))
            texts = {index: "" for index in selected}
            stripped = set()
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for start in range(0, len(selected), workers):
                    batch = selected[start:start + workers]
                    images = []
                    for index in batch:
                        page = pdf[index]
                        try:
                            if strip_scan_background(page):
                                stripped.add(index)
                            images.append(self._render_for_ocr(page))
                        finally:
                            page.close()
                    for index, text in zip(batch, pool.map(read, images)):
                        texts[index] = text
        finally:
            pdf.close()

        # If removing a suspected background also removed the real text, read the untouched
        # page once more. This is separate from the final quality gate in inspect_pdf().
        retry = [i for i in sorted(stripped) if len(re.findall(r"\w", texts[i])) < 20]
        if retry:
            pdf = pdfium.PdfDocument(pdf_path)
            try:
                for index in retry:
                    page = pdf[index]
                    try:
                        texts[index] = read(self._render_for_ocr(page))
                    finally:
                        page.close()
            finally:
                pdf.close()
        return texts

    def ocr_pdf(self, pdf_path: str) -> str:
        """Read a PDF by looking at the page images. Nothing is uploaded.

        Tesseract is a separate program. TESSERACT_CMD points at it when it is not
        on PATH. A missing program raises TesseractNotFoundError, which the caller
        treats as a temporary failure rather than a scanned page it has already read.
        """
        texts = self._ocr_pages(pdf_path)
        return "\n".join(texts[index] for index in sorted(texts)).strip()

    def _render_for_ocr(self, page):
        """One page as a greyscale image. Tesseract reads grey; colour only costs memory."""
        bitmap = page.render(scale=self.OCR_DPI / 72, grayscale=True)
        try:
            return bitmap.to_pil().copy()
        finally:
            bitmap.close()

    def inspect_pdf(self, pdf_path: str):
        """Read a PDF far enough to tell a scanned page from a broken file.

        An embedded text layer is returned as-is and OCR is not called. A page with
        no text is rendered and read locally. Ollama is not called from here.
        """
        try:
            reader = PdfReader(pdf_path)
        except OSError as error:
            return DocumentRead(
                status="retryable_failure",
                reason=f"PDF could not be read: {error}",
            )
        except Exception as error:
            return DocumentRead(
                status="unsupported",
                reason=f"PDF could not be read: {error}",
            )

        try:
            if reader.is_encrypted:
                try:
                    reader.decrypt("")
                except Exception as error:
                    return DocumentRead(
                        status="unsupported",
                        reason=f"PDF is encrypted: {error}",
                    )

            try:
                pages = reader.pages
                page_count = len(pages)
                page_texts = [(page.extract_text() or "").strip() for page in pages]
            except Exception as error:
                message = str(error).lower()
                if "decrypt" in message or "encrypted" in message:
                    return DocumentRead(status="unsupported", reason="PDF is encrypted")
                return DocumentRead(
                    status="unsupported",
                    reason=f"PDF could not be read: {error}",
                )
        finally:
            # pypdf keeps the file open. On Windows that lock is still held during the
            # later model calls, and the hash or the archive move then fails.
            reader.close()

        if page_count < 1:
            return DocumentRead(status="unsupported", reason="PDF has no pages")
        missing_pages = [index for index, text in enumerate(page_texts) if not text]
        if not missing_pages:
            return DocumentRead(text="\n".join(page_texts), extraction_source="embedded")

        try:
            # Preserve the public whole-document OCR path for image-only PDFs. Hybrid files
            # OCR only their missing pages, then merge them back in original page order.
            if len(missing_pages) == page_count:
                ocr_by_page = {0: self.ocr_pdf(pdf_path)}
                all_scanned = True
            else:
                ocr_by_page = self._ocr_pages(pdf_path, missing_pages)
                all_scanned = False
        except Exception as error:
            if type(error).__name__ == "TesseractNotFoundError":
                reason = f"Tesseract is not installed: {error}"
            else:
                reason = f"OCR failed: {error}"
            return DocumentRead(status="retryable_failure", reason=reason)

        if all_scanned:
            ocr_text = (ocr_by_page.get(0) or "").strip()
            if not self._usable_ocr_text(ocr_text):
                return DocumentRead(
                    status="needs_ocr",
                    reason="OCR output failed the minimum text quality check",
                )
            combined = ocr_text
            source = "ocr"
        else:
            for index in missing_pages:
                ocr_text = (ocr_by_page.get(index) or "").strip()
                if not self._usable_ocr_text(ocr_text):
                    return DocumentRead(
                        status="needs_ocr",
                        reason=f"Page {index + 1} OCR output failed the minimum text quality check",
                    )
                page_texts[index] = ocr_text
            combined = "\n".join(page_texts).strip()
            source = "hybrid"
        print(
            f"  └─ [OCR] Read {len(combined)} characters from {os.path.basename(pdf_path)}"
        )
        return DocumentRead(text=combined, extraction_source=source)

    def _infer_vendor_fallback(self, raw_text: str, file_name: str) -> Optional[str]:
        """Heuristic fallback to extract vendor name from text header or filename."""
        # 1. Check filename patterns (e.g. 'sample_invoice_1_Apex_Cloud.pdf' or 'Google_Invoice.pdf')
        # Split on underscores/spaces only (NOT hyphens) so invoice codes like
        # "INV-2026-001" stay intact as a single token instead of fragmenting
        # into "INV", "2026", "001" (which would otherwise leak "INV" as a
        # bogus vendor name guess).
        cleaned_filename = os.path.splitext(file_name)[0]
        name_parts = re.split(r"[_ ]+", cleaned_filename)
        filtered_parts = [
            p for p in name_parts
            if p.lower() not in ["invoice", "receipt", "sample", "tax", "bill", "doc"]
            and not p.isdigit()
            and not INVOICE_CODE_TOKEN_PATTERN.match(p)
        ]
        if filtered_parts:
            return " ".join(filtered_parts)

        # 2. Fallback to the first non-empty lines of the document
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        for line in lines[:5]:
            if len(line) < 50 and not re.search(r"invoice|tax|receipt|bill to|date|total", line, re.IGNORECASE):
                return line
        return "Unknown Vendor"

    def extract_invoice_data(
        self,
        raw_text: str,
        file_name: str,
        use_rag: Optional[bool] = None,
    ) -> Optional[ExtractedInvoice]:
        """Query Ollama, optionally overriding RAG for this one extraction.

        The per-call override lets the workflow make a baseline attempt first and use
        retrieved examples only when deterministic validation finds a weakness. Evaluation
        callers that omit it keep the configured always-baseline or always-RAG behaviour.
        """
        self.last_retrieval = []
        self.last_error = None
        rag_context = ""
        rag_enabled = self.use_rag if use_rag is None else use_rag

        if rag_enabled:
            try:
                self.last_retrieval = rag_retrieval.retrieve_examples(
                    raw_text,
                    limit=self.rag_limit,
                    examples_path=self.rag_examples_path,
                )
                rag_context = rag_retrieval.format_examples(
                    self.last_retrieval
                )
            except (OSError, ValueError) as error:
                print(
                    "  [Warning] RAG retrieval failed; "
                    f"continuing without examples: {error}"
                )

        prompt = f"""
        You are an advanced Document Intelligence AI. Extract the invoice fields from the following document into structured JSON.

        Source Boundary Rules:
        - Extract every output value from CURRENT DOCUMENT CONTENT only.
        - Retrieved examples demonstrate structure and label interpretation only.
        - Never copy a vendor, invoice number, date, amount, currency, or item from an example.
        - If a value is absent from the current document, return null, 0.0, "Unknown", or an empty list as appropriate. Do not guess.

        Strict Extraction Rules:
        1. "vendor_name": Look at the header, sender, top letterhead, or logo text. If completely missing, return null.
        2. "invoice_number": Look for "Invoice #", "Tax Invoice No.", "Receipt No.", "Order #", or "Ref #".
        3. "date": Extract the invoice date, bill date, or transaction date.
        4. "total_amount": Locate the final payable amount ("Total", "Grand Total", "Amount Due", "Total AUD/USD"). Return as a numeric float only (e.g. 1500.50). Do not return 0.0 unless the invoice explicitly says 0.
        5. "currency": Detect the explicit currency (e.g. USD, AUD, EUR, GBP, CAD, TWD, $). If ambiguous or not found, return "Unknown". Do NOT assume USD.
        6. "items": Extract line items if visible.

        Never leave a field at its default (null / 0.0 / "Unknown") if the value is actually printed somewhere in the document -- read the whole document carefully before deciding a field is missing.

        Example:
        Document Content:
        \"\"\"
        Vendor: Bright Star Media Pty Ltd
        Invoice Number: INV-2025-042
        Date of Issue: 2025-03-12
        Currency: AUD
        Grand Total AUD 980.50
        \"\"\"
        Expected JSON:
        {{"invoice_number": "INV-2025-042", "vendor_name": "Bright Star Media Pty Ltd", "date": "2025-03-12", "total_amount": 980.50, "currency": "AUD", "items": []}}

        {rag_context}

        Current Document Content:
        \"\"\"{raw_text}\"\"\"
        """

        try:
            response = ollama.chat(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                format=ExtractedInvoice.model_json_schema(),
                options={"temperature": 0.0}
            )
            content = response["message"]["content"]
            parsed_data = ExtractedInvoice.model_validate_json(content)

            # Heuristic enhancement: If vendor is None, apply fallback inference
            if not parsed_data.vendor_name or parsed_data.vendor_name.strip() in ["", "None", "null"]:
                parsed_data.vendor_name = self._infer_vendor_fallback(raw_text, file_name)

            parsed_data.vendor_name = self._clean_vendor_label(parsed_data.vendor_name)

            # Deterministic regex fallback for the remaining structured fields
            parsed_data = self._infer_missing_fields_fallback(parsed_data, raw_text)

            return parsed_data
        except Exception as error:
            self.last_error = error
            print(f"  [Error] LLM Extraction failed: {error}")
            return None

    # Field captions the model sometimes copies along with the value it was asked for.
    VENDOR_LABELS = ("vendor:", "supplier:", "seller:", "from:", "billed from:", "bill to:")

    def _clean_vendor_label(self, value: Optional[str]) -> Optional[str]:
        """Strips a leading field caption from an extracted vendor name.

        On 2026-09-03 the model returned 'Vendor: Apex Cloud Solutions Pty Ltd' for
        invoice 1: the whole line, caption included. This is a repair to our output, not
        an extraction, so it is named to match the convention evaluation/run_eval.py uses
        to switch repairs off. With repairs disabled the harness still sees the caption,
        which is the point: fixing this quietly would delete the evidence that it happens.
        """
        if not value:
            return value
        text = str(value).strip()
        for label in self.VENDOR_LABELS:
            if text.lower().startswith(label):
                return text[len(label):].strip() or None
        return text

    def _infer_missing_fields_fallback(self, data: ExtractedInvoice, raw_text: str) -> ExtractedInvoice:
        """Deterministic regex fallback that backfills fields the LLM missed.

        Only overwrites a field when the LLM's value is missing/empty/zero/
        "Unknown", so a correct LLM answer is never clobbered.
        """
        if not data.invoice_number or str(data.invoice_number).strip().lower() in ["", "none", "null"]:
            match = INVOICE_NUMBER_PATTERN.search(raw_text)
            if match:
                data.invoice_number = match.group(1).strip().rstrip(".,")

        if not data.date or str(data.date).strip().lower() in ["", "none", "null"]:
            match = DATE_LABEL_PATTERN.search(raw_text)
            if match:
                candidate = match.group(1).strip()
                try:
                    parsed_date = date_parser.parse(candidate, fuzzy=True)
                    data.date = parsed_date.strftime("%Y-%m-%d")
                except (ValueError, OverflowError):
                    pass

        if not data.total_amount or data.total_amount <= 0:
            match = TOTAL_AMOUNT_PATTERN.search(raw_text)
            if match:
                try:
                    data.total_amount = float(match.group(1).replace(",", ""))
                except ValueError:
                    pass

        if not data.currency or data.currency.strip().lower() == "unknown":
            match = CURRENCY_CODE_PATTERN.search(raw_text)
            if match:
                data.currency = match.group(1).upper()

        return data

# =====================================================================
# 3. Confidence Validator
# =====================================================================
class ConfidenceValidator:
    """Scores an extraction against the source text, and decides whether it may auto-pass.

    The score is not a confidence score and this class does not turn it into one. It measures
    field completeness and agreement with the document text. See validation-gate-spec.md.
    """

    # A line whose text contains one of these introduces a grand total. Deliberately narrower
    # than the summary-row labels in storage.py: "subtotal" is excluded, because a subtotal
    # sits before tax and must not be accepted as the payable amount.
    TOTAL_LABELS = ("grand total", "total due", "amount due", "balance due",
                    "invoice total", "total")

    # How many lines may sit between a total label and its amount. Two covers the layouts in
    # evaluation/samples, where the label and the amount are on adjacent lines. Wider windows
    # start accepting line-item amounts as though they were the total.
    LABEL_WINDOW = 2

    VENDOR_LABELS = ("vendor:", "supplier:", "seller:", "from:", "billed from:", "bill to:")

    @classmethod
    def _looks_like_a_label(cls, value: Optional[str]) -> bool:
        return bool(value) and str(value).strip().lower().startswith(cls.VENDOR_LABELS)

    def __init__(self, threshold: float = 0.80):
        self.threshold = threshold

    # -- amount verification -------------------------------------------
    @staticmethod
    def _cents_on_line(line: str) -> List[int]:
        """Every money-like number on one line, as integer cents.

        Cents, not floats, for the same reason the database stores cents. Comparing with
        `abs(a - b) < 0.01` looked reasonable and was wrong: abs(1500.0 - 1500.01) evaluates
        to 0.009999999999763531, which is less than 0.01, so a near miss compared equal.
        Integer cents make the comparison exact.
        """
        values = []
        for token in re.findall(r"\d[\d,]*(?:\.\d+)?", line):
            cents = storage.to_cents(token.replace(",", ""))
            if cents is not None:
                values.append(cents)
        return values

    def verify_amount(self, amount: Optional[float], raw_text: str) -> str:
        """Returns 'verified', 'present' or 'absent'.

        'verified' means the amount sits within LABEL_WINDOW lines of a grand-total label.
        'present' means it appears somewhere in the document but not near such a label, which
        is what a line-item total looks like. The distinction matters: on invoice 3 the true
        total is 2350.00 while 1500.00 also appears, as a line item.
        """
        target = storage.to_cents(amount)
        if not target:
            return "absent"

        lines = raw_text.splitlines()
        label_lines = [
            i for i, line in enumerate(lines)
            if any(label in line.lower() for label in self.TOTAL_LABELS)
        ]

        found_anywhere = False
        for i, line in enumerate(lines):
            if target not in self._cents_on_line(line):
                continue
            found_anywhere = True
            if any(abs(i - j) <= self.LABEL_WINDOW for j in label_lines):
                return "verified"

        return "present" if found_anywhere else "absent"

    # -- scoring -------------------------------------------------------
    def explain(self, data: ExtractedInvoice, raw_text: str) -> dict:
        """The per-check detail behind the score, so a task can carry a real reason.

        A score of 1.00 means the extraction is complete: no field is empty, every value
        that can be checked against the document agrees with it, and the amount was found
        beside a grand-total label. Anything missing reduces the score. A document scoring
        0.85 with 'items' empty is telling you precisely what it failed to read.
        """
        lower_text = raw_text.lower()
        amount_state = self.verify_amount(data.total_amount, raw_text)

        rows = storage.normalise_items([i.model_dump() for i in data.items])
        reconciliation = storage.reconcile(storage.to_cents(data.total_amount), rows)

        # Completeness: is anything empty? 0.40 of the score, 0.06 per header field and
        # 0.10 for line items, which are worth more because they are a whole table.
        complete = {
            "invoice_number": bool(data.invoice_number and data.invoice_number != "None"),
            "vendor": bool(data.vendor_name and data.vendor_name != "Unknown Vendor"),
            "date": bool(data.date and str(data.date) != "None"),
            "currency": bool(data.currency and data.currency != "Unknown"),
            "total": bool(data.total_amount and data.total_amount > 0),
        }
        has_items = bool(data.items)

        # Agreement with the document: is any of it invented? 0.60 of the score.
        checks = {
            "invoice_number_in_text": bool(
                data.invoice_number and data.invoice_number.lower() in lower_text),
            "vendor_in_text": bool(data.vendor_name and data.vendor_name.lower() in lower_text),
            "vendor_is_not_a_label": not self._looks_like_a_label(data.vendor_name),
        }
        checks.update({f"{k}_present": v for k, v in complete.items()})
        checks["items_present"] = has_items

        score = (
            0.06 * sum(complete.values())
            + 0.10 * has_items
            + 0.10 * checks["invoice_number_in_text"]
            + 0.08 * checks["vendor_in_text"]
            + 0.07 * checks["vendor_is_not_a_label"]
            + {"verified": 0.25, "present": 0.125, "absent": 0.0}[amount_state]
            + {"exact": 0.10, "plausible": 0.10, "unknown": 0.05, "short": 0.0}[reconciliation]
        )
        score = round(min(score, 1.0), 2)

        # Hard rules, not weightings. A weighted score that happens to land below the
        # threshold is fragile: change one weight and the guarantee disappears silently.
        # Money we could not confirm is never auto-approved; nor is a vendor that is
        # actually a field label; nor line items that exceed the total they belong to.
        passes = (
            score >= self.threshold
            and amount_state == "verified"
            and reconciliation != "short"
            and checks["vendor_is_not_a_label"]
        )

        empty = [k for k, v in complete.items() if not v] + ([] if has_items else ["items"])

        if amount_state == "absent":
            reason = (f"The total {data.total_amount} does not appear in the document."
                      if data.total_amount else "No total amount was extracted.")
        elif amount_state == "present":
            reason = (f"The total {data.total_amount} appears in the document but not beside a "
                      "grand-total label, so it may be a line item rather than the amount due.")
        elif not checks["vendor_is_not_a_label"]:
            reason = (f"The vendor {data.vendor_name!r} begins with a field label, so the "
                      "extraction captured the caption rather than the value.")
        elif reconciliation == "short":
            reason = ("The line items add up to more than the stated total, which neither tax "
                      "nor shipping explains.")
        elif not passes:
            reason = f"Validation score {score:.2f} is below the {self.threshold:.2f} threshold."
        elif empty:
            reason = ("Passed the validation gate, with "
                      f"{', '.join(empty)} not extracted. Awaiting business approval.")
        else:
            reason = "Passed the validation gate. Awaiting business approval."

        return {
            "score": score,
            "status": "Validated" if passes else "NeedsReview",
            "amount_state": amount_state,
            "reconciliation": reconciliation,
            "empty": empty,
            "checks": checks,
            "reason": reason,
        }

    def evaluate(self, data: ExtractedInvoice, raw_text: str) -> tuple[float, str]:
        """Kept as a 2-tuple: evaluation/run_eval.py unpacks exactly two values."""
        detail = self.explain(data, raw_text)
        return detail["score"], detail["status"]

def should_retry_with_rag(verdict: dict) -> bool:
    """Return whether deterministic validation found a reason for a RAG retry."""
    return (
        verdict["status"] != "Validated"
        or bool(verdict["empty"])
        or verdict["amount_state"] != "verified"
        or verdict["reconciliation"] in {"unknown", "short"}
    )


def validation_quality(verdict: dict) -> tuple:
    """Rank two extraction verdicts using evidence, not model self-confidence.

    The ordering protects business safety first. Passing the validation gate, verifying the
    payable amount, and reconciling line items outrank a small numerical score difference.
    The final completeness component makes ties deterministic.
    """
    amount_rank = {"absent": 0, "present": 1, "verified": 2}
    reconciliation_rank = {"short": 0, "unknown": 1, "plausible": 2, "exact": 3}
    return (
        int(verdict["status"] == "Validated"),
        amount_rank.get(verdict["amount_state"], -1),
        reconciliation_rank.get(verdict["reconciliation"], -1),
        verdict["score"],
        -len(verdict["empty"]),
    )


def rag_result_is_better(baseline: dict, rag: dict) -> bool:
    """Adopt RAG only for a strict improvement; equal results keep the first answer."""
    return validation_quality(rag) > validation_quality(baseline)


# =====================================================================
# 3b. Document Intelligence (category / summary / action items)
# =====================================================================
class DocumentIntelligenceRunner:
    """Runs email_ai.py's document analysis over one already-extracted document.

    A second, independent Ollama pass (email_ai.analyse_email), not part of
    ExtractedInvoice/DocumentExtractor above. Kept separate rather than folded into that
    schema so this pass can fail without affecting the invoice fields it has nothing to do
    with -- see ai-document-fields-spec.md ("all optional: that pass can fail independently
    and a document must still be stored without it").

    email_ai.py's schema is built for an email (subject/sender/body/attachments). main.py
    processes files from inbox/, which may or may not have arrived by email, so the
    document's own text is passed as if it were a single attachment; subject is the file
    name, since that is the only "envelope" information guaranteed to exist.
    """

    def analyse(self, file_name: str, raw_text: str) -> Optional[email_ai.EmailAnalysis]:
        if not raw_text.strip():
            return None
        try:
            message = email_ai.EmailMessageInput(
                subject=file_name,
                body="",
                attachments=[email_ai.ParsedAttachment(filename=file_name, content=raw_text)],
            )
            # analyse_email returns EmailAnalysisOutcome; category/summary/
            # action_items live on outcome.analysis, which matches this method's
            # declared return type.
            return email_ai.analyse_email(message).analysis
        except Exception as error:
            # Broad on purpose, matching DocumentExtractor.extract_invoice_data: a
            # connection error, a validation error and an Ollama response error are all
            # "this pass did not produce an answer", and the caller treats them alike.
            print(f"  [Warn] Document intelligence (category/summary/action items) failed: {error}")
            return None

    @staticmethod
    def as_action_item_rows(analysis: Optional[email_ai.EmailAnalysis]) -> List[dict]:
        """Converts email_ai.ActionItem objects to the plain dicts storage.py expects."""
        if not analysis:
            return []
        return [item.model_dump() for item in analysis.action_items]

# =====================================================================
# 4. Storage Layer
# =====================================================================
# The normalised schema, the content-hash upsert and the money helpers live in storage.py.
# StorageManager is imported above. See database-spec.md.

class DownstreamDispatcher:
    """Turns a processed document into the human work it implies.

    The Teams and Jira lines are still simulated: nothing here makes an HTTP call, and
    tasks.external_ref stays NULL until a real integration exists. What changed is that the
    task is now written to the database instead of only printed, so "how many documents are
    waiting for a person" is answerable and survives the terminal being closed.

    The routing rule is deliberately about what the document needs, not about the score:

        NeedsReview -> Review    the pipeline could not read it confidently
        Validated   -> Approve   it was read cleanly, but money still needs a human signature

    A Validated document therefore still creates work. Automation reduces the reading, it
    does not remove the approval.
    """

    def __init__(self, storage_manager: StorageManager):
        self.storage = storage_manager

    def dispatch(self, invoice_id: int, file_name: str, status: str, score: float,
                 reason: str = "") -> None:
        task_type = "Review" if status == "NeedsReview" else "Approve"
        # The gate knows why it decided what it decided. Passing that through means the task
        # says "the total does not appear in the document" instead of "low confidence score",
        # which is the difference between a queue a person can triage and one they cannot.
        reason = reason or f"Validation score {score:.2f}."

        from storage import connect

        with connect(self.storage.db_path) as conn:
            row = conn.execute(
                "SELECT approval_status FROM invoices WHERE invoice_id = ?",
                (invoice_id,),
            ).fetchone()
        if not row or row["approval_status"] != "Pending":
            cancelled = self.storage.resolve_tasks(
                invoice_id, state="Cancelled", task_type=task_type
            )
            if cancelled:
                print(f"  └─ [Task Queue] Cancelled stale {task_type} task -- "
                      "this invoice was already approved or rejected.")
            return

        result = self.storage.open_task(invoice_id, task_type, reason)

        if result["was_created"]:
            print(f"  └─ [Task Queue] Opened {task_type} task #{result['task_id']}: {reason}")
        else:
            print(f"  └─ [Task Queue] {task_type} task #{result['task_id']} is already open, "
                  "not duplicated.")

# =====================================================================
# 5. Workflow Orchestrator
# =====================================================================
class WorkflowOrchestrator:
    """Coordinates end-to-end processing pipeline."""

    def __init__(
        self,
        inbox_dir: str = "./inbox",
        archive_dir: str = "./archive",
        threshold: float = 0.80,
        use_rag: bool = False,
        rag_limit: int = 1,
        rag_examples_path: str = rag_retrieval.DEFAULT_EXAMPLES_PATH,
        db_path: str = storage.DEFAULT_DB_PATH,
    ):
        self.inbox_dir = inbox_dir
        self.archive_dir = archive_dir
        self.threshold = threshold
        self.use_rag = use_rag

        self.extractor = DocumentExtractor(
            model_name="llama3.2",
            use_rag=use_rag,
            rag_limit=rag_limit,
            rag_examples_path=rag_examples_path,
        )
        self.validator = ConfidenceValidator(threshold=self.threshold)
        self.intelligence = DocumentIntelligenceRunner()
        self.storage = StorageManager(db_path=db_path)
        self.dispatcher = DownstreamDispatcher(self.storage)

        os.makedirs(self.inbox_dir, exist_ok=True)
        os.makedirs(self.archive_dir, exist_ok=True)

    def _read_input(self, file_name: str, source_path: str) -> DocumentRead:
        """Text for the model, or a permanent/temporary reason to stop before calling it."""
        if file_name.lower().endswith(".pdf"):
            return self.extractor.inspect_pdf(source_path)
        try:
            with open(source_path, "r", encoding="utf-8", errors="ignore") as handle:
                raw_text = handle.read()
        except OSError as error:
            return DocumentRead(
                status="retryable_failure",
                reason=f"File could not be read: {error}",
            )
        except Exception as error:
            return DocumentRead(
                status="unsupported",
                reason=f"File could not be read: {error}",
            )
        if not raw_text.strip():
            return DocumentRead(status="unsupported", reason="File has no readable text")
        return DocumentRead(text=raw_text)

    def _record_failure(
        self, file_name: str, source_path: str, status: str, reason: str
    ) -> FileOutcome:
        """Write one ingestion_failures row and describe it for the listener.

        The byte hash is the identity. When the bytes cannot be read there is nothing
        to hash, so the name is used instead: otherwise a locked file would be claimed
        on every poll and the attempt count would never move.
        """
        source_sha256 = storage.sha256_file(source_path) or f"unreadable:{file_name}"
        row = self.storage.record_ingestion_failure(
            file_name, source_sha256, status, reason
        )
        attempt = row["attempt_count"]
        limit = storage.MAX_INGESTION_ATTEMPTS
        if status == "needs_ocr":
            print(f"  └─ [Needs OCR] {reason}")
        elif status == "unsupported":
            print(f"  └─ [Unsupported] {reason}")
        elif attempt >= limit:
            print(f"  └─ [Retry exhausted] attempt {attempt}/{limit}: {reason}")
        else:
            print(f"  └─ [Retry] attempt {attempt}/{limit}: {reason}")
        return FileOutcome(
            file_name=file_name,
            status=status,
            reason=reason,
            source_sha256=source_sha256,
            attempt_count=attempt,
        )

    def _process_file(self, file_name: str, run_id: int) -> FileOutcome:
        """Run one file. A failure here is an outcome, not a reason to drop the rest."""
        source_path = os.path.join(self.inbox_dir, file_name)
        try:
            with open(source_path, "rb") as handle:
                source_sha256 = storage.sha256_bytes(handle.read())
        except OSError as error:
            return self._record_failure(
                file_name,
                source_path,
                "retryable_failure",
                f"Could not hash {file_name}: {error}",
            )

        read = self._read_input(file_name, source_path)
        if read.status:
            return self._record_failure(file_name, source_path, read.status, read.reason)
        raw_text = read.text

        # 2. Extract with AI. In selective RAG mode the first pass is deliberately
        # example-free. Deterministic validation decides whether a second pass is useful.
        print("  └─ [Step 2: AI Extraction] Processing with Ollama (llama3.2)...")
        data = self.extractor.extract_invoice_data(
            raw_text,
            file_name,
            use_rag=False if self.use_rag else None,
        )
        if not data:
            detail = self.extractor.last_error
            reason = f"AI extraction failed: {detail}" if detail else "AI extraction failed"
            return self._record_failure(
                file_name, source_path, "retryable_failure", reason
            )

        verdict = self.validator.explain(data, raw_text)
        if self.use_rag and should_retry_with_rag(verdict):
            baseline_data, baseline_verdict = data, verdict
            trigger = baseline_verdict["reason"]
            print(f"  └─ [Step 2a: Selective RAG] Retry triggered: {trigger}")

            rag_data = self.extractor.extract_invoice_data(
                raw_text,
                file_name,
                use_rag=True,
            )
            if self.extractor.last_retrieval:
                selected = ", ".join(
                    f"{item['example_id']} (score {item['retrieval_score']})"
                    for item in self.extractor.last_retrieval
                )
                print(f"     Retrieved {selected}")

            if rag_data:
                rag_verdict = self.validator.explain(rag_data, raw_text)
                if rag_result_is_better(baseline_verdict, rag_verdict):
                    data, verdict = rag_data, rag_verdict
                    print(
                        "     Adopted RAG result: "
                        f"{baseline_verdict['score']:.2f} -> {rag_verdict['score']:.2f}"
                    )
                else:
                    data, verdict = baseline_data, baseline_verdict
                    print(
                        "     Kept baseline result: RAG did not improve the "
                        "evidence-based validation rank."
                    )
            else:
                data, verdict = baseline_data, baseline_verdict
                print("     Kept baseline result: the RAG extraction failed.")

        # 2b. Trace the document back to the email that delivered it, then pull that
        # sender's prior correspondence as context. A miss is normal rather than an
        # error: a file dropped straight into inbox/ never had an email.
        delivered_by = self.storage.email_for_attachment(file_name)
        email_id = delivered_by["email_id"] if delivered_by else None
        context = ""
        if delivered_by:
            print(f"  └─ [Step 2b: Link] Delivered by email_id={email_id} "
                  f"from {delivered_by['sender']}")
            hits = retrieval.retrieve(sender=delivered_by["sender"],
                                      strategy="sender", limit=3,
                                      db_path=self.storage.db_path)
            # Exclude the email that carried this very document: it is not history.
            hits = [h for h in hits if h["email_id"] != email_id]
            if hits:
                context = retrieval.as_context(hits, max_chars=1200)
                print(f"  └─ [Step 2c: Retrieval] {len(hits)} prior emails, "
                      f"{len(context)} chars of context")
        else:
            print("  └─ [Step 2b: Link] No email matched this file")

        # 3. Validation Gate. The verdict was calculated above so the optional RAG
        # retry and the stored decision use exactly the same deterministic rules.
        confidence, status = verdict["score"], verdict["status"]
        print(f"  └─ [Step 3: Validation Gate] Score: {confidence} -> {status} "
              f"(amount {verdict['amount_state']})")
        if verdict["amount_state"] != "verified":
            print(f"     {verdict['reason']}")

        # 3b. Document Intelligence: category, summary, action items.
        # Independent of steps 2-3 above; a failure here does not stop the document
        # from being stored, it is just stored without these three fields.
        print("  └─ [Step 3b: Document Intelligence] Categorising with Ollama...")
        analysis = self.intelligence.analyse(file_name, raw_text)
        if analysis:
            print(f"     Category: {analysis.category} | "
                  f"{len(analysis.action_items)} action item(s)")

        # 4. Storage, then archive.
        # The database write commits first. If the move then fails, the file simply stays
        # in inbox/ and the next run upserts onto the same row, rather than the old
        # failure mode of a file archived with no record.
        target_archive_path = os.path.abspath(os.path.join(self.archive_dir, file_name))
        content_sha256 = storage.content_hash(raw_text, source_sha256)

        record = ProcessedRecord(
            file_name=file_name,
            status=status,
            confidence_score=confidence,
            extracted_data=data,
            archive_path=target_archive_path,
            processed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )

        result = self.storage.save_invoice(
            run_id=run_id,
            file_name=record.file_name,
            source_sha256=source_sha256,
            content_sha256=content_sha256,
            extracted=data.model_dump(),
            validation_score=record.confidence_score,
            validation_status=record.status,
            archive_path=record.archive_path,
            raw_json=data.model_dump_json(),
            raw_text=raw_text,
            category=analysis.category if analysis else None,
            summary=analysis.summary if analysis else None,
            action_items=self.intelligence.as_action_item_rows(analysis),
            email_id=email_id,
        )
        self.storage.resolve_ingestion_failure(source_sha256)

        action = "Updated" if result["was_update"] else "Inserted"
        total = storage.from_cents(result["total_cents"])
        total_text = "no total" if total is None else f"{total:,.2f}"
        if result["recovery_note"]:
            total_text += f" (recovered from {result['recovery_note']})"
        print(f"  └─ [Step 4: Storage] {action} invoice_id={result['invoice_id']}: "
              f"{total_text}, {result['line_item_count']} line items, "
              f"{result['document_type']}, reconciliation {result['reconciliation']}")
        if result["reconciliation"] == "short":
            print("     [Warn] The stated total is less than the line items add up to. "
                  "Tax and shipping cannot reduce a total, so one of the two is wrong.")

        try:
            shutil.move(source_path, target_archive_path)
            print(f"  └─ [Step 4b: Archive] Moved to: {target_archive_path}")
        except OSError as error:
            print(f"  └─ [Warn] Record saved but archiving failed: {error}. "
                  f"File left in {self.inbox_dir} for the next run.")

        # 5. Downstream Dispatch
        # Auto-approval is a hard rule, not a weight implication: score 1.0 alone is
        # not enough. The amount must also be verified against the document
        # (validation-gate-spec.md section 2). Checking amount_state explicitly means a
        # future weight change cannot silently auto-approve unverified money. Anything
        # that fails either check still opens a task and waits for a person.
        try:
            if confidence >= 1.0 and verdict["amount_state"] == "verified":
                followup = self.storage.auto_approve(result["invoice_id"])
                print(f"  └─ [Auto-Approval] Score {confidence:.2f}, amount verified -- "
                      "approved without a "
                      "review task.")
                if followup:
                    if followup["was_created"]:
                        print(f"  └─ [Task Queue] Opened {followup['task_type']} task "
                              f"#{followup['task_id']} (post-approval hand-off).")
                    else:
                        print(f"  └─ [Task Queue] {followup['task_type']} task "
                              f"#{followup['task_id']} already open.")
                    task_dispatch.dispatch_task_to_jira(
                        self.storage,
                        followup["task_id"],
                        result["invoice_id"],
                        followup["task_type"],
                        followup.get("reason") or "Auto-approved invoice follow-up.",
                        approval_path="auto",
                    )
            else:
                self.dispatcher.dispatch(result["invoice_id"], file_name, status, confidence,
                                         reason=verdict["reason"])
        except Exception as error:
            # The invoice row is already committed. A task failure must not send the
            # file back through extraction, or a stored document would be quarantined
            # after three attempts.
            print(f"  └─ [Warn] Stored, but the follow-up task failed: {error}")

        return FileOutcome(
            file_name=file_name,
            status="stored",
            reason="",
            source_sha256=source_sha256,
            attempt_count=0,
        )

    def run(self) -> List[FileOutcome]:
        """Processes all documents in inbox. One outcome per file, including failures."""
        files = [f for f in os.listdir(self.inbox_dir) if not f.startswith(".")]

        if not files:
            print(f"[*] Notice: No documents found in '{self.inbox_dir}'.")
            return []

        run_id = self.storage.start_run(
            model_name=self.extractor.model_name,
            threshold=self.threshold,
        )
        print(f"=== Starting AI Automation Pipeline ({len(files)} files found, run_id={run_id}) ===")

        outcomes: List[FileOutcome] = []
        for file_name in files:
            print(f"\n[Step 1: Ingestion] Reading file: {file_name}")
            try:
                outcome = self._process_file(file_name, run_id)
            except Exception as error:
                # One bad file must not abandon the rest of the batch.
                print(f"  └─ [Fail] {file_name}: {error}")
                try:
                    outcome = self._record_failure(
                        file_name,
                        os.path.join(self.inbox_dir, file_name),
                        "retryable_failure",
                        str(error),
                    )
                except Exception as record_error:
                    print(f"  └─ [Warn] Could not record the failure: {record_error}")
                    outcome = FileOutcome(
                        file_name=file_name,
                        status="retryable_failure",
                        reason=str(error),
                        source_sha256=None,
                        attempt_count=1,
                    )
            outcomes.append(outcome)

        processed_count = sum(1 for outcome in outcomes if outcome.status == "stored")
        self.storage.finish_run(run_id, processed_count)
        print(f"\n=== Workflow Completed: {processed_count}/{len(files)} documents stored (run_id={run_id}) ===")
        return outcomes

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run the local invoice automation pipeline."
    )
    parser.add_argument(
        "--rag",
        action="store_true",
        help="Retry weak extractions with a retrieved anonymised example.",
    )
    parser.add_argument(
        "--rag-limit",
        type=int,
        default=1,
        help="Maximum number of RAG examples added to each prompt.",
    )
    parser.add_argument(
        "--rag-examples",
        default=rag_retrieval.DEFAULT_EXAMPLES_PATH,
        help="Path to the local RAG example repository.",
    )
    args = parser.parse_args()

    orchestrator = WorkflowOrchestrator(
        inbox_dir="./inbox",
        archive_dir="./archive",
        threshold=0.80,
        use_rag=args.rag,
        rag_limit=args.rag_limit,
        rag_examples_path=args.rag_examples,
    )
    outcomes = orchestrator.run()
    # Imported here so loading the pipeline does not load the mailbox listener.
    from email_listener import dispose_outcomes
    dispose_outcomes(orchestrator.inbox_dir, outcomes)
