"""Lightweight local PDF reading shared by the pipeline and dashboard.

This module deliberately has no Ollama, RAG, database or Streamlit dependency.  A caller can
therefore inspect an archived document without loading the model-facing pipeline in ``main.py``.
"""

import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from pypdf import PdfReader


class DocumentRead:
    """Text taken from a PDF, or a structured reason why it could not be read."""

    def __init__(
        self,
        text: str = "",
        status: Optional[str] = None,
        reason: str = "",
        extraction_source: Optional[str] = None,
    ):
        self.text = text
        self.status = status
        self.reason = reason
        # ``embedded`` is a native text layer, ``ocr`` is page images, and ``hybrid`` used both.
        self.extraction_source = extraction_source


def _clipped_area(bounds, width, height):
    left, bottom, right, top = bounds
    return max(0.0, min(right, width) - max(left, 0.0)) * max(
        0.0, min(top, height) - max(bottom, 0.0)
    )


def strip_scan_background(page) -> bool:
    """Drop a full-page colour background behind 1-bit text masks, when present."""
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


class DocumentReader:
    """Read native, scanned and mixed PDFs locally, without invoking an LLM."""

    # A scanned page has no text operators. 300 DPI is sufficient for the invoice fixtures
    # while remaining inexpensive for the short documents this project accepts.
    OCR_DPI = 300

    def extract_text_from_pdf(self, pdf_path: str) -> str:
        """Return the native text layer from all pages, when one is available."""
        try:
            reader = PdfReader(pdf_path)
            try:
                return "\n".join((page.extract_text() or "") for page in reader.pages).strip()
            finally:
                reader.close()
        except Exception as error:  # noqa: BLE001 - a broken PDF is a read failure, not a crash
            print(f"  [Error] Failed to read PDF {pdf_path}: {error}")
            return ""

    @staticmethod
    def _usable_ocr_text(text: str) -> bool:
        """Reject stray OCR noise while still accepting a short receipt."""
        text = (text or "").strip()
        characters = re.findall(r"[A-Za-z0-9]", text)
        words = re.findall(r"\b[A-Za-z0-9]{2,}\b", text)
        return len(characters) >= 20 and len(words) >= 4

    def _ocr_pages(self, pdf_path: str, page_indexes=None) -> dict[int, str]:
        """OCR selected zero-based pages and return text keyed by page number."""
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

        # Each page is processed concurrently, so each Tesseract process must stay single-threaded.
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

        # Retry an untouched layered page if background removal also removed its real text.
        retry = [index for index in sorted(stripped) if len(re.findall(r"\w", texts[index])) < 20]
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
        """Read a PDF from page images with local Tesseract; nothing is uploaded."""
        texts = self._ocr_pages(pdf_path)
        return "\n".join(texts[index] for index in sorted(texts)).strip()

    def _render_for_ocr(self, page):
        bitmap = page.render(scale=self.OCR_DPI / 72, grayscale=True)
        try:
            return bitmap.to_pil().copy()
        finally:
            bitmap.close()

    def inspect_pdf(self, pdf_path: str) -> DocumentRead:
        """Combine native text and OCR page by page, preserving the original page order."""
        try:
            reader = PdfReader(pdf_path)
        except OSError as error:
            return DocumentRead(status="retryable_failure", reason=f"PDF could not be read: {error}")
        except Exception as error:  # noqa: BLE001
            return DocumentRead(status="unsupported", reason=f"PDF could not be read: {error}")

        try:
            if reader.is_encrypted:
                try:
                    reader.decrypt("")
                except Exception as error:  # noqa: BLE001
                    return DocumentRead(status="unsupported", reason=f"PDF is encrypted: {error}")
            try:
                pages = reader.pages
                page_count = len(pages)
                page_texts = [(page.extract_text() or "").strip() for page in pages]
            except Exception as error:  # noqa: BLE001
                message = str(error).lower()
                if "decrypt" in message or "encrypted" in message:
                    return DocumentRead(status="unsupported", reason="PDF is encrypted")
                return DocumentRead(status="unsupported", reason=f"PDF could not be read: {error}")
        finally:
            reader.close()

        if page_count < 1:
            return DocumentRead(status="unsupported", reason="PDF has no pages")
        missing_pages = [index for index, text in enumerate(page_texts) if not text]
        if not missing_pages:
            return DocumentRead(text="\n".join(page_texts), extraction_source="embedded")

        try:
            if len(missing_pages) == page_count:
                ocr_by_page = {0: self.ocr_pdf(pdf_path)}
                all_scanned = True
            else:
                ocr_by_page = self._ocr_pages(pdf_path, missing_pages)
                all_scanned = False
        except Exception as error:  # noqa: BLE001
            reason = (
                f"Tesseract is not installed: {error}"
                if type(error).__name__ == "TesseractNotFoundError"
                else f"OCR failed: {error}"
            )
            return DocumentRead(status="retryable_failure", reason=reason)

        if all_scanned:
            ocr_text = (ocr_by_page.get(0) or "").strip()
            if not self._usable_ocr_text(ocr_text):
                return DocumentRead(
                    status="needs_ocr",
                    reason="OCR output failed the minimum text quality check",
                )
            combined, source = ocr_text, "ocr"
        else:
            for index in missing_pages:
                ocr_text = (ocr_by_page.get(index) or "").strip()
                if not self._usable_ocr_text(ocr_text):
                    return DocumentRead(
                        status="needs_ocr",
                        reason=f"Page {index + 1} OCR output failed the minimum text quality check",
                    )
                page_texts[index] = ocr_text
            combined, source = "\n".join(page_texts).strip(), "hybrid"

        print(f"  └─ [OCR] Read {len(combined)} characters from {os.path.basename(pdf_path)}")
        return DocumentRead(text=combined, extraction_source=source)
