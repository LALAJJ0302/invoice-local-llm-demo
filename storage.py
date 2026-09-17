"""Phase 4 storage layer for the local invoice pipeline.

Owns the relational schema, the content-hash upsert, and the money helpers. Deliberately
free of any dependency on main.py so that migrations/001_normalise.py can reuse it.

See database-spec.md for the schema, the field-by-field data dictionary, and the reasoning
behind the dedup key.
"""

import hashlib
import os
import re
import sqlite3
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA_VERSION = 11
DEFAULT_DB_PATH = "workflow_platform.db"

VALID_VALIDATION_STATUSES = ("Validated", "NeedsReview", "Failed")
VALID_TOTAL_SOURCES = ("model", "fallback", "manual")
VALID_BODY_SOURCES = ("intake", "mock")
VALID_TASK_TYPES = ("Review", "Approve", "Fix", "Payment", "File")
VALID_RECONCILIATIONS = ("exact", "plausible", "short", "unknown")
VALID_DOCUMENT_TYPES = ("Invoice", "Receipt", "Unknown")
VALID_OUTBOUND_CHANNELS = ("Teams", "Jira", "Planner", "Email")
VALID_OUTBOUND_STATES = ("Pending", "Sent", "Failed")
VALID_RUN_KINDS = ("invoice", "email")
VALID_THREAD_SOURCES = ("headers", "subject", "manual")

# JJ's module owns this list; it is a Literal on EmailAnalysis in email_ai.py. Constrained
# here because it is model output, and an LLM drifts to "Project Update" or "Meeting request"
# given the chance. A free-text category cannot be aggregated across runs.
VALID_EMAIL_CATEGORIES = (
    "Project update", "Meeting", "Invoice", "Quotation", "Issue", "Other",
)

# What a document calls itself, usually on its first line. Checked against the opening lines
# only, because these words also appear mid-document ("please pay this invoice") where they
# say nothing about the document's type.
INVOICE_MARKERS = ("tax invoice", "invoice", "bill to", "amount due", "payment due")
RECEIPT_MARKERS = ("receipt", "paid", "payment received", "thank you for your payment")
CLASSIFY_LINES = 6
VALID_TASK_STATES = ("Open", "InProgress", "Done", "Cancelled")
OPEN_TASK_STATES = ("Open", "InProgress")

# A line item whose description normalises to one of these is a summary row, not a
# purchase. Summary rows are excluded from SUM so they cannot be double-counted.
SUMMARY_LABELS = frozenset({
    "total", "grandtotal", "subtotal", "totaldue",
    "amountdue", "balancedue", "invoicetotal",
})

# The narrower set that may be read as the payable amount. "subtotal" is deliberately
# absent: a subtotal sits before tax, so treating it as the total understates the invoice.
GRAND_TOTAL_LABELS = frozenset({
    "total", "grandtotal", "totaldue", "amountdue", "balancedue", "invoicetotal",
})

DDL = """
CREATE TABLE schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE processing_runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    model_name  TEXT    NOT NULL,
    -- Nullable since migration 010. A run of the email AI has no threshold, and writing a
    -- meaningless 0.0 would put a number in a column that means nothing. The paired CHECK
    -- below is what keeps that from becoming "sometimes NULL for no reason".
    threshold   REAL    CHECK (threshold IS NULL OR threshold BETWEEN 0 AND 1),
    doc_count   INTEGER NOT NULL DEFAULT 0,
    -- Which pipeline this run belongs to. Without it SELECT AVG(threshold) silently mixes
    -- invoice runs with email runs once both exist.
    run_kind    TEXT    NOT NULL DEFAULT 'invoice'
                        CHECK (run_kind IN ('invoice','email')),
    CHECK ((run_kind = 'invoice') = (threshold IS NOT NULL))
);

CREATE TABLE email_messages (
    email_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    -- The RFC 5322 Message-ID header. Globally unique by definition, so it is the natural
    -- key for "have we already fetched this email". Gives intake the duplicate protection
    -- it currently lacks.
    message_id       TEXT    NOT NULL UNIQUE,
    sender           TEXT    NOT NULL,
    subject          TEXT,
    received_at      TEXT,
    fetched_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    attachment_count INTEGER NOT NULL DEFAULT 0 CHECK (attachment_count >= 0),
    -- The message body, for retrieval. Sender and subject answer "has this vendor
    -- written before" and nothing else; the case that makes retrieval worth building
    -- is finding the earlier message that explains a disputed line item.
    body_text        TEXT,
    -- Where the body came from. A retrieval result measured over generated text must
    -- never be reported as though it came from real correspondence. Same reasoning as
    -- total_source: provenance belongs in a column, not in someone's memory.
    body_source      TEXT    CHECK (body_source IS NULL OR body_source IN ('intake','mock')),
    -- Comma-separated attachment file names, so a document read from inbox/ can be traced
    -- back to the message that delivered it. attachment_count alone cannot do that: it
    -- says how many arrived, not which.
    attachment_names TEXT,
    -- Thread identity, added by migration 010 so thread_analysis has something to join to.
    -- Nullable because nothing populates it for a message dropped in by hand.
    thread_id        TEXT,
    -- How that grouping was formed. 'headers' is the RFC 5322 reply chain and is correct;
    -- 'subject' is a guess that merges two unrelated "Monthly statement" emails and splits a
    -- conversation whose subject someone edited. A summary computed over a guess must never
    -- be reported as though it came from a real chain. Same reasoning as body_source.
    thread_source    TEXT    CHECK (thread_source IS NULL OR
                                    thread_source IN ('headers','subject','manual'))
);

CREATE INDEX ix_email_sender ON email_messages(sender);

CREATE TABLE invoices (
    invoice_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            INTEGER NOT NULL REFERENCES processing_runs(run_id),
    file_name         TEXT    NOT NULL,
    source_sha256     TEXT    NOT NULL,
    content_sha256    TEXT    NOT NULL,
    invoice_number    TEXT,
    vendor_name       TEXT,
    -- GLOB has no single-character wildcard: '_' is a literal underscore, not a
    -- placeholder (that is LIKE). The original '____-__-__' therefore rejected every real
    -- date. Character classes also verify the characters are digits. See migration 003.
    invoice_date      TEXT    CHECK (invoice_date IS NULL OR
                                     invoice_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    total_cents       INTEGER CHECK (total_cents IS NULL OR total_cents >= 0),
    currency          TEXT    CHECK (currency IS NULL OR currency = 'Unknown' OR length(currency) = 3),
    -- The gate's score, and the verdict derived from it by thresholding. Both are
    -- written by ConfidenceValidator, never by the model and never by a person. The
    -- 'validation_' stem keeps that ownership visible next to approval_status below.
    validation_score  REAL    NOT NULL CHECK (validation_score BETWEEN 0 AND 1),
    validation_status TEXT    NOT NULL
                              CHECK (validation_status IN ('Validated','NeedsReview','Failed')),
    total_source      TEXT    NOT NULL DEFAULT 'model'
                              CHECK (total_source IN ('model','fallback','manual')),
    approval_status   TEXT    NOT NULL DEFAULT 'Pending'
                              CHECK (approval_status IN ('Pending','Approved','Rejected')),
    reviewed_at       TEXT,
    archive_path      TEXT,
    raw_json          TEXT,
    processed_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    -- Nullable on purpose: a document can be dropped straight into inbox/ by hand, which
    -- is the documented way to test without Gmail. NULL means "not delivered by email".
    email_id          INTEGER REFERENCES email_messages(email_id),
    -- The three below sit last deliberately. ALTER TABLE ADD COLUMN can only append, so a
    -- database built by replaying the migrations gets them here. Declaring them anywhere
    -- else would make a fresh database differ from a migrated one in column order, which
    -- changes what SELECT * returns. tests/test_migration.py asserts the two match.
    --
    -- Always 'model' until the extractor reports which path produced the vendor name: the
    -- filename heuristic in DocumentExtractor can silently replace a model answer and
    -- storage cannot see that it happened. See storage-completion-spec.md 1.2.
    vendor_source     TEXT    NOT NULL DEFAULT 'model'
                              CHECK (vendor_source IN ('model','fallback','manual')),
    -- An invoice is unpaid and needs approval; a receipt is already paid and needs filing.
    -- The follow-on task depends on this, not on how confidently the document was read.
    document_type     TEXT    NOT NULL DEFAULT 'Unknown'
                              CHECK (document_type IN ('Invoice','Receipt','Unknown')),
    -- total_cents against the sum of line items. A flag, not a constraint: tax and shipping
    -- make 'plausible' legitimate. Only 'short' is a genuine anomaly, because neither can
    -- reduce a total.
    reconciliation    TEXT    NOT NULL DEFAULT 'unknown'
                              CHECK (reconciliation IN ('exact','plausible','short','unknown'))
);

CREATE UNIQUE INDEX ux_invoices_content ON invoices(content_sha256);
CREATE INDEX ix_invoices_status ON invoices(validation_status);
CREATE INDEX ix_invoices_vendor ON invoices(vendor_name);
CREATE INDEX ix_invoices_run    ON invoices(run_id);

CREATE TABLE line_items (
    line_item_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id       INTEGER NOT NULL REFERENCES invoices(invoice_id) ON DELETE CASCADE,
    line_no          INTEGER NOT NULL,
    description      TEXT    NOT NULL,
    quantity         REAL,
    unit_price_cents INTEGER CHECK (unit_price_cents IS NULL OR unit_price_cents >= 0),
    line_total_cents INTEGER CHECK (line_total_cents IS NULL OR line_total_cents >= 0),
    is_summary_row   INTEGER NOT NULL DEFAULT 0 CHECK (is_summary_row IN (0,1)),
    UNIQUE (invoice_id, line_no)
);

CREATE INDEX ix_line_items_invoice ON line_items(invoice_id);

CREATE TABLE tasks (
    task_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id   INTEGER NOT NULL REFERENCES invoices(invoice_id) ON DELETE CASCADE,
    task_type    TEXT    NOT NULL CHECK (task_type IN ('Review','Approve','Fix','Payment','File')),
    reason       TEXT,
    assignee     TEXT,
    state        TEXT    NOT NULL DEFAULT 'Open'
                         CHECK (state IN ('Open','InProgress','Done','Cancelled')),
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    resolved_at  TEXT,
    external_ref TEXT,
    -- A task is resolved exactly when it carries a resolution time. Without this the two
    -- can drift and "how many are still open" stops being answerable.
    CHECK ((state IN ('Done','Cancelled')) = (resolved_at IS NOT NULL))
);

-- At most one live task of each type per invoice. This is what stops a re-run of the
-- pipeline from opening a second identical task every time it sees the same document.
CREATE UNIQUE INDEX ux_tasks_one_open ON tasks(invoice_id, task_type)
    WHERE state IN ('Open','InProgress');

CREATE INDEX ix_tasks_state ON tasks(state);
CREATE INDEX ix_tasks_invoice ON tasks(invoice_id);

-- One row per thing that should reach an external system. Nothing sends anything yet: rows
-- are written Pending and stay there. That is the honest state of the project, and a queue
-- holding pending notifications demonstrates the workflow, where a print statement that has
-- already scrolled past demonstrates nothing.
CREATE TABLE outbound_messages (
    outbox_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      INTEGER REFERENCES tasks(task_id) ON DELETE SET NULL,
    invoice_id   INTEGER NOT NULL REFERENCES invoices(invoice_id) ON DELETE CASCADE,
    channel      TEXT    NOT NULL CHECK (channel IN ('Teams','Jira','Planner','Email')),
    payload      TEXT    NOT NULL,
    state        TEXT    NOT NULL DEFAULT 'Pending'
                         CHECK (state IN ('Pending','Sent','Failed')),
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    sent_at      TEXT,
    external_ref TEXT,
    error        TEXT,
    CHECK ((state = 'Sent') = (sent_at IS NOT NULL))
);

CREATE INDEX ix_outbound_state ON outbound_messages(state);
CREATE INDEX ix_outbound_invoice ON outbound_messages(invoice_id);

-- ---------------------------------------------------------------------
-- The email AI module's output. Added by migration 011.
--
-- These four tables store what email_ai.py returns and nothing else. Storage does not
-- import that module: it does `from ollama import chat` at module scope, so importing it
-- would make the migrations and most of the test suite require Ollama to be installed.
-- The save methods take a plain mapping, and the caller does record.model_dump().
-- ---------------------------------------------------------------------

CREATE TABLE email_analysis (
    analysis_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id          INTEGER NOT NULL REFERENCES email_messages(email_id) ON DELETE CASCADE,
    run_id            INTEGER NOT NULL REFERENCES processing_runs(run_id),
    category          TEXT    NOT NULL CHECK (category IN
                              ('Project update','Meeting','Invoice','Quotation','Issue','Other')),
    summary           TEXT,
    -- The database allows three values to match invoices, but email_ai.py only ever emits
    -- two. A retained result that failed evidence validation is NeedsReview; Failed would
    -- mean no analysis exists at all, which is not a row. Storage never writes Failed.
    validation_status TEXT    NOT NULL
                              CHECK (validation_status IN ('Validated','NeedsReview','Failed')),
    -- A machine-readable code, not a sentence. Two exist today, empty_evidence_quote and
    -- evidence_quote_not_found, and a third will appear the first time the validator learns
    -- a new failure, so the values are not pinned. Rejecting spaces is enough to keep
    -- GROUP BY validation_reason countable, which is the point of having the column.
    validation_reason TEXT    CHECK (validation_reason IS NULL OR validation_reason NOT GLOB '* *'),
    -- Deliberately not capped at 3. email_ai.py already caps it in four places with
    -- le=MAX_EVIDENCE_ATTEMPTS. Repeating the cap here means raising that constant breaks
    -- every insert and costs a migration.
    attempt_count     INTEGER NOT NULL CHECK (attempt_count >= 1),
    processed_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (email_id, run_id),
    CHECK ((validation_status = 'Validated') = (validation_reason IS NULL))
);

CREATE INDEX ix_email_analysis_status ON email_analysis(validation_status);
CREATE INDEX ix_email_analysis_run ON email_analysis(run_id);

CREATE TABLE thread_analysis (
    thread_analysis_id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id          TEXT    NOT NULL,
    thread_source      TEXT    NOT NULL CHECK (thread_source IN ('headers','subject','manual')),
    -- Nullable: ThreadAnalysisRecord.latest_message_id is str | None, because it reads
    -- thread.messages[-1].message_id and that field is itself optional. A thread analysed
    -- from text that was never in the mailbox has nothing to point at, and refusing the row
    -- would lose the analysis rather than record its limits.
    latest_email_id    INTEGER REFERENCES email_messages(email_id),
    run_id             INTEGER NOT NULL REFERENCES processing_runs(run_id),
    summary            TEXT,
    validation_status  TEXT    NOT NULL
                               CHECK (validation_status IN ('Validated','NeedsReview','Failed')),
    validation_reason  TEXT    CHECK (validation_reason IS NULL OR validation_reason NOT GLOB '* *'),
    attempt_count      INTEGER NOT NULL CHECK (attempt_count >= 1),
    processed_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (thread_id, run_id),
    CHECK ((validation_status = 'Validated') = (validation_reason IS NULL))
);

CREATE INDEX ix_thread_analysis_run ON thread_analysis(run_id);

-- One table, two parents. ActionItem is a single Pydantic class used for both
-- EmailAnalysis.action_items and ThreadSummary.outstanding_actions, so the row shape is
-- identical and only the owner differs. That is why this is not the nullable foreign key
-- rejected for tasks in email-analysis-schema-spec.md section 1: there the two kinds need
-- different columns and half of every row would be NULL. Merging identical rows is
-- normalisation; merging different rows is the flat table Phase 4 undid.
--
-- No state column on purpose. Re-running an analysis deletes the children and re-inserts
-- them, the way save_invoice does with line_items, so a state column would reset a person's
-- finished work to Open every time a model was re-run. This is a record of what the model
-- said, and lifecycle belongs with tasks and the outbox.
CREATE TABLE action_items (
    action_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id        INTEGER REFERENCES email_analysis(analysis_id) ON DELETE CASCADE,
    thread_analysis_id INTEGER REFERENCES thread_analysis(thread_analysis_id) ON DELETE CASCADE,
    item_no            INTEGER NOT NULL,
    task               TEXT    NOT NULL,
    owner              TEXT,
    -- The wording exactly as the source put it. JJ's module copies it and does not convert.
    deadline_text      TEXT,
    -- Populated only where the wording is unambiguous. "end of month", "by Friday" and
    -- "30/09/2026" all stay NULL. See coerce_deadline_date.
    deadline_date      TEXT    CHECK (deadline_date IS NULL OR
                                deadline_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    evidence_quote     TEXT    NOT NULL,
    CHECK ((analysis_id IS NULL) <> (thread_analysis_id IS NULL)),
    -- A normalised date can only exist where there was wording to normalise. This is what
    -- stops storage producing a deadline with nothing behind it.
    CHECK (deadline_date IS NULL OR deadline_text IS NOT NULL)
);

CREATE UNIQUE INDEX ux_action_items_email ON action_items(analysis_id, item_no)
    WHERE analysis_id IS NOT NULL;
CREATE UNIQUE INDEX ux_action_items_thread ON action_items(thread_analysis_id, item_no)
    WHERE thread_analysis_id IS NOT NULL;
CREATE INDEX ix_action_items_owner ON action_items(owner);

-- latest_decisions is list[str], so one row per string. decision_no preserves the order a
-- list has and rows do not, the same job line_items.line_no does. Storing the list as a
-- joined blob was the line_items mistake, and it cost a migration to undo.
CREATE TABLE thread_decisions (
    decision_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_analysis_id INTEGER NOT NULL
                       REFERENCES thread_analysis(thread_analysis_id) ON DELETE CASCADE,
    decision_no        INTEGER NOT NULL,
    decision           TEXT    NOT NULL,
    UNIQUE (thread_analysis_id, decision_no)
);
"""


class SchemaMismatch(RuntimeError):
    """Raised when the database on disk is not at SCHEMA_VERSION.

    Exists so that schema drift between teammates fails loudly instead of being papered
    over by CREATE TABLE IF NOT EXISTS.
    """


class UnknownEmail(LookupError):
    """Raised when an analysis names a message_id the mailbox has never seen.

    An analysis of an email that is not in the database is a real error, not a row to write
    with a NULL foreign key. Failing here is what stops orphaned analyses accumulating.
    """


# =====================================================================
# Connection
# =====================================================================
def connect(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Opens SQLite with foreign keys enforced and WAL enabled.

    foreign_keys defaults OFF per connection, so without this the FKs are decorative.

    WAL lets the dashboard keep reading while the pipeline writes. It is a persistent
    property of the database file, so setting it here applies once and sticks. It is set
    with a plain execute rather than inside a transaction because SQLite refuses a
    journal_mode change while one is open.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        # A locked or read-only database can refuse the switch. Not fatal: the rollback
        # journal still works, it just serialises readers against the commit window.
        pass
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


# =====================================================================
# Money, text and label helpers
# =====================================================================
def to_cents(value: Any) -> Optional[int]:
    """Converts a dollar amount to integer cents. Returns None for missing or negative."""
    if value is None or value == "":
        return None
    try:
        cents = Decimal(str(value)).scaleb(2).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return None
    if cents < 0:
        return None
    return int(cents)


def from_cents(cents: Optional[int]) -> Optional[float]:
    """Converts integer cents back to dollars for display at the edge."""
    if cents is None:
        return None
    return int(cents) / 100.0


def normalise_extracted_text(text: str) -> str:
    """Collapses whitespace so trivial layout differences do not change the content hash."""
    return re.sub(r"\s+", " ", text or "").strip()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> Optional[str]:
    """Hashes the raw file bytes. Provenance only, never the dedup key: see the spec."""
    try:
        with open(path, "rb") as handle:
            return sha256_bytes(handle.read())
    except OSError:
        return None


def content_hash(raw_text: str, source_sha256: Optional[str]) -> str:
    """The dedup key: SHA-256 of the normalised text layer.

    Falls back to the byte hash for documents with no extractable text, such as the
    scanned PDFs that will arrive once OCR lands.
    """
    normalised = normalise_extracted_text(raw_text)
    if normalised:
        return sha256_bytes(normalised.encode("utf-8"))
    if source_sha256:
        return f"bytes:{source_sha256}"
    raise ValueError("cannot build a content hash without text or a byte hash")


def normalise_label(description: Any) -> str:
    """Lowercases and strips every non-alphanumeric character.

    "Grand Total" -> "grandtotal" (a summary row).
    "Total Station Rental" -> "totalstationrental" (a genuine line item).
    """
    return re.sub(r"[^a-z0-9]", "", str(description or "").lower())


def is_summary_row(description: Any, quantity: Any) -> bool:
    """Exact match on the normalised label plus quantity <= 1.

    Chosen over a substring search, which would wrongly flag "Total Station Rental".
    """
    if normalise_label(description) not in SUMMARY_LABELS:
        return False
    if quantity is None:
        return True
    try:
        return float(quantity) <= 1.0
    except (TypeError, ValueError):
        return True


def coerce_currency(value: Any) -> Optional[str]:
    """Accepts a 3-letter code, otherwise reports 'Unknown' rather than guessing."""
    if value is None:
        return None
    text = str(value).strip().upper()
    if len(text) == 3 and text.isalpha():
        return text
    return "Unknown"


_DATE_FORMATS = ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y", "%Y/%m/%d")


def coerce_date(value: Any) -> Optional[str]:
    """Normalises to YYYY-MM-DD, or returns None.

    Purely numeric slash formats are rejected on purpose: 03/04/2026 is ambiguous between
    Australian and US ordering, and a wrong guess is worse than a null. The original string
    always survives in raw_json.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# Wording that introduces a deadline without changing it. Stripped before parsing so that
# "by 30 September 2026" reaches coerce_date as a date. Longest first, because "due by"
# must be tried before "due".
_DEADLINE_LEAD_INS = (
    "no later than", "not later than", "due before", "due by", "due on",
    "before", "due", "by", "on",
)

_ORDINAL = re.compile(r"\b(\d{1,2})(st|nd|rd|th)\b", re.IGNORECASE)


def coerce_deadline_date(text: Any) -> Optional[str]:
    """Normalises an action item's deadline wording to YYYY-MM-DD, or returns None.

    JJ's module sends the original wording in deadline_text and does not convert it. This
    fills deadline_date **only where the wording is unambiguous**, which is narrower than it
    sounds: "end of month", "by Friday" and "30/09/2026" all return None and the wording
    survives in deadline_text.

    "by Friday" could in principle be resolved against the email's received_at. It is not,
    because which Friday and in whose timezone are both guesses, and how often a deadline
    cannot be normalised is a number worth reporting rather than one worth hiding.

    Delegates to coerce_date rather than parsing again, which is also what keeps the ISO
    passthrough honest: dateutil.parser.parse('2026-03-12', dayfirst=True) returns 3
    December, because dayfirst is applied to the last two components whatever the shape of
    the string. Nothing in this project may re-parse a value that is already ISO.
    """
    if text is None:
        return None
    candidate = str(text).strip().rstrip(".,;").strip()
    if not candidate:
        return None

    lowered = candidate.lower()
    for lead in _DEADLINE_LEAD_INS:
        if lowered.startswith(lead + " "):
            candidate = candidate[len(lead):].strip()
            break

    candidate = _ORDINAL.sub(r"\1", candidate)
    return coerce_date(candidate)


# =====================================================================
# Line item normalisation and total recovery
# =====================================================================
def normalise_items(items: Optional[Iterable[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Turns the raw_json items blob into line_items rows."""
    rows: List[Dict[str, Any]] = []
    for line_no, item in enumerate(items or [], start=1):
        if not isinstance(item, dict):
            continue
        description = str(item.get("description") or "").strip()
        if not description:
            description = f"(unnamed item {line_no})"
        quantity = item.get("quantity")
        try:
            quantity = float(quantity) if quantity is not None else None
        except (TypeError, ValueError):
            quantity = None
        rows.append({
            "line_no": line_no,
            "description": description,
            "quantity": quantity,
            "unit_price_cents": to_cents(item.get("unit_price")),
            "line_total_cents": to_cents(item.get("total")),
            "is_summary_row": 1 if is_summary_row(description, quantity) else 0,
        })
    return rows


def recover_total_cents(rows: List[Dict[str, Any]]) -> Tuple[Optional[int], Optional[str]]:
    """Derives a total from line items when the extractor did not report one.

    Returns (cents, reason). Prefers an explicit grand-total row over a sum, because a sum
    of line items misses tax and shipping on any real invoice. Callers must record
    total_source = 'fallback' so a derived total is never mistaken for an extracted one.
    """
    grand_totals = [
        r["line_total_cents"] for r in rows
        if r["is_summary_row"]
        and normalise_label(r["description"]) in GRAND_TOTAL_LABELS
        and r["line_total_cents"] is not None
    ]
    if grand_totals:
        return max(grand_totals), "summary row"

    line_totals = [
        r["line_total_cents"] for r in rows
        if not r["is_summary_row"] and r["line_total_cents"] is not None
    ]
    if line_totals:
        return sum(line_totals), f"sum of {len(line_totals)} line items"

    return None, None


def classify_document(raw_text: str) -> str:
    """Guesses whether a document is an Invoice or a Receipt from how it labels itself.

    Only the opening lines are read. The same words appear mid-document ("please pay this
    invoice", "thank you for your payment") where they describe an instruction rather than
    the document's type.

    Returns 'Unknown' when neither or both kinds of marker appear. Unknown is a real answer
    here: it routes the document to a person instead of routing it on a guess.
    """
    head = " ".join((raw_text or "").splitlines()[:CLASSIFY_LINES]).lower()
    invoice_hit = any(marker in head for marker in INVOICE_MARKERS)
    receipt_hit = any(marker in head for marker in RECEIPT_MARKERS)
    if invoice_hit and not receipt_hit:
        return "Invoice"
    if receipt_hit and not invoice_hit:
        return "Receipt"
    return "Unknown"


def reconcile(total_cents: Optional[int], rows: List[Dict[str, Any]]) -> str:
    """Compares the stated total against the sum of the line items.

    We hold the same amount twice, read two different ways: once from a total line, once
    from a table of items. Disagreement means at least one of them is wrong.

        exact      they agree
        plausible  total is higher, which tax or shipping would explain
        short      total is lower, which neither can explain
        unknown    there are no line items to compare against

    A flag, not a constraint. 'plausible' is the normal state of any real invoice carrying
    GST or freight. Only 'short' is an anomaly on its own.
    """
    line_totals = [r["line_total_cents"] for r in rows
                   if not r["is_summary_row"] and r["line_total_cents"] is not None]
    if total_cents is None or not line_totals:
        return "unknown"

    line_sum = sum(line_totals)
    if total_cents == line_sum:
        return "exact"
    return "plausible" if total_cents > line_sum else "short"


# =====================================================================
# Storage manager
# =====================================================================
class StorageManager:
    """Owns run lifecycle and the content-hash upsert."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._init_database()

    def _init_database(self) -> None:
        """Creates the schema on a fresh database, and refuses to run against a stale one."""
        with connect(self.db_path) as conn:
            if table_exists(conn, "invoices"):
                self._assert_version(conn)
                return
            if table_exists(conn, "workflow_records"):
                raise SchemaMismatch(
                    f"{self.db_path} still holds the flat workflow_records table. "
                    "Run: ./.venv/bin/python migrations/001_normalise.py"
                )
            conn.executescript(DDL)
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
            conn.commit()

    @staticmethod
    def _assert_version(conn: sqlite3.Connection) -> None:
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        found = row["v"] if row else None
        if found != SCHEMA_VERSION:
            raise SchemaMismatch(
                f"database is at schema version {found}, this code expects {SCHEMA_VERSION}. "
                "Run the migrations in migrations/."
            )

    # -- runs ---------------------------------------------------------
    def start_run(self, model_name: str, threshold: float) -> int:
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO processing_runs (model_name, threshold) VALUES (?, ?)",
                (model_name, threshold),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def start_email_run(self, model_name: str) -> int:
        """Opens a run of the email AI. No threshold, because there is no gate on this path.

        Separate from start_run so that nothing has to invent a 0.0 to satisfy a NOT NULL
        column. The paired CHECK on processing_runs enforces the pairing either way.
        """
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO processing_runs (model_name, threshold, run_kind) "
                "VALUES (?, NULL, 'email')",
                (model_name,),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, doc_count: int) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE processing_runs SET finished_at = datetime('now'), doc_count = ? "
                "WHERE run_id = ?",
                (doc_count, run_id),
            )
            conn.commit()

    # -- invoices -----------------------------------------------------
    def save_invoice(
        self,
        run_id: int,
        file_name: str,
        source_sha256: str,
        content_sha256: str,
        extracted: Dict[str, Any],
        validation_score: float,
        validation_status: str,
        archive_path: Optional[str],
        raw_json: str,
        email_id: Optional[int] = None,
        raw_text: str = "",
    ) -> Dict[str, Any]:
        """Upserts one invoice and replaces its line items, in a single transaction.

        Keyed on content_sha256, so re-processing the same document updates its row instead
        of appending a duplicate. approval_status and reviewed_at are never overwritten:
        they record a human decision, not an extraction result.
        """
        if validation_status not in VALID_VALIDATION_STATUSES:
            raise ValueError(f"validation_status must be one of {VALID_VALIDATION_STATUSES}, "
                             f"got {validation_status!r}")

        rows = normalise_items(extracted.get("items"))
        total_cents = to_cents(extracted.get("total_amount"))
        total_source = "model"
        recovery_note = None

        if not total_cents:  # covers both None and a reported 0.00
            recovered, reason = recover_total_cents(rows)
            if recovered:
                total_cents, total_source, recovery_note = recovered, "fallback", reason

        document_type = classify_document(raw_text)
        reconciliation = reconcile(total_cents, rows)

        if archive_path:
            archive_path = os.path.abspath(archive_path)

        with connect(self.db_path) as conn:
            existing = conn.execute(
                "SELECT invoice_id FROM invoices WHERE content_sha256 = ?", (content_sha256,)
            ).fetchone()

            cursor = conn.execute(
                """
                INSERT INTO invoices (
                    run_id, file_name, source_sha256, content_sha256, invoice_number,
                    vendor_name, invoice_date, total_cents, currency, validation_score,
                    validation_status, total_source, document_type, reconciliation,
                    archive_path, raw_json, email_id, processed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(content_sha256) DO UPDATE SET
                    run_id            = excluded.run_id,
                    file_name         = excluded.file_name,
                    source_sha256     = excluded.source_sha256,
                    invoice_number    = excluded.invoice_number,
                    vendor_name       = excluded.vendor_name,
                    invoice_date      = excluded.invoice_date,
                    total_cents       = excluded.total_cents,
                    currency          = excluded.currency,
                    validation_score  = excluded.validation_score,
                    validation_status = excluded.validation_status,
                    total_source      = excluded.total_source,
                    reconciliation    = excluded.reconciliation,
                    -- document_type is preserved on update: a person may have corrected it,
                    -- and a heuristic must not overwrite a human classification.
                    document_type     = CASE WHEN invoices.document_type = 'Unknown'
                                             THEN excluded.document_type
                                             ELSE invoices.document_type END,
                    archive_path      = excluded.archive_path,
                    raw_json          = excluded.raw_json,
                    -- COALESCE so a manual re-run never erases the email a document
                    -- originally arrived on.
                    email_id          = COALESCE(excluded.email_id, invoices.email_id),
                    processed_at      = excluded.processed_at
                RETURNING invoice_id
                """,
                (
                    run_id,
                    file_name,
                    source_sha256,
                    content_sha256,
                    extracted.get("invoice_number"),
                    extracted.get("vendor_name"),
                    coerce_date(extracted.get("date")),
                    total_cents,
                    coerce_currency(extracted.get("currency")),
                    validation_score,
                    validation_status,
                    total_source,
                    document_type,
                    reconciliation,
                    archive_path,
                    raw_json,
                    email_id,
                ),
            )
            invoice_id = int(cursor.fetchone()["invoice_id"])

            conn.execute("DELETE FROM line_items WHERE invoice_id = ?", (invoice_id,))
            conn.executemany(
                """
                INSERT INTO line_items (
                    invoice_id, line_no, description, quantity,
                    unit_price_cents, line_total_cents, is_summary_row
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        invoice_id, r["line_no"], r["description"], r["quantity"],
                        r["unit_price_cents"], r["line_total_cents"], r["is_summary_row"],
                    )
                    for r in rows
                ],
            )
            conn.commit()

        return {
            "invoice_id": invoice_id,
            "was_update": existing is not None,
            "total_cents": total_cents,
            "total_source": total_source,
            "recovery_note": recovery_note,
            "line_item_count": len(rows),
            "document_type": document_type,
            "reconciliation": reconciliation,
        }

    # -- email intake --------------------------------------------------
    def record_email(
        self,
        message_id: str,
        sender: str,
        subject: Optional[str] = None,
        received_at: Optional[str] = None,
        attachment_count: int = 0,
        body_text: Optional[str] = None,
        body_source: Optional[str] = None,
        attachment_names: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Records one fetched email. Idempotent on the RFC 5322 Message-ID.

        Intended to be called by the intake step before it saves attachments, so that a row
        in `invoices` can say which email delivered the document. Because message_id is
        UNIQUE, calling this again for an email already fetched updates it instead of
        inserting, which is what gives intake its duplicate protection.

        Returns the email_id and whether this email had been seen before.
        """
        if not message_id:
            raise ValueError("message_id is required: it is the key that prevents refetching")

        # A body without a recorded origin is worse than no body: a retrieval result
        # measured over generated text would be indistinguishable from one measured over
        # real correspondence. Refuse rather than store an unattributable body.
        if body_text is not None and body_source not in VALID_BODY_SOURCES:
            raise ValueError(
                f"body_source must be one of {VALID_BODY_SOURCES} when body_text is given, "
                f"got {body_source!r}")

        with connect(self.db_path) as conn:
            existing = conn.execute(
                "SELECT email_id FROM email_messages WHERE message_id = ?", (message_id,)
            ).fetchone()
            cursor = conn.execute(
                """
                INSERT INTO email_messages (message_id, sender, subject, received_at,
                                            attachment_count, body_text, body_source,
                                            attachment_names)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    sender           = excluded.sender,
                    subject          = excluded.subject,
                    received_at      = excluded.received_at,
                    attachment_count = excluded.attachment_count,
                    -- Keep an existing body when the caller supplies none, so a re-fetch
                    -- that reads only headers does not erase what was already captured.
                    body_text        = COALESCE(excluded.body_text, email_messages.body_text),
                    body_source      = COALESCE(excluded.body_source, email_messages.body_source),
                    attachment_names = COALESCE(excluded.attachment_names,
                                                email_messages.attachment_names)
                RETURNING email_id
                """,
                (message_id, sender, subject, received_at, max(0, int(attachment_count)),
                 body_text, body_source,
                 ",".join(attachment_names) if attachment_names else None),
            )
            email_id = int(cursor.fetchone()["email_id"])
            conn.commit()

        return {"email_id": email_id, "already_seen": existing is not None}

    def email_for_attachment(self, file_name: str) -> Optional[Dict[str, Any]]:
        """Finds which recorded email delivered a given attachment, by file name.

        The piece the design left open: intake saves an attachment to inbox/ and the
        pipeline later reads it, with nothing carrying the link between them. A sidecar
        file per attachment and a staging table were both considered. This is the third
        option and the cheapest: the file name is already unique in inbox/, and the
        mailbox already records which attachment each message carried.

        It is a lookup, not a guarantee. Two emails carrying attachments of the same name
        cannot be told apart this way, so the newest match wins and the caller is free to
        treat a miss as a miss. When intake writes the link directly this becomes
        redundant, which is the intended outcome.
        """
        with connect(self.db_path) as conn:
            row = conn.execute(
                """SELECT email_id, sender, subject FROM email_messages
                   WHERE attachment_names IS NOT NULL
                     AND (',' || attachment_names || ',') LIKE ('%,' || ? || ',%')
                   ORDER BY received_at DESC LIMIT 1""",
                (file_name,)).fetchone()
        if not row:
            return None
        return {"email_id": int(row["email_id"]), "sender": row["sender"],
                "subject": row["subject"]}

    def has_seen_email(self, message_id: str) -> bool:
        """Lets intake skip an email it already downloaded, without re-reading attachments."""
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM email_messages WHERE message_id = ?", (message_id,)
            ).fetchone()
        return row is not None

    # -- tasks ---------------------------------------------------------
    def open_task(
        self,
        invoice_id: int,
        task_type: str,
        reason: Optional[str] = None,
        assignee: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Opens a task for an invoice, unless one of that type is already live.

        The partial unique index enforces at most one Open or InProgress task per
        (invoice, type), so re-processing a document does not pile up duplicates. This
        method checks first rather than catching the IntegrityError, so the caller can tell
        the difference between "opened" and "already there".
        """
        if task_type not in VALID_TASK_TYPES:
            raise ValueError(f"task_type must be one of {VALID_TASK_TYPES}, got {task_type!r}")

        placeholders = ",".join("?" for _ in OPEN_TASK_STATES)
        with connect(self.db_path) as conn:
            existing = conn.execute(
                f"SELECT task_id FROM tasks WHERE invoice_id = ? AND task_type = ? "
                f"AND state IN ({placeholders})",
                (invoice_id, task_type, *OPEN_TASK_STATES),
            ).fetchone()
            if existing:
                return {"task_id": int(existing["task_id"]), "was_created": False}

            cursor = conn.execute(
                "INSERT INTO tasks (invoice_id, task_type, reason, assignee) "
                "VALUES (?, ?, ?, ?) RETURNING task_id",
                (invoice_id, task_type, reason, assignee),
            )
            task_id = int(cursor.fetchone()["task_id"])
            conn.commit()
        return {"task_id": task_id, "was_created": True}

    def resolve_tasks(
        self,
        invoice_id: int,
        state: str = "Done",
        task_type: Optional[str] = None,
    ) -> int:
        """Closes the live tasks on an invoice. Returns how many were closed.

        Called when a person approves or rejects: the work the task represented is finished,
        so the task should not stay in the queue. Already-closed tasks are left alone, so
        their original resolved_at is never rewritten.
        """
        if state not in ("Done", "Cancelled"):
            raise ValueError("a task is resolved as either 'Done' or 'Cancelled'")

        placeholders = ",".join("?" for _ in OPEN_TASK_STATES)
        sql = (f"UPDATE tasks SET state = ?, resolved_at = datetime('now') "
               f"WHERE invoice_id = ? AND state IN ({placeholders})")
        params = [state, invoice_id, *OPEN_TASK_STATES]
        if task_type:
            sql += " AND task_type = ?"
            params.append(task_type)

        with connect(self.db_path) as conn:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor.rowcount

    # -- the post-approval hand-off ------------------------------------
    # What a document needs once a person has accepted it. Depends on what the document IS,
    # not on how confidently it was read: an invoice still has to be paid, a receipt only
    # has to be filed. An unclassified document goes back to a person rather than being
    # routed on a guess.
    FOLLOWUP_BY_TYPE = {
        "Invoice": ("Payment", "Approved invoice. Payment has not been scheduled."),
        "Receipt": ("File", "Approved receipt. Nothing to pay; file and reconcile."),
        "Unknown": ("Review", "Approved, but the document type is unknown. Classify it before routing."),
    }

    def open_followup_task(self, invoice_id: int) -> Optional[Dict[str, Any]]:
        """Opens the task that follows approval. Returns None if the invoice is not approved.

        Approval used to terminate: the task closed and nothing happened next. This is the
        hand-off from Phase 5 to Phase 6. Rejection deliberately opens nothing, because the
        document was not accepted and nothing downstream should act on it.
        """
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT approval_status, document_type FROM invoices WHERE invoice_id = ?",
                (invoice_id,)).fetchone()
        if not row or row["approval_status"] != "Approved":
            return None

        task_type, reason = self.FOLLOWUP_BY_TYPE[row["document_type"]]
        result = self.open_task(invoice_id, task_type, reason)
        result["task_type"] = task_type
        return result

    # -- the outbox ----------------------------------------------------
    def queue_outbound(self, invoice_id: int, channel: str, payload: str,
                       task_id: Optional[int] = None) -> int:
        """Records that something should reach an external system. Sends nothing.

        Rows are written Pending and stay there. No Teams, Jira or Planner call is made
        anywhere in this codebase. This is the honest shape of where the project is: the
        decision to notify is made and recorded, the transport is not built. A real
        integration would read this table and fill in external_ref.
        """
        if channel not in VALID_OUTBOUND_CHANNELS:
            raise ValueError(f"channel must be one of {VALID_OUTBOUND_CHANNELS}, got {channel!r}")
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO outbound_messages (task_id, invoice_id, channel, payload) "
                "VALUES (?, ?, ?, ?) RETURNING outbox_id",
                (task_id, invoice_id, channel, payload))
            outbox_id = int(cursor.fetchone()["outbox_id"])
            conn.commit()
        return outbox_id


    def pending_outbound(self) -> List[sqlite3.Row]:
        """The dispatch queue: what would be sent if the integrations existed."""
        with connect(self.db_path) as conn:
            return conn.execute("""
                SELECT o.outbox_id, o.channel, o.payload, o.state, o.created_at,
                       i.invoice_id, i.file_name, i.vendor_name, i.document_type
                FROM outbound_messages o
                JOIN invoices i ON i.invoice_id = o.invoice_id
                WHERE o.state = 'Pending'
                ORDER BY o.created_at, o.outbox_id
            """).fetchall()

    def open_tasks(self) -> List[sqlite3.Row]:
        """The work queue: every live task with the invoice it belongs to."""
        placeholders = ",".join("?" for _ in OPEN_TASK_STATES)
        with connect(self.db_path) as conn:
            return conn.execute(
                f"""
                SELECT t.task_id, t.task_type, t.reason, t.assignee, t.state, t.created_at,
                       i.invoice_id, i.file_name, i.vendor_name, i.total_cents,
                       i.validation_status, i.approval_status, i.document_type
                FROM tasks t
                JOIN invoices i ON i.invoice_id = t.invoice_id
                WHERE t.state IN ({placeholders})
                ORDER BY t.created_at, t.task_id
                """,
                OPEN_TASK_STATES,
            ).fetchall()

    # -- email AI analysis ---------------------------------------------
    #
    # These take a plain mapping, which is what record.model_dump() produces. Storage does
    # not import email_ai: that module does `from ollama import chat` at import time, so
    # depending on it would make the migrations and most of the test suite need Ollama
    # installed. Same boundary storage.py already keeps against main.py.
    #
    # They also take message_id and thread_id rather than internal ids, and resolve them
    # here, exactly as email_for_attachment resolves a file name. JJ's records key on the
    # RFC 5322 string and should keep doing so.

    def _resolve_email_id(self, conn: sqlite3.Connection, message_id: Optional[str],
                          *, required: bool) -> Optional[int]:
        if not message_id:
            if required:
                raise UnknownEmail("an analysis record needs a message_id")
            return None
        row = conn.execute(
            "SELECT email_id FROM email_messages WHERE message_id = ?", (message_id,)
        ).fetchone()
        if row is None:
            if required:
                raise UnknownEmail(
                    f"no email with message_id {message_id!r}. Record it with record_email() "
                    "before storing an analysis of it."
                )
            return None
        return int(row["email_id"])

    @staticmethod
    def _action_rows(items: Optional[Iterable[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """Turns ActionItem dicts into action_items rows, normalising the deadline."""
        rows: List[Dict[str, Any]] = []
        for item_no, item in enumerate(items or [], start=1):
            deadline_text = item.get("deadline_text")
            rows.append({
                "item_no": item_no,
                "task": item.get("task") or "",
                "owner": item.get("owner"),
                "deadline_text": deadline_text,
                "deadline_date": coerce_deadline_date(deadline_text),
                "evidence_quote": item.get("evidence_quote") or "",
            })
        return rows

    @staticmethod
    def _write_action_items(conn: sqlite3.Connection, rows: List[Dict[str, Any]],
                            *, analysis_id: Optional[int] = None,
                            thread_analysis_id: Optional[int] = None) -> None:
        """Replaces a parent's action items wholesale.

        Delete and re-insert rather than merge, the way save_invoice handles line_items: a
        re-run is a new answer to the same question, and merging would leave items behind
        that the model no longer returns. This is also why action_items carries no state
        column, since a state would be reset here on every re-run.
        """
        column = "analysis_id" if analysis_id is not None else "thread_analysis_id"
        parent = analysis_id if analysis_id is not None else thread_analysis_id
        conn.execute(f"DELETE FROM action_items WHERE {column} = ?", (parent,))
        conn.executemany(
            f"""
            INSERT INTO action_items (
                {column}, item_no, task, owner, deadline_text, deadline_date, evidence_quote
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (parent, r["item_no"], r["task"], r["owner"],
                 r["deadline_text"], r["deadline_date"], r["evidence_quote"])
                for r in rows
            ],
        )

    def save_email_analysis(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Stores one EmailAnalysisRecord. Upserts on (email_id, run_id).

        Re-running the same run updates in place instead of accumulating, which is the
        defect Phase 4 fixed for invoices and should not be reintroduced here. Two different
        run_ids over the same email are two rows on purpose: that is the comparison run_id
        exists for.
        """
        analysis = record.get("analysis") or {}
        rows = self._action_rows(analysis.get("action_items"))

        with connect(self.db_path) as conn:
            email_id = self._resolve_email_id(conn, record.get("message_id"), required=True)
            run_id = record["run_id"]
            existing = conn.execute(
                "SELECT analysis_id FROM email_analysis WHERE email_id = ? AND run_id = ?",
                (email_id, run_id),
            ).fetchone()

            cursor = conn.execute(
                """
                INSERT INTO email_analysis (
                    email_id, run_id, category, summary,
                    validation_status, validation_reason, attempt_count, processed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')))
                ON CONFLICT (email_id, run_id) DO UPDATE SET
                    category          = excluded.category,
                    summary           = excluded.summary,
                    validation_status = excluded.validation_status,
                    validation_reason = excluded.validation_reason,
                    attempt_count     = excluded.attempt_count,
                    processed_at      = excluded.processed_at
                RETURNING analysis_id
                """,
                (
                    email_id,
                    run_id,
                    analysis.get("category"),
                    analysis.get("summary"),
                    record.get("validation_status"),
                    record.get("validation_reason"),
                    record.get("attempt_count"),
                    record.get("processed_at"),
                ),
            )
            analysis_id = int(cursor.fetchone()["analysis_id"])
            self._write_action_items(conn, rows, analysis_id=analysis_id)
            conn.commit()

        return {
            "analysis_id": analysis_id,
            "email_id": email_id,
            "was_update": existing is not None,
            "action_item_count": len(rows),
            "dated_count": sum(1 for r in rows if r["deadline_date"]),
            "undated_count": sum(
                1 for r in rows if r["deadline_text"] and not r["deadline_date"]),
        }

    def save_thread_analysis(self, record: Dict[str, Any], *,
                             thread_source: str) -> Dict[str, Any]:
        """Stores one ThreadAnalysisRecord. Upserts on (thread_id, run_id).

        thread_source is a keyword argument rather than a field of the record because it
        describes how the grouping was formed and JJ's module does not know. Until intake
        captures In-Reply-To and References it is 'subject' on every row, and every claim
        about threads has to say so.
        """
        if thread_source not in VALID_THREAD_SOURCES:
            raise ValueError(
                f"thread_source must be one of {VALID_THREAD_SOURCES}, got {thread_source!r}")

        analysis = record.get("analysis") or {}
        rows = self._action_rows(analysis.get("outstanding_actions"))
        decisions = list(analysis.get("latest_decisions") or [])

        with connect(self.db_path) as conn:
            # Not required: latest_message_id is str | None, and a thread analysed from text
            # that was never in the mailbox has nothing to point at.
            latest_email_id = self._resolve_email_id(
                conn, record.get("latest_message_id"), required=False)
            thread_id = record["thread_id"]
            run_id = record["run_id"]
            existing = conn.execute(
                "SELECT thread_analysis_id FROM thread_analysis "
                "WHERE thread_id = ? AND run_id = ?",
                (thread_id, run_id),
            ).fetchone()

            cursor = conn.execute(
                """
                INSERT INTO thread_analysis (
                    thread_id, thread_source, latest_email_id, run_id, summary,
                    validation_status, validation_reason, attempt_count, processed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')))
                ON CONFLICT (thread_id, run_id) DO UPDATE SET
                    thread_source     = excluded.thread_source,
                    latest_email_id   = excluded.latest_email_id,
                    summary           = excluded.summary,
                    validation_status = excluded.validation_status,
                    validation_reason = excluded.validation_reason,
                    attempt_count     = excluded.attempt_count,
                    processed_at      = excluded.processed_at
                RETURNING thread_analysis_id
                """,
                (
                    thread_id,
                    thread_source,
                    latest_email_id,
                    run_id,
                    analysis.get("summary"),
                    record.get("validation_status"),
                    record.get("validation_reason"),
                    record.get("attempt_count"),
                    record.get("processed_at"),
                ),
            )
            thread_analysis_id = int(cursor.fetchone()["thread_analysis_id"])

            self._write_action_items(conn, rows, thread_analysis_id=thread_analysis_id)
            conn.execute(
                "DELETE FROM thread_decisions WHERE thread_analysis_id = ?",
                (thread_analysis_id,),
            )
            conn.executemany(
                "INSERT INTO thread_decisions (thread_analysis_id, decision_no, decision) "
                "VALUES (?, ?, ?)",
                [(thread_analysis_id, n, text) for n, text in enumerate(decisions, start=1)],
            )
            conn.commit()

        return {
            "thread_analysis_id": thread_analysis_id,
            "latest_email_id": latest_email_id,
            "was_update": existing is not None,
            "action_item_count": len(rows),
            "decision_count": len(decisions),
        }

    @staticmethod
    def _read_action_items(conn: sqlite3.Connection, *, analysis_id: Optional[int] = None,
                           thread_analysis_id: Optional[int] = None) -> List[Dict[str, Any]]:
        column = "analysis_id" if analysis_id is not None else "thread_analysis_id"
        parent = analysis_id if analysis_id is not None else thread_analysis_id
        return [
            dict(r) for r in conn.execute(
                f"""
                SELECT item_no, task, owner, deadline_text, deadline_date, evidence_quote
                FROM action_items WHERE {column} = ? ORDER BY item_no
                """,
                (parent,),
            )
        ]

    def email_analysis_for(self, message_id: str,
                           run_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """The stored analysis of one email, with its action items in order.

        With no run_id, returns the most recent run's. Passing one is how two models are
        compared over the same mailbox.
        """
        with connect(self.db_path) as conn:
            sql = """
                SELECT a.*, e.message_id, e.subject, e.sender
                FROM email_analysis a
                JOIN email_messages e ON e.email_id = a.email_id
                WHERE e.message_id = ?
            """
            params: List[Any] = [message_id]
            if run_id is not None:
                sql += " AND a.run_id = ?"
                params.append(run_id)
            sql += " ORDER BY a.run_id DESC LIMIT 1"

            row = conn.execute(sql, params).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["action_items"] = self._read_action_items(
                conn, analysis_id=result["analysis_id"])
            return result

    def thread_analysis_for(self, thread_id: str,
                            run_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """The stored analysis of one thread, with its decisions and outstanding actions."""
        with connect(self.db_path) as conn:
            sql = "SELECT * FROM thread_analysis WHERE thread_id = ?"
            params: List[Any] = [thread_id]
            if run_id is not None:
                sql += " AND run_id = ?"
                params.append(run_id)
            sql += " ORDER BY run_id DESC LIMIT 1"

            row = conn.execute(sql, params).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["outstanding_actions"] = self._read_action_items(
                conn, thread_analysis_id=result["thread_analysis_id"])
            result["latest_decisions"] = [
                r["decision"] for r in conn.execute(
                    "SELECT decision FROM thread_decisions WHERE thread_analysis_id = ? "
                    "ORDER BY decision_no",
                    (result["thread_analysis_id"],),
                )
            ]
            return result
