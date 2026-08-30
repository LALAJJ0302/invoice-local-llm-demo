import os
import re
import shutil
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field
from pypdf import PdfReader
import ollama

import storage
from storage import StorageManager

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
        cleaned_filename = os.path.splitext(file_name)[0]
        name_parts = re.split(r"[-_ ]+", cleaned_filename)
        filtered_parts = [p for p in name_parts if p.lower() not in ["invoice", "receipt", "sample", "tax", "bill", "doc"] and not p.isdigit()]
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

            return parsed_data
        except Exception as error:
            print(f"  [Error] LLM Extraction failed: {error}")
            return None

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
        """The per-check detail behind the score, so a task can carry a real reason."""
        lower_text = raw_text.lower()
        amount_state = self.verify_amount(data.total_amount, raw_text)

        checks = {
            "invoice_number_present": bool(data.invoice_number and data.invoice_number != "None"),
            "vendor_present": bool(data.vendor_name and data.vendor_name != "Unknown Vendor"),
            "total_present": bool(data.total_amount and data.total_amount > 0),
            "invoice_number_in_text": bool(
                data.invoice_number and data.invoice_number.lower() in lower_text),
            "vendor_in_text": bool(data.vendor_name and data.vendor_name.lower() in lower_text),
        }

        # Completeness 0.40, agreement with the document 0.60.
        score = (
            0.15 * checks["invoice_number_present"]
            + 0.10 * checks["vendor_present"]
            + 0.15 * checks["total_present"]
            + 0.20 * checks["invoice_number_in_text"]
            + 0.15 * checks["vendor_in_text"]
            + {"verified": 0.25, "present": 0.125, "absent": 0.0}[amount_state]
        )
        score = round(min(score, 1.0), 2)

        # A hard rule, not a weighting. A weighted score that happens to land below the
        # threshold is fragile: change one weight and the guarantee disappears silently.
        # The policy is that money we could not confirm is never auto-approved.
        passes = score >= self.threshold and amount_state == "verified"

        if amount_state == "absent":
            reason = (f"The total {data.total_amount} does not appear in the document."
                      if data.total_amount else "No total amount was extracted.")
        elif amount_state == "present":
            reason = (f"The total {data.total_amount} appears in the document but not beside a "
                      "grand-total label, so it may be a line item rather than the amount due.")
        elif not passes:
            reason = f"Validation score {score:.2f} is below the {self.threshold:.2f} threshold."
        else:
            reason = "Passed the validation gate. Awaiting business approval."

        return {
            "score": score,
            "status": "Validated" if passes else "NeedsReview",
            "amount_state": amount_state,
            "checks": checks,
            "reason": reason,
        }

    def evaluate(self, data: ExtractedInvoice, raw_text: str) -> tuple[float, str]:
        """Kept as a 2-tuple: evaluation/run_eval.py unpacks exactly two values."""
        detail = self.explain(data, raw_text)
        return detail["score"], detail["status"]

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

        result = self.storage.open_task(invoice_id, task_type, reason)

        # The Teams line is still simulated. What changed is that the intent is recorded in
        # outbound_messages instead of only printed, so "what have we dispatched, and did it
        # succeed" is answerable. Nothing here contacts Teams; the row stays Pending.
        self.storage.queue_outbound(
            invoice_id, "Teams",
            f"{file_name}: {status} at score {score:.2f}. {reason}",
            task_id=result["task_id"])
        print(f"  └─ [Teams Webhook] Queued for {file_name} (Status: {status}, Score: {score})")
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

    def __init__(self, inbox_dir: str = "./inbox", archive_dir: str = "./archive", threshold: float = 0.80):
        self.inbox_dir = inbox_dir
        self.archive_dir = archive_dir
        self.threshold = threshold

        self.extractor = DocumentExtractor(model_name="llama3.2")
        self.validator = ConfidenceValidator(threshold=self.threshold)
        self.storage = StorageManager(db_path="workflow_platform.db")
        self.dispatcher = DownstreamDispatcher(self.storage)

        os.makedirs(self.inbox_dir, exist_ok=True)
        os.makedirs(self.archive_dir, exist_ok=True)

    def run(self):
        """Processes all documents in inbox."""
        files = [f for f in os.listdir(self.inbox_dir) if not f.startswith(".")]

        if not files:
            print(f"[*] Notice: No documents found in '{self.inbox_dir}'.")
            return

        run_id = self.storage.start_run(
            model_name=self.extractor.model_name,
            threshold=self.threshold,
        )
        print(f"=== Starting AI Automation Pipeline ({len(files)} files found, run_id={run_id}) ===")

        processed_count = 0
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

            # 3. Validation Gate
            verdict = self.validator.explain(data, raw_text)
            confidence, status = verdict["score"], verdict["status"]
            print(f"  └─ [Step 3: Validation Gate] Score: {confidence} -> {status} "
                  f"(amount {verdict['amount_state']})")
            if verdict["amount_state"] != "verified":
                print(f"     {verdict['reason']}")

            # 4. Storage, then archive.
            # The database write commits first. If the move then fails, the file simply stays
            # in inbox/ and the next run upserts onto the same row, rather than the old
            # failure mode of a file archived with no record.
            target_archive_path = os.path.abspath(os.path.join(self.archive_dir, file_name))
            source_sha256 = storage.sha256_file(source_path)
            if source_sha256 is None:
                # The file was readable a moment ago, so this is close to impossible. Guard
                # anyway: source_sha256 is NOT NULL, and a violation here would abort the run.
                print(f"  └─ [Skip] Could not hash {file_name}.")
                continue
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
            )
            processed_count += 1

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
            self.dispatcher.dispatch(result["invoice_id"], file_name, status, confidence,
                                     reason=verdict["reason"])

        self.storage.finish_run(run_id, processed_count)
        print(f"\n=== Workflow Completed: {processed_count}/{len(files)} documents stored (run_id={run_id}) ===")

if __name__ == "__main__":
    orchestrator = WorkflowOrchestrator(
        inbox_dir="./inbox",
        archive_dir="./archive",
        threshold=0.80
    )
    orchestrator.run()