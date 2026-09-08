# Streamlit: categories, summaries, action items, original sources — Spec

**Author:** Luke. **For review by:** Neo (owns `app.py`).
**Status:** proposed, not merged to `main`. Branch: `luke/team-tasks`.

Depends on `task-assignment-and-review-spec.md` (migration 008, `assign_task()`) and
`ai-document-fields-spec.md` (migration 009, `category`/`summary`/`action_items`). This spec
only covers `app.py`.

## 1. `load_data()` now joins `email_messages`

```sql
SELECT i.*, e.sender AS email_sender, e.subject AS email_subject,
       e.received_at AS email_received_at
FROM invoices i
LEFT JOIN email_messages e ON e.email_id = i.email_id
```

`invoices.email_id` was already selected before this change, but nothing ever followed the
FK. `LEFT JOIN`, not `JOIN`: an invoice with `email_id IS NULL` (a file dropped straight into
`inbox/`, the documented way to test without Gmail) must still appear in the table, with the
three email columns coming back `NULL`/`NaN` rather than dropping the row.

## 2. Explorer table gains a **Category** column

Straight column addition next to the existing **Doc Type** — deliberately kept as two
separate columns rather than merged, because they answer different questions: Doc Type
(`Invoice`/`Receipt`/`Unknown`) drives the post-approval task routing
(`storage.py`'s `FOLLOWUP_BY_TYPE`); Category (`email_ai.py`'s business classification) does
not drive anything yet, it is informational.

## 3. Detail Inspector gains three things

- **Category**, alongside the existing Document Type line.
- **Summary**, as a blockquote. Falls back to a caption explaining *why* it might be empty
  (the intelligence pass can fail independently — see `ai-document-fields-spec.md`) rather
  than just showing a blank.
- **Original Source**, as an expander: sender / subject / received-at when `email_id` is
  set, a caption explaining the manual-drop case when it is not, and — new — a
  **download button** for the archived file when it still exists on disk. `archive_path`
  has been stored since Phase 4 and never surfaced in the UI before this.

## 4. A new full-width **Action Items** section

Below the two-column layout (line items sit in a half-width column; a per-item row with an
owner, a deadline and a quoted source reads badly at half width). One `st.checkbox` per
action item, labelled with the task, owner and deadline when present, with the model's
`evidence_quote` shown underneath as a caption so a person can verify the claim against the
source without reopening the file.

Checking a box calls `storage.py`'s `set_action_item_done()` and reruns. This is
**explicitly not** wired to `validation_score` or `approval_status` — see the caption in the
UI. An action item is a claim about the document; ticking it off is a personal to-do, not a
data-quality or business decision.

## 5. Task Queue: editable **Assignee** column

`open_tasks_df` gains an `Assignee` column (already selected by `storage.py`'s
`open_tasks()`, just not displayed). The table itself becomes a `st.data_editor` with every
column except `Assignee` disabled, so a person can only edit the one thing this is for. Only
rows whose `Assignee` value actually changed are written back (`assign_task()`), rather than
re-writing every visible row on every rerun.

**Evidence:** `python -c "import app"` runs the whole script (bare mode; every widget
constructor still executes, just without a live session) against a real database populated
by `main.py`'s end-to-end run — no exceptions, confirming the join, the new columns and the
action-items/data-editor code paths all execute against real data. `streamlit run app.py`
smoke-tested manually (HTTP 200, screenshot not included here since this is a spec, see the
PR description for one).

## What this does not do

- Does not add a roster/dropdown for `Assignee` — see `task-assignment-and-review-spec.md`,
  same reasoning (three-person demo, free text).
- Does not let a person edit `category` or `summary` — they are the model's output, shown
  read-only, same treatment as `vendor_name` etc. elsewhere in the Explorer table.
- Does not remove or rename any existing column. Everything here is additive.
