# Task Assignment and Email Intake Completion — Spec

**Author:** Luke. **For review by:** Neo (owns `storage.py`, `migrations/`, `app.py`).
**Status:** proposed, not merged to `main`. Branch: `luke/team-tasks`.

Two small, additive gaps closed together because they were found together while wiring up
email intake (see `email_listener.py` on this branch).

---

## 1. `tasks.assignee` is a column nobody writes

`tasks.assignee` has existed since migration 004. Nothing in `storage.py` or `app.py` ever
sets it, so "who is working this" cannot be answered even though the schema has a place to
hold the answer.

**Change:** `StorageManager.assign_task(task_id: int, assignee: Optional[str]) -> bool`.

```python
def assign_task(self, task_id: int, assignee: Optional[str]) -> bool:
    with connect(self.db_path) as conn:
        cursor = conn.execute(
            "UPDATE tasks SET assignee = ? WHERE task_id = ?", (assignee, task_id)
        )
        conn.commit()
        return cursor.rowcount > 0
```

No schema change. `assignee` is already a plain nullable `TEXT` column with no `CHECK`, so a
free-text name is deliberately not validated against a fixed roster — this is a three-person
demo, not an org directory. Passing `None` clears an assignment, which is a legitimate
"unassign" rather than an error case.

`app.py`'s Task Queue gets an editable **Assignee** column wired to this method (see
`streamlit-display-spec.md`).

**Evidence:** `tests/test_storage.py::TestTaskAssignment` (4 tests): assigns, reassigns,
clears with `None`, and a bad `task_id` returns `False` rather than raising.

---

## 2. Email bodies and attachments had nowhere to live

Two capabilities `email_listener.py` needed (see its own changes on this branch) had no
column or table:

### `email_messages.body`

Nothing recorded what an email said, only who sent it and how many files came with it.
Added as a single nullable `TEXT` column. Nullable is not a compromise here: it means
"body not known" for every row that predates this migration, which is exactly true — nothing
backfills history that was never captured.

### `email_attachments`

Attachment-level dedup did not exist at all before this. `email_messages` already protects
against re-fetching the same **email** (keyed on Message-ID), but nothing protected against
re-saving the same **attachment** — the only guard was a filename-collision suffix on disk,
which says nothing about content. A vendor's PDF re-attached across two emails is a
legitimate second copy; the same bytes seen twice inside one email is not.

```sql
CREATE TABLE email_attachments (
    attachment_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id       INTEGER NOT NULL REFERENCES email_messages(email_id) ON DELETE CASCADE,
    filename       TEXT    NOT NULL,
    content_sha256 TEXT    NOT NULL,
    saved_path     TEXT,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (email_id, content_sha256)
);
```

Scoped to `(email_id, content_sha256)`, not a global unique on `content_sha256` alone — see
above. Mirrors `line_items`' shape: one parent, many rows, deleted and reinserted by the
parent's caller if ever needed (attachments are append-only here, so in practice they are
just inserted once).

**New `StorageManager` methods**, matching the existing `open_task()` check-then-insert
pattern rather than catching the `UNIQUE` violation:

```python
has_seen_attachment(email_id, content_sha256) -> bool
record_attachment(email_id, filename, content_sha256, saved_path=None) -> {attachment_id, was_created}
```

`record_email()` gains an optional `body` parameter. `ON CONFLICT` uses
`COALESCE(excluded.body, email_messages.body)` so a call that only re-records the envelope
(no body re-read) never blanks out a body already captured.

**Evidence:**
- `migrations/008_email_body_and_attachments.py`, dry-run and applied against the legacy
  fixture, additive (existing invoice count and totals unchanged — see the migration's own
  before/after report).
- `tests/test_migration.py::TestEmailBodyAndAttachments` (6 tests) and
  `tests/test_storage.py::TestEmailAttachments` (6 tests).
- `tests/test_migration.py::TestChain::test_migrated_matches_fresh` still passes with
  `email_attachments` added to the compared table list — a replayed migration chain and a
  fresh `storage.DDL` database still agree.

---

## Schema version

`SCHEMA_VERSION` moves 7 ? 8 for this change (migration 008). Migration 009
(`ai-document-fields-spec.md`, reviewed separately) takes it to 9. Both are on this same
branch because they were built together, but they are independently revertable: dropping
either migration's columns/table does not require touching the other's.

## What this does not do

- Does not change what `email_listener.py` searches for (still `SUBJECT "invoice"`) or
  whether it respects `\Seen` — out of scope for this spec.
- Does not add a UI for picking an assignee from a roster. Free text, as noted above.
- Does not deduplicate attachments *across* emails. Discussed and rejected above: it is not
  a duplicate, it is a second delivery of the same document.
