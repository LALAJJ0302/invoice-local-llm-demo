# Document Intelligence: category, summary, action items — Spec

**Author:** Luke. **For review by:** JJ (owns extraction/`main.py`) and Neo (owns `storage.py`).
**Status:** proposed, not merged to `main`. Branch: `luke/team-tasks`.

## What this is

Wires JJ's already-built `email_ai.py` (branch `jj/email-ai`, merged into this branch, not
rewritten) into the pipeline so every processed document gets a **category**, a
**summary**, and a list of **action items** — none of which exist in `ExtractedInvoice` or
the database today.

This is deliberately an *integration*, not a new extraction schema. `email_ai.py` is JJ's
contract; nothing in it was changed. If JJ wants to change the categories, the prompt, or the
`ActionItem` shape, that happens on `jj/email-ai` and flows through here unchanged.

## Why not add these fields to `ExtractedInvoice` instead

Two reasons, both concrete:

1. **CLAUDE.md's documented root cause.** Every field in `ExtractedInvoice` is `Optional`
   with a default, which is *why* Ollama's constrained decoder legally omits fields
   (`extraction-schema-findings.md`). `email_ai.py` already gets this right — `category`,
   `summary` and `action_items` are all required, no defaults — and folding them into
   `ExtractedInvoice` risks that discipline slipping if anyone later adds an `Optional`
   sibling field without noticing the difference matters.
2. **Independent failure.** A category/summary pass failing must not take the invoice
   number, vendor, date, total and currency down with it. Two separate Ollama calls
   (`DocumentExtractor.extract_invoice_data` and `email_ai.analyse_email`) fail
   independently; one Pydantic model covering both would not.

## Integration point: `main.py`

New `DocumentIntelligenceRunner` (main.py, section 3b, between the validation gate and
storage):

```python
class DocumentIntelligenceRunner:
    def analyse(self, file_name: str, raw_text: str) -> Optional[email_ai.EmailAnalysis]:
        if not raw_text.strip():
            return None
        try:
            message = email_ai.EmailMessageInput(
                subject=file_name, body="",
                attachments=[email_ai.ParsedAttachment(filename=file_name, content=raw_text)])
            return email_ai.analyse_email(message)
        except Exception as error:
            print(f"  [Warn] Document intelligence failed: {error}")
            return None
```

`email_ai.py`'s schema is built for an email (`subject`/`sender`/`body`/`attachments`).
`main.py` processes files dropped in `inbox/`, which may or may not have arrived by email,
so the document's own extracted text is passed as if it were the email's one attachment,
and the file name stands in for the subject line — the only "envelope" fact guaranteed to
exist regardless of how the file arrived.

`WorkflowOrchestrator.run()` calls this after the validation gate (§3) and before storage
(§4), and passes the result's `category`, `summary`, and
`[item.model_dump() for item in analysis.action_items]` into `save_invoice()`. A `None`
result (the pass failed, or raw_text was empty) passes `None`/`None`/`[]` — the document is
still stored, just without these three fields, matching the "all optional, fails
independently" design in `task-assignment-and-review-spec.md`.

**A model-name mismatch, not touched here:** `DocumentExtractor` uses `"llama3.2"`,
`email_ai.MODEL_NAME` is `"llama3.2:latest"`. Ollama resolves both to the same pulled model,
so this is cosmetic today, but it is JJ's constant to reconcile, not this PR's.

## Storage: migration 009 (see `task-assignment-and-review-spec.md` for 008)

- `invoices.category TEXT`, `invoices.summary TEXT` — nullable, one document-intelligence
  pass per invoice, not normalised into their own table because both are scalar.
- `action_items` table, shaped like `line_items`:

```sql
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
```

Mirrors `email_ai.ActionItem` (`task`/`owner`/`deadline_text`/`evidence_quote`) exactly, plus
`is_done` so the dashboard can offer a checklist without a second table. `evidence_quote` is
kept specifically so a person can check the model's claim against the source text without
reopening the file — the same instinct behind `ConfidenceValidator.verify_amount()` checking
the total against the document rather than trusting the model's number.

**Deliberately distinct from `tasks`:** a task is this pipeline's own routing decision
(`Review`/`Approve`/`Payment`/`File`, opened by `DownstreamDispatcher`); an action item is a
claim about what the *document itself* asks for. An invoice can pass the validation gate
cleanly and still contain an action item ("renew the support contract by 2026-10-01") that
has nothing to do with payment approval.

`StorageManager.save_invoice()` gained `category`, `summary`, `action_items` keyword
arguments (all optional, defaulting to `None`/`None`/`None`) and replaces an invoice's
`action_items` rows the same way it already replaces `line_items` — **with one difference**:
it only deletes and reinserts when the new task list actually differs from what is stored,
so a reprocess that finds an unchanged action list does not silently reset a person's
`is_done` checkbox. `line_items` does not need this guard (nothing lets a person edit a line
item), which is why the two differ.

**Evidence:**
- `tests/test_storage.py::TestAIDocumentFields` (7 tests): storage, `NULL` defaults, blank
  tasks dropped, ordering, `is_done` toggling and survival across a reprocess, cascade delete.
- `tests/test_migration.py::TestAIDocumentFields` (6 tests) plus the round trip through
  `save_invoice` on a freshly migrated database.
- `tests/test_migration.py::TestChain::test_migrated_matches_fresh` still passes with
  `action_items` added to the compared table list.

## What this does not do

- Does not run `email_ai.summarise_thread()` — there is no concept of an email thread in
  this pipeline (`main.py` processes individual files, not conversations). Deferred; the
  function exists on `jj/email-ai` if a thread view is ever wanted.
- Does not change `email_ai.py` in any way. Any disagreement with its categories, prompt
  wording, or `ActionItem` shape is JJ's call, on his branch.
- Does not surface these fields in the dashboard — see `streamlit-display-spec.md`.
