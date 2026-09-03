import os
import re
import shutil
import sqlite3
from datetime import datetime
from typing import List, Optional
from dateutil import parser as date_parser
from pydantic import BaseModel, Field
from pypdf import PdfReader
import ollama

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
class DocumentExtractor:
    """Extracts text from PDF and performs structured inference using Ollama."""

    def __init__(self, model_name: str = "llama3.2"):
        self.model_name = model_name

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

    def extract_invoice_data(self, raw_text: str, file_name: str) -> Optional[ExtractedInvoice]:
        """Queries local Ollama model with strict few-shot instructions."""
        prompt = f"""
        You are an advanced Document Intelligence AI. Extract the invoice fields from the following document into structured JSON.

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

        Document Content:
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

            # Deterministic regex fallback for the remaining structured fields
            parsed_data = self._infer_missing_fields_fallback(parsed_data, raw_text)

            return parsed_data
        except Exception as error:
            print(f"  [Error] LLM Extraction failed: {error}")
            return None

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
    """Calculates confidence score based on field completeness and text matching."""

    def __init__(self, threshold: float = 0.80):
        self.threshold = threshold

    def evaluate(self, data: ExtractedInvoice, raw_text: str) -> tuple[float, str]:
        score = 0.0
        lower_text = raw_text.lower()

        # 1. Field completeness (50%)
        if data.invoice_number and data.invoice_number != "None":
            score += 0.15
        if data.vendor_name and data.vendor_name != "Unknown Vendor":
            score += 0.15
        if data.total_amount and data.total_amount > 0:
            score += 0.20

        # 2. Text match verification (50%)
        if data.invoice_number and data.invoice_number.lower() in lower_text:
            score += 0.25
        if data.vendor_name and data.vendor_name.lower() in lower_text:
            score += 0.25

        final_score = round(min(score, 1.0), 2)
        status = "Validated" if final_score >= self.threshold else "NeedsReview"
        return final_score, status

# =====================================================================
# 4. Storage Layer
# =====================================================================
class StorageManager:
    """Manages SQLite operations."""

    def __init__(self, db_path: str = "workflow_platform.db"):
        self.db_path = db_path
        self._init_database()

    def _init_database(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS workflow_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    confidence_score REAL NOT NULL,
                    invoice_number TEXT,
                    vendor_name TEXT,
                    date TEXT,
                    total_amount REAL,
                    currency TEXT,
                    archive_path TEXT,
                    raw_json TEXT,
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def save_record(self, record: ProcessedRecord):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO workflow_records (
                    file_name, status, confidence_score, invoice_number,
                    vendor_name, date, total_amount, currency,
                    archive_path, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record.file_name,
                record.status,
                record.confidence_score,
                record.extracted_data.invoice_number,
                record.extracted_data.vendor_name,
                record.extracted_data.date,
                record.extracted_data.total_amount,
                record.extracted_data.currency,
                record.archive_path,
                record.extracted_data.model_dump_json()
            ))
            conn.commit()

class DownstreamDispatcher:
    """Simulates Teams and Jira notifications."""
    @staticmethod
    def send_teams_alert(file_name: str, status: str, confidence: float):
        print(f"  └─ [Teams Webhook] Dispatched notification for {file_name} (Status: {status}, Score: {confidence})")

    @staticmethod
    def create_jira_ticket(file_name: str, reason: str):
        print(f"  └─ [Jira REST API] Created ticket for {file_name} (Reason: {reason})")

# =====================================================================
# 5. Workflow Orchestrator
# =====================================================================
class WorkflowOrchestrator:
    """Coordinates end-to-end processing pipeline."""

    def __init__(self, inbox_dir: str = "./inbox", archive_dir: str = "./archive", threshold: float = 0.80):
        self.inbox_dir = inbox_dir
        self.archive_dir = archive_dir
        self.threshold = threshold

        self.extractor = DocumentExtractor(model_name="llama3.2")
        self.validator = ConfidenceValidator(threshold=self.threshold)
        self.storage = StorageManager(db_path="workflow_platform.db")
        self.dispatcher = DownstreamDispatcher()

        os.makedirs(self.inbox_dir, exist_ok=True)
        os.makedirs(self.archive_dir, exist_ok=True)

    def run(self):
        """Processes all documents in inbox."""
        files = [f for f in os.listdir(self.inbox_dir) if not f.startswith(".")]

        if not files:
            print(f"[*] Notice: No documents found in '{self.inbox_dir}'.")
            return

        print(f"=== Starting AI Automation Pipeline ({len(files)} files found) ===")

        for file_name in files:
            source_path = os.path.join(self.inbox_dir, file_name)
            print(f"\n[Step 1: Ingestion] Reading file: {file_name}")

            # 1. Extract Text
            raw_text = ""
            if file_name.lower().endswith(".pdf"):
                raw_text = self.extractor.extract_text_from_pdf(source_path)
            else:
                try:
                    with open(source_path, "r", encoding="utf-8", errors="ignore") as f:
                        raw_text = f.read()
                except Exception:
                    pass

            if not raw_text.strip():
                print(f"  └─ [Skip] Document is empty or binary.")
                continue

            # 2. Extract with AI
            print("  └─ [Step 2: AI Extraction] Processing with Ollama (llama3.2)...")
            data = self.extractor.extract_invoice_data(raw_text, file_name)
            if not data:
                print(f"  └─ [Fail] AI extraction failed.")
                continue

            # 3. Confidence Gate
            confidence, status = self.validator.evaluate(data, raw_text)
            print(f"  └─ [Step 3: Confidence Gate] Confidence: {confidence} -> Status: {status}")

            # 4. Storage and Archive
            target_archive_path = os.path.join(self.archive_dir, file_name)
            shutil.move(source_path, target_archive_path)

            record = ProcessedRecord(
                file_name=file_name,
                status=status,
                confidence_score=confidence,
                extracted_data=data,
                archive_path=target_archive_path,
                processed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )
            self.storage.save_record(record)
            print(f"  └─ [Step 4: Storage] Saved to SQLite & moved to: {target_archive_path}")

            # 5. Downstream Dispatch
            self.dispatcher.send_teams_alert(file_name, status, confidence)
            if status == "NeedsReview":
                self.dispatcher.create_jira_ticket(file_name, "Low confidence score.")

        print("\n=== Workflow Completed Successfully ===")

if __name__ == "__main__":
    orchestrator = WorkflowOrchestrator(
        inbox_dir="./inbox",
        archive_dir="./archive",
        threshold=0.80
    )
    orchestrator.run()