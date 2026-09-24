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
# The dashboard queue: work that still needs a person to decide. Payment/File follow-ups
# are created after approval (including auto-approval) and live in Jira, not here.
APPROVAL_TASK_TYPES = ("Review", "Approve")
VALID_RECONCILIATIONS = ("exact", "plausible", "short", "unknown")
VALID_DOCUMENT_TYPES = ("Invoice", "Receipt", "Unknown")
VALID_OUTBOUND_CHANNELS = ("Teams", "Jira", "Planner", "Email")
VALID_OUTBOUND_STATES = ("Pending", "Sent", "Failed")

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
    threshold   REAL    NOT NULL CHECK (threshold BETWEEN 0 AND 1),
    doc_count   INTEGER NOT NULL DEFAULT 0
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
    attachment_names TEXT
);

CREATE INDEX ix_email_sender ON email_messages(sender);

-- One row per saved attachment. Added in migration 008 alongside email_messages.body_text:
-- before this, attachment-level dedup did not exist at all, and a re-run of
-- email_listener.py could only avoid re-saving a file by noticing a filename collision on
-- disk, which says nothing about whether the *content* was already seen.
CREATE TABLE email_attachments (
    attachment_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id       INTEGER NOT NULL REFERENCES email_messages(email_id) ON DELETE CASCADE,
    filename       TEXT    NOT NULL,
    content_sha256 TEXT    NOT NULL,
    saved_path     TEXT,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    -- The same attachment (by content, not by filename) recorded twice for one email is
    -- always a bug in the caller, not a legitimate second attachment.
    UNIQUE (email_id, content_sha256)
);

CREATE INDEX ix_email_attachments_email ON email_attachments(email_id);

-- Dashboard login. A reviewer is a row here; invoices.reviewed_by points at the person
-- who approved or rejected the document. Passwords are hashes, never the .env plaintext.
CREATE TABLE users (
    user_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT    NOT NULL UNIQUE,
    display_name    TEXT    NOT NULL,
    password_hash   TEXT    NOT NULL,
    jira_account_id TEXT,
    -- 1 until the person replaces the password that auth.py add stored. The dashboard
    -- refuses the rest of the app while this is set.
    must_change_password INTEGER NOT NULL DEFAULT 1 CHECK (must_change_password IN (0, 1))
);

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
                              CHECK (reconciliation IN ('exact','plausible','short','unknown')),
    -- Added in migration 009, alongside the action_items table below. Both come from
    -- email_ai.py's EmailAnalysis (JJ's jj/email-ai branch), run as a second pass over the
    -- same document text. category is one of email_ai.EmailOverview's Literal values;
    -- summary is its one-or-two-sentence plain-English summary. Nullable: this pass can
    -- fail (Ollama unreachable, a validation error) independently of the main extraction,
    -- and a document should still be stored without it rather than not at all.
    category          TEXT,
    summary           TEXT,
    -- Who approved or rejected this document. NULL for auto-approval and for anything
    -- still pending: same meaning as reviewed_at. Appended last so a migrated database
    -- and a fresh one agree on column order.
    reviewed_by       INTEGER REFERENCES users(user_id)
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

-- Added in migration 009. One row per follow-up action email_ai.py's ActionExtraction
-- pulled out of the document text -- "Pay by 2026-09-30", "Renew the contract before it
-- expires". Distinct from `tasks`: a task is this pipeline's own routing decision
-- (Review/Approve/Payment/File); an action item is a claim about what the *document*
-- itself asks for, with the model's supporting quote kept alongside it so a person can
-- check the claim against the source without reopening the file.
CREATE TABLE action_items (
    action_item_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id      INTEGER NOT NULL REFERENCES invoices(invoice_id) ON DELETE CASCADE,
    line_no         INTEGER NOT NULL,
    task            TEXT    NOT NULL,
    owner           TEXT,
    deadline_text   TEXT,
    evidence_quote  TEXT,
    is_done         INTEGER NOT NULL DEFAULT 0 CHECK (is_done IN (0,1)),
    UNIQUE (invoice_id, line_no)
);

CREATE INDEX ix_action_items_invoice ON action_items(invoice_id);

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
"""


class SchemaMismatch(RuntimeError):
    """Raised when the database on disk is not at SCHEMA_VERSION.

    Exists so that schema drift between teammates fails loudly instead of being papered
    over by CREATE TABLE IF NOT EXISTS.
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
        category: Optional[str] = None,
        summary: Optional[str] = None,
        action_items: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Upserts one invoice and replaces its line items, in a single transaction.

        Keyed on content_sha256, so re-processing the same document updates its row instead
        of appending a duplicate. approval_status, reviewed_at and reviewed_by are never
        overwritten: they record a human decision, not an extraction result.

        category, summary and action_items come from email_ai.py's document-intelligence
        pass (migration 009), run separately from the main extraction. All optional: that
        pass can fail independently and a document must still be stored without it.
        action_items entries are dicts shaped like email_ai.ActionItem
        (task/owner/deadline_text/evidence_quote); unrecognised keys are ignored.
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
                    archive_path, raw_json, email_id, category, summary, processed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
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
                    category          = excluded.category,
                    summary           = excluded.summary,
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
                    category,
                    summary,
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

            # Same replace-in-place pattern as line_items. is_done is not reset here: a
            # reprocess with an unchanged action list should not silently un-tick a box a
            # person already checked. Simplest safe rule, given we cannot yet match action
            # items across a re-run by anything better than position: only wipe and
            # reinsert when the new list actually differs in size or text.
            action_rows = [
                {
                    "task": str(item.get("task") or "").strip(),
                    "owner": item.get("owner") or None,
                    "deadline_text": item.get("deadline_text") or None,
                    "evidence_quote": item.get("evidence_quote") or None,
                }
                for item in (action_items or [])
                if str(item.get("task") or "").strip()
            ]
            existing_tasks = [
                r["task"] for r in conn.execute(
                    "SELECT task FROM action_items WHERE invoice_id = ? ORDER BY line_no",
                    (invoice_id,)).fetchall()
            ]
            if action_rows and [r["task"] for r in action_rows] != existing_tasks:
                conn.execute("DELETE FROM action_items WHERE invoice_id = ?", (invoice_id,))
                conn.executemany(
                    """
                    INSERT INTO action_items (
                        invoice_id, line_no, task, owner, deadline_text, evidence_quote
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (invoice_id, i, r["task"], r["owner"], r["deadline_text"], r["evidence_quote"])
                        for i, r in enumerate(action_rows, start=1)
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
            "category": category,
            "action_item_count": len(action_rows),
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

    # -- email attachments ---------------------------------------------
    def has_seen_attachment(self, email_id: int, content_sha256: str) -> bool:
        """Attachment-level dedup, checked before anything is written to disk.

        Scoped to one email on purpose: the same bytes attached to two different emails
        (a vendor resending an invoice) are two legitimate, separately-provenanced copies.
        """
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM email_attachments WHERE email_id = ? AND content_sha256 = ?",
                (email_id, content_sha256),
            ).fetchone()
        return row is not None

    def record_attachment(
        self,
        email_id: int,
        filename: str,
        content_sha256: str,
        saved_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Records one saved attachment. Idempotent on (email_id, content_sha256).

        Checks first rather than relying on the UNIQUE constraint to raise, so the caller
        can tell "recorded" apart from "already there" the same way open_task() does.
        """
        with connect(self.db_path) as conn:
            existing = conn.execute(
                "SELECT attachment_id FROM email_attachments "
                "WHERE email_id = ? AND content_sha256 = ?",
                (email_id, content_sha256),
            ).fetchone()
            if existing:
                return {"attachment_id": int(existing["attachment_id"]), "was_created": False}

            cursor = conn.execute(
                "INSERT INTO email_attachments (email_id, filename, content_sha256, saved_path) "
                "VALUES (?, ?, ?, ?) RETURNING attachment_id",
                (email_id, filename, content_sha256, saved_path),
            )
            attachment_id = int(cursor.fetchone()["attachment_id"])
            conn.commit()
        return {"attachment_id": attachment_id, "was_created": True}

    # -- users ---------------------------------------------------------
    def upsert_user(
        self,
        username: str,
        display_name: str,
        password_hash: str,
        jira_account_id: Optional[str] = None,
    ) -> int:
        """Inserts a dashboard user, or replaces the same username.

        The hash is stored as given. Callers hash the password before this; the
        plaintext never reaches the database.
        """
        username = (username or "").strip()
        display_name = (display_name or "").strip()
        if not username or not display_name or not password_hash:
            raise ValueError("username, display_name and password_hash are required")
        account_id = (jira_account_id or "").strip() or None
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                INSERT INTO users (
                    username, display_name, password_hash, jira_account_id,
                    must_change_password
                ) VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(username) DO UPDATE SET
                    display_name         = excluded.display_name,
                    password_hash        = excluded.password_hash,
                    jira_account_id      = excluded.jira_account_id,
                    must_change_password = 1
                RETURNING user_id
                """,
                (username, display_name, password_hash, account_id),
            ).fetchone()
            conn.commit()
        return int(row["user_id"])

    def user_by_username(self, username: str) -> Optional[sqlite3.Row]:
        """The login lookup, including the password hash."""
        with connect(self.db_path) as conn:
            return conn.execute(
                "SELECT user_id, username, display_name, password_hash, jira_account_id, "
                "must_change_password FROM users WHERE username = ?",
                ((username or "").strip(),),
            ).fetchone()

    def replace_password(self, user_id: int, password_hash: str) -> bool:
        """Stores a password the person chose and clears the first-login flag."""
        if not password_hash:
            raise ValueError("password_hash is required")
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE users SET password_hash = ?, must_change_password = 0 "
                "WHERE user_id = ?",
                (password_hash, user_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def count_users(self) -> int:
        with connect(self.db_path) as conn:
            return int(conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"])

    def user_by_id(self, user_id: int) -> Optional[sqlite3.Row]:
        """Display name and Jira account id for a reviewer. No password hash."""
        with connect(self.db_path) as conn:
            return conn.execute(
                "SELECT user_id, username, display_name, jira_account_id "
                "FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()

    def record_decision(self, invoice_id: int, decision: str, reviewed_by: int) -> bool:
        """Records who approved or rejected an invoice.

        Writes approval_status, reviewed_at and reviewed_by only. validation_status and
        validation_score stay as the pipeline left them. An unknown user_id is rejected
        rather than stored: a decision with no real reviewer is not a decision.
        Returns whether a row changed.
        """
        if decision not in ("Approved", "Rejected", "Pending"):
            raise ValueError(f"unknown decision {decision!r}")
        with connect(self.db_path) as conn:
            reviewer = conn.execute(
                "SELECT user_id FROM users WHERE user_id = ?", (reviewed_by,)
            ).fetchone()
            if not reviewer:
                raise ValueError(f"unknown reviewer {reviewed_by}")
            cursor = conn.execute(
                "UPDATE invoices SET approval_status = ?, reviewed_at = datetime('now'), "
                "reviewed_by = ? WHERE invoice_id = ?",
                (decision, reviewed_by, invoice_id),
            )
            conn.commit()
            return cursor.rowcount > 0

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

    def assign_task(self, task_id: int, assignee: Optional[str]) -> bool:
        """Assigns, reassigns, or (passing None) clears who is responsible for a task.

        Returns whether a row actually changed, so the dashboard can tell a bad task_id
        apart from a genuine no-op. Deliberately does not require the task to still be
        open: reassigning a resolved task's record for audit purposes is legitimate.
        """
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE tasks SET assignee = ? WHERE task_id = ?", (assignee, task_id)
            )
            conn.commit()
            return cursor.rowcount > 0

    def set_task_external_ref(self, task_id: int, external_ref: str) -> bool:
        """Records the external ticket key (e.g. Jira issue key) on a task."""
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE tasks SET external_ref = ? WHERE task_id = ?",
                (external_ref, task_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def task_by_id(self, task_id: int) -> Optional[sqlite3.Row]:
        """One task row, for Jira assignee sync."""
        with connect(self.db_path) as conn:
            return conn.execute(
                "SELECT task_id, invoice_id, task_type, reason, assignee, external_ref, state "
                "FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()

    def invoice_summary(self, invoice_id: int) -> Optional[sqlite3.Row]:
        """Header fields needed to build a Jira issue summary."""
        with connect(self.db_path) as conn:
            return conn.execute(
                """SELECT invoice_id, file_name, vendor_name, validation_score,
                          validation_status, document_type, total_cents
                   FROM invoices WHERE invoice_id = ?""",
                (invoice_id,),
            ).fetchone()

    # -- action items (email_ai.py's document-intelligence pass) -------
    def action_items_for(self, invoice_id: int) -> List[sqlite3.Row]:
        """The follow-up actions email_ai.py found in this document, in extraction order."""
        with connect(self.db_path) as conn:
            return conn.execute(
                "SELECT action_item_id, line_no, task, owner, deadline_text, evidence_quote, "
                "is_done FROM action_items WHERE invoice_id = ? ORDER BY line_no",
                (invoice_id,),
            ).fetchall()

    def set_action_item_done(self, action_item_id: int, is_done: bool) -> bool:
        """Lets a person check off an action item in the dashboard. Returns whether it changed."""
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE action_items SET is_done = ? WHERE action_item_id = ?",
                (1 if is_done else 0, action_item_id),
            )
            conn.commit()
            return cursor.rowcount > 0

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
        result["reason"] = reason
        return result

    def auto_approve(self, invoice_id: int) -> Optional[Dict[str, Any]]:
        """The score-1.0 path: no human review, no Review/Approve task. approval_status goes
        straight to Approved. reviewed_at stays NULL -- nobody reviewed it, and NULL is how
        the dashboard tells this apart from a human decision.

        Still hands off to the same post-approval work a human approval would: an invoice
        still has to be paid, a receipt only has to be filed. Any Review/Approve task left
        open from an earlier run is closed first, so the document does not sit in the
        dashboard queue after the decision has already been made.
        """
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE invoices SET approval_status = 'Approved' "
                "WHERE invoice_id = ? AND approval_status = 'Pending'",
                (invoice_id,))
            conn.commit()
            row = conn.execute(
                "SELECT approval_status FROM invoices WHERE invoice_id = ?",
                (invoice_id,),
            ).fetchone()
        if row and row["approval_status"] == "Approved":
            for task_type in APPROVAL_TASK_TYPES:
                self.resolve_tasks(invoice_id, state="Done", task_type=task_type)
        return self.open_followup_task(invoice_id)

    # -- the outbox ----------------------------------------------------
    def queue_outbound(self, invoice_id: int, channel: str, payload: str,
                       task_id: Optional[int] = None) -> int:
        """Records that something should reach an external system.

        Jira rows may be marked Sent or Failed by task_dispatch.py when configured.
        Teams and Planner rows stay Pending until a transport is built.
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

    def mark_outbound_sent(self, outbox_id: int, external_ref: str) -> bool:
        """Marks an outbox row as successfully dispatched."""
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE outbound_messages SET state = 'Sent', sent_at = datetime('now'), "
                "external_ref = ? WHERE outbox_id = ?",
                (external_ref, outbox_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def mark_outbound_failed(self, outbox_id: int, error: str) -> bool:
        """Records a failed dispatch attempt without aborting the pipeline."""
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE outbound_messages SET state = 'Failed', error = ? WHERE outbox_id = ?",
                (error, outbox_id),
            )
            conn.commit()
            return cursor.rowcount > 0

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

    def open_tasks(self, task_types: Optional[Iterable[str]] = None) -> List[sqlite3.Row]:
        """The work queue: every live task with the invoice it belongs to.

        Pass task_types to restrict to those kinds. The dashboard queue asks for
        APPROVAL_TASK_TYPES so Payment/File follow-ups (handed to Jira) stay out of it.
        """
        if task_types is not None:
            task_types = tuple(task_types)
            unknown = [t for t in task_types if t not in VALID_TASK_TYPES]
            if unknown:
                raise ValueError(
                    f"task_type must be one of {VALID_TASK_TYPES}, got {unknown[0]!r}"
                )
            if not task_types:
                return []

        placeholders = ",".join("?" for _ in OPEN_TASK_STATES)
        sql = f"""
                SELECT t.task_id, t.task_type, t.reason, t.assignee, t.state, t.created_at,
                       t.external_ref,
                       i.invoice_id, i.file_name, i.vendor_name, i.total_cents,
                       i.validation_status, i.approval_status, i.document_type
                FROM tasks t
                JOIN invoices i ON i.invoice_id = t.invoice_id
                WHERE t.state IN ({placeholders})
                """
        params: List[Any] = list(OPEN_TASK_STATES)
        if task_types is not None:
            type_placeholders = ",".join("?" for _ in task_types)
            sql += f" AND t.task_type IN ({type_placeholders})"
            params.extend(task_types)
        sql += " ORDER BY t.created_at, t.task_id"
        with connect(self.db_path) as conn:
            return conn.execute(sql, params).fetchall()
