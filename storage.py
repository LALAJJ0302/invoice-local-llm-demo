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

SCHEMA_VERSION = 5
DEFAULT_DB_PATH = "workflow_platform.db"

VALID_VALIDATION_STATUSES = ("Validated", "NeedsReview", "Failed")
VALID_TOTAL_SOURCES = ("model", "fallback", "manual")
VALID_TASK_TYPES = ("Review", "Approve", "Fix")
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
    attachment_count INTEGER NOT NULL DEFAULT 0 CHECK (attachment_count >= 0)
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
    email_id          INTEGER REFERENCES email_messages(email_id)
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
    task_type    TEXT    NOT NULL CHECK (task_type IN ('Review','Approve','Fix')),
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
                    validation_status, total_source, archive_path, raw_json, email_id, processed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
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
        }

    # -- email intake --------------------------------------------------
    def record_email(
        self,
        message_id: str,
        sender: str,
        subject: Optional[str] = None,
        received_at: Optional[str] = None,
        attachment_count: int = 0,
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

        with connect(self.db_path) as conn:
            existing = conn.execute(
                "SELECT email_id FROM email_messages WHERE message_id = ?", (message_id,)
            ).fetchone()
            cursor = conn.execute(
                """
                INSERT INTO email_messages (message_id, sender, subject, received_at, attachment_count)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    sender           = excluded.sender,
                    subject          = excluded.subject,
                    received_at      = excluded.received_at,
                    attachment_count = excluded.attachment_count
                RETURNING email_id
                """,
                (message_id, sender, subject, received_at, max(0, int(attachment_count))),
            )
            email_id = int(cursor.fetchone()["email_id"])
            conn.commit()

        return {"email_id": email_id, "already_seen": existing is not None}

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

    def open_tasks(self) -> List[sqlite3.Row]:
        """The work queue: every live task with the invoice it belongs to."""
        placeholders = ",".join("?" for _ in OPEN_TASK_STATES)
        with connect(self.db_path) as conn:
            return conn.execute(
                f"""
                SELECT t.task_id, t.task_type, t.reason, t.assignee, t.state, t.created_at,
                       i.invoice_id, i.file_name, i.vendor_name, i.total_cents,
                       i.validation_status, i.approval_status
                FROM tasks t
                JOIN invoices i ON i.invoice_id = t.invoice_id
                WHERE t.state IN ({placeholders})
                ORDER BY t.created_at, t.task_id
                """,
                OPEN_TASK_STATES,
            ).fetchall()
