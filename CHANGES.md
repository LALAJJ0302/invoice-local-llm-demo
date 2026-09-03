# Changes on `neo/database-redesign`

**From:** Neo · **Branch:** `neo/database-redesign` · schema version 7 · 194 tests passing

_Commit and file counts are deliberately not written here: the PR shows them live and they go stale
the moment anything is pushed._

```
https://github.com/NattakritPitayasiri/invoice-local-llm-demo/tree/neo/database-redesign
```

**PR #1** https://github.com/LALAJJ0302/invoice-local-llm-demo/pull/1

**Approved by JJ.** He asked for two things before merge, both addressed:

- *"Make sure CHANGES.md reflects the current PR state"* — this file was written before migrations
  006 and 007 and before the report work. Brought up to date, and the six decisions at the end are
  now numbered correctly (there were six under a heading that said five).
- *"Keep the extraction accuracy fix as a separate PR"* — agreed, and it was never in this one.
  `ExtractedInvoice` and `DocumentExtractor` are untouched here. The evidence for that fix is in
  `evaluation/schema_comparison.py`, ready to go as its own PR whenever JJ wants it.

Still not merging without a word from Luke, since the `.docx` untracking and the `requirements.txt`
pin affect his setup too.

Everything here was verified by running something. Commands are included so you can check any of it.

## After you pull

```bash
./.venv/bin/python -m pip install -r requirements.txt   # adds pytest and python-docx
for m in migrations/00*.py; do ./.venv/bin/python $m; done   # backs your .db up first, safe to re-run
./.venv/bin/python main.py
./.venv/bin/python -m pytest tests/ -q
```

---

## Files I did NOT touch

Listing these first, because it is the part that matters for review.

| File | Owner |
|---|---|
| `email_listener.py` | Luke |
| `DocumentExtractor` in `main.py` | JJ |
| `ExtractedInvoice`, `InvoiceItem`, `ProcessedRecord` | shared contract |
| `generate_mock_invoices.py`, `test_ollama.py` | unchanged |

`ExtractedInvoice` is the cause of the 3/15 extraction result and it is **unchanged**. That fix
belongs to JJ and is offered as a PR, not applied across the boundary.

---

## New files

### Storage layer

| File | Lines | What it does |
|---|---|---|
| `storage.py` | 831 | The whole data layer: schema, content-hash upsert, cents conversion, summary-row detection, total recovery, reconciliation, document classification, email, task and outbox APIs |
| `reprocess.py` | 173 | Restore a file from `archive/` to re-run it, delete a row, list runs that crashed |
| `requirements.txt` | 27 | Every direct dependency pinned |

### Migrations, run in order, each backs up first and is safe to re-run

| File | Lines | What it does |
|---|---|---|
| `migrations/001_normalise.py` | 357 | Flat table → `processing_runs` / `invoices` / `line_items`, and recovers the totals trapped in `raw_json` |
| `migrations/002_rename_total_source.py` | 146 | `extraction_source` → `total_source` |
| `migrations/003_fix_date_check.py` | 189 | Repairs a CHECK constraint that rejected every valid date |
| `migrations/004_email_and_tasks.py` | 171 | Adds `email_messages` and `tasks`, plus `invoices.email_id` |
| `migrations/005_rename_validation_status.py` | 138 | `status` → `validation_status` |
| `migrations/006_storage_completion.py` | 171 | Adds `reconciliation`, `vendor_source`, `document_type`, backfilling the first two from stored data |
| `migrations/007_post_approval.py` | 186 | Extends `task_type` with `Payment` and `File`, adds `outbound_messages` |

### Tests

| File | Lines | Covers |
|---|---|---|
| `tests/test_storage.py` | 830 | Money, summary rows, total recovery, hashing, upsert, constraints, email, tasks, reconciliation, classification, the post-approval hand-off, the outbox |
| `tests/test_migration.py` | 446 | All seven migrations, and that a migrated database matches a fresh one column for column |
| `tests/test_validation_gate.py` | 169 | Hallucinated totals, line-item totals, number comparison |

### Evaluation

| File | Lines | What it does |
|---|---|---|
| `evaluation/schema_comparison.py` | 179 | Controlled test: Optional schema 3/15 vs required schema 15/15 |
| `evaluation/sentinel_comparison.py` | 233 | What required fields cost: invented values on 3 of 9 absent fields, and why a sentinel does not help |
| `evaluation/run_eval.py` | 151 | Per-field accuracy against hand-transcribed ground truth |

### Documentation

| File | Lines | What it is |
|---|---|---|
| `database-spec.md` | 541 | **Single source of truth for the data layer.** ER diagram, field-by-field data dictionary, twelve design decisions with the evidence behind each, migration history, and what the database does and does not prove |
| `requirements-spec.md` | 184 | Functional and non-functional requirements with an honest status on each, plus assumptions and risks |
| `validation-gate-spec.md` | 167 | The amount-verification fix and the float bug its own tests found |
| `storage-completion-spec.md` | 166 | Migrations 006 and 007, and why each column exists |
| `team-sync-notes.md` | 311 | Meeting agenda and the decisions the group owes |
| `report/` | — | Final report outline with an evidence map, a study guide, an account of the AI collaboration, and a markdown-to-PDF script. Neo's assignment work, not part of the pipeline |

---

## Edited files

### `main.py` +223 / -93

| Class | Change |
|---|---|
| `ConfidenceValidator` | Rewritten. Verifies the total against the document in three tiers, adds a hard rule that nothing is `Validated` without a verified amount, rebalanced weights, new `explain()` |
| `DownstreamDispatcher` | Writes a row to `tasks` and queues an `outbound_messages` row, instead of printing a fake Teams/Jira line that vanished with the terminal |
| `WorkflowOrchestrator` | Opens and closes a run, hashes before archiving, **commits to the database before moving the file**, passes the document text through so it can be classified |
| `StorageManager` | Removed, moved to `storage.py` |

### `app.py` +224 / -55

- Reads the new schema, converts `total_cents / 100.0` at the display edge
- **Fixed `filtered_df["ID"]` → `["id"]`**, the KeyError that had been killing the entire Detail Inspector
- Approve and Reject write `approval_status` and `reviewed_at` only. They no longer overwrite the
  pipeline's verdict or reset the score to `1.0`
- New Task Queue table and an Open Tasks metric
- Line items come from the `line_items` table instead of being unpacked from `raw_json`
- Shows `document_type` and `reconciliation`, and warns when a stated total is **less** than the
  line items add up to, which tax and shipping cannot explain
- Approving opens the follow-on task and queues its notification

### `query_db.py` +48 / -24

Reads `invoices`, prints cents as money, shows `total_source` and `reconciliation` so a derived or
unreconciled total is visible at a glance.

---

## Bugs fixed

| Bug | How it was found |
|---|---|
| `filtered_df["ID"]` KeyError, so the approve button had never once executed | JJ reported it |
| Approving overwrote `confidence_score = 1.0`, destroying the measurement | Code review |
| File was archived *before* the database write, so a failed insert lost the file with no record | Code review |
| The gate never checked the total against the document: a fake `999,999.99` on a $1,500 invoice scored `1.00` and passed as `Validated` | Code review |
| Deduplicating on a file-byte hash would have deduplicated nothing, because ReportLab writes a random `/ID` into every PDF | Measured before writing the code |
| `invoice_date GLOB '____-__-__'` rejected every valid date — `_` is not a wildcard in GLOB, that is `LIKE` | Test suite, first run |
| Migrations 002 to 004 reported failure when re-run, so running the sequence twice looked broken | Test suite, first run |
| `abs(1500.0 - 1500.01) < 0.01` evaluates to true, so a near miss compared equal in the money check | Test suite, first run |
| `ALTER TABLE ADD COLUMN` can only append, so a fresh database and a migrated one ended up with different column orders, changing what `SELECT *` returns | Test suite |
| The claim that required fields fix extraction had been repeated for days with nothing in the repo able to reproduce it | Asked how it was known |

---

## Result

| | Before | After |
|---|---|---|
| Stored invoice totals | 0.00 / 0.00 / 0.00 | 1500.00 / 2650.00 / 2350.00, matching ground truth |
| Re-running the pipeline | appends duplicate rows | updates the same row (4 runs, 3 rows) |
| Extracted totals | 0/3 | **0/3, unchanged** |
| Extraction accuracy | 3/15 (20%) | **3/15 (20%), unchanged** |
| Automation pass rate | 0/3 | 0/3, unchanged |
| Hallucinated total `999,999.99` | scored `1.00`, passed as `Validated` | scores `0.75`, goes to review |
| Total against line items | never compared | `reconciliation` on every row, `exact` on all three |
| After approval | nothing happened | opens the work that follows, routed by document type |

The recovered totals are real, but they were derived by summing line items the model extracted
correctly, **not extracted by the model**. Every one is tagged `total_source = 'fallback'` so this
cannot be misread. Nothing in this branch improves extraction, and it should not: that is a
separate change in JJ's lane.

---

## Six decisions, not just code

I implemented them because they were blocking other work. Say so if you disagree; each is one
migration or one commit to reverse.

1. **Approval now hands off instead of terminating.** Approving an `Invoice` opens a `Payment`
   task, a `Receipt` opens `File`, an `Unknown` opens `Review`, and rejecting opens nothing.
   Preparation only: `outbound_messages` records what should be sent and every row stays `Pending`.
   Nothing in the codebase contacts Teams, Jira or Planner.
2. **`status` split from `approval_status`.** Approving no longer overwrites the pipeline's verdict.
   A row can now read `NeedsReview` and `Approved` at once, which is intended: the extractor was not
   confident and a person accepted it anyway.
3. **`status` renamed to `validation_status`.** `model_result_status` was considered and rejected,
   because the value comes from our gate thresholding its own score, not from the model.
4. **A `Validated` document still creates an approval task.** Automation reduces the reading, it
   does not remove the approval.
5. **The `.docx` is untracked.** Git cannot merge a binary, and a `.docx` gets a new hash on every
   generation even with identical content. `generate_briefing_docx.py` rebuilds it.
6. **The gate's score scale changed.** The samples moved from 0.40 to 0.25 because the weights
   changed. `validation_score` is therefore not comparable across this branch. Use the automation
   pass rate for any before-and-after, which is stable at 0/3.

---

## Next

**Everyone** — push your branches.

**Group** — decide how we report the extraction result. My argument: fix it and report both numbers.
The strongest thing we have is not a high accuracy figure, it is that we found a non-obvious root
cause, isolated it with a controlled comparison, and fixed it.

**JJ** — say the word and I will open the `ExtractedInvoice` PR with `evaluation/schema_comparison.py`
as the evidence. Worth knowing first: making fields required means the model can no longer stay
silent, so on a document where a field genuinely is absent it may guess instead.

**Luke** — four lines in `email_listener.py` to record fetched emails. The API is written and
tested, just not called:

```python
result = storage.record_email(
    message_id=msg["Message-ID"], sender=msg["From"],
    subject=msg["Subject"], received_at=msg["Date"],
    attachment_count=len(attachments))
if result["already_seen"]:
    continue          # never re-download the same email
```

One piece I deliberately did not design: how a saved file links back to its `email_id`. A sidecar
file or a staging table both work. Your call.

**Me** — the three items that were here (reconcile line totals, `document_type`, a provenance column
for `vendor_name`) are **all done**, in migrations 006 and 007. What is left in my lane is
deliberately deferred: the gate's checks on `invoice_number` and `vendor_name` are still substring
tests, and hardening them now would mean fitting more rules to three files we generated ourselves.
That waits for real documents. `database-spec.md` §8.1 records it as a stopping point rather than an
omission.
