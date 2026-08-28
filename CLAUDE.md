# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

A local-first invoice processing prototype for a UTS Industry Project (group of three).
Originally a Microsoft cloud design (SharePoint, Power Automate, Copilot, Power BI); de-scoped to a
local open-source stack on supervisor advice after hitting UTS tenant permission limits.

```
Gmail (IMAP) -> email_listener.py -> inbox/ -> main.py (pypdf + Ollama llama3.2)
             -> SQLite workflow_platform.db -> Streamlit app.py
```

**Important framing:** this project does not build or train a language model. It builds a pipeline
that *uses* an existing local model. "We built a local LLM" and "we ran a local LLM" are very
different claims.

## Setup and run

Requires Ollama serving and a virtualenv (both already set up on Neo's machine).

```bash
ollama serve &                                   # must be running before main.py
./.venv/bin/python generate_mock_invoices.py     # writes 3 sample PDFs to inbox/
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python migrations/001_normalise.py   # one-off: flat table -> normalised schema
./.venv/bin/python migrations/002_rename_total_source.py
./.venv/bin/python migrations/003_fix_date_check.py
./.venv/bin/python migrations/004_email_and_tasks.py
./.venv/bin/python migrations/005_rename_validation_status.py
./.venv/bin/python main.py                       # process inbox -> SQLite -> archive/
./.venv/bin/python query_db.py                   # inspect records
./.venv/bin/python -m streamlit run app.py       # dashboard on :8501
./.venv/bin/python evaluation/run_eval.py        # per-field accuracy vs ground truth
./.venv/bin/python -m pytest tests/ -q           # 141 storage and migration tests
./.venv/bin/python reprocess.py --list           # what is stored, and is its file still there
```

Use `./.venv/bin/python`, not bare `python3`. Dependencies are pinned in `requirements.txt`.

`main.py` **moves** files out of `inbox/` into `archive/`. Since the Phase 4 redesign it **upserts**
on a content hash, so regenerating the mocks and re-running updates the same three rows instead of
accumulating duplicates. It writes to SQLite first and archives only after the commit.

The migration is one-off and idempotent. It renames `workflow_records` to `workflow_records_v1`,
keeps it, and takes a `.bak` copy of the database first. Running it twice is a no-op.

## Ownership

Three people, one codebase. Stay in your lane or say so first.

| Area | Files | Owner |
|---|---|---|
| Intake (Phase 1) | `email_listener.py` | Luke |
| Extraction (Phases 2-3) | `DocumentExtractor` in `main.py` | JJ |
| Validation gate (Phase 5) | `ConfidenceValidator` in `main.py` | **Neo** |
| Storage & archive (Phase 4) | `storage.py`, `migrations/`, `query_db.py`, `reprocess.py` | **Neo** |
| Dashboard & approval (Phase 5) | `app.py` | **Neo** |
| Schemas | `ExtractedInvoice` etc. in `main.py` | shared contract, change by agreement |

`main.py` is now 283 lines holding three classes: the storage layer moved out to `storage.py` in the
Phase 4 redesign. Splitting the rest along its own section banners into `extraction.py` /
`validation.py` / `pipeline.py` is proposed but not agreed.

## Current state (2026-08-26)

**The pipeline runs but does not work.** Measured on the project's own mock invoices:

```
vendor_name 3/3 | invoice_number 0/3 | date 0/3 | total_amount 0/3 | currency 0/3
OVERALL 3/15 (20%)   Gate: 0/3 Validated, 0% automation pass rate
```

**Root cause is the schema, not model accuracy.** Every field in `ExtractedInvoice` is `Optional`
with a default, so Pydantic emits an empty `required` list and Ollama's constrained decoder legally
omits fields; the defaults then backfill `None`, `0.0`, `"Unknown"`. The same model and prompt with
required fields returns 5/5. A controlled 2x2 isolates the schema as the causal variable.

Do not attribute these failures to the model or attempt to fix them with prompt engineering.

### Other confirmed defects

- **The gate never verifies `total_amount`.** A hallucinated total of `999999.99` on a $1,500 invoice
  scores 1.00 and passes as `Validated`. Only the amount lacks a source-text check. **This is now the
  single largest open defect.** Needs its own spec before implementation.
- **`confidence_score` is not a confidence score.** It measures field completeness and substring
  agreement. Renamed to `validation_score` in the database (Phase 4). The in-memory Pydantic field
  is still `confidence_score`, because `ProcessedRecord` is a shared contract and renaming it needs
  the group's agreement.
- ~~**The approve button has never executed.**~~ **Fixed 2026-08-26.** `app.py` used
  `filtered_df["ID"]` where the frame has lowercase `id`, raising `KeyError` and killing the whole
  Detail Inspector. Approving no longer overwrites `validation_score` with `1.0` either.
- ~~**Correct totals trapped in the `raw_json` blob.**~~ **Fixed 2026-08-26** by migration 001. All
  three stored totals now read 1500.00 / 2650.00 / 2350.00 against ground truth, recovered from
  `line_items` with no extractor change. They carry `extraction_source = 'fallback'`, because the
  model still extracts `0.00`: the *stored* totals are 3/3, the *extracted* totals are still 0/3.
- Scanned/image PDFs are skipped entirely; `pypdf` needs a text layer, no OCR yet.
- ~~No `requirements.txt`~~ **Fixed 2026-08-28.** Every direct dependency pinned.
- ~~`Enterprise_AI_Workflow_Briefing.docx` is tracked and binary~~ **Fixed 2026-08-28.** Untracked
  and gitignored; `generate_briefing_docx.py` rebuilds it. The files inside are byte-identical, but
  the `.docx` itself is not: it is a ZIP that stamps each entry with the write time, so an unchanged
  document still produces a file git reports as modified. That is the reason it cannot be tracked.
- ~~The `invoice_date` CHECK rejected every real date~~ **Fixed 2026-08-28** by migration 003.
  `GLOB '____-__-__'` has no wildcard: `_` is literal in GLOB, that is `LIKE`'s wildcard. Never fired
  because every date is NULL. Found by the test suite on the day it was written.

## Work in progress

Branches `evaluation-and-gate-fixes` and `neo/database-redesign` (the latter off the former, so it
carries the spec). Local only, **nothing pushed**.

- `evaluation/` measures per-field accuracy against independently transcribed ground truth. Reads
  nothing from `inbox/` or `archive/` and edits no existing file. See `evaluation/evaluation-method.md`,
  including its honest "what this does NOT measure" section (n=3, synthetic, no OCR path).
- `database-spec.md` is the **single source of truth for the data layer**: the schema, a
  field-by-field data dictionary, the design decisions with their evidence, migration history, and
  what the database does and does not prove. It absorbed the two earlier specs
  (`database-redesign-spec.md`, `database-completion-spec.md`), which are gone from the tree but
  remain in git history. Phase 4 is **implemented 2026-08-26** in `storage.py` and
  `migrations/001_normalise.py`; §8 lists what is left.

  One correction it records: the dedup key is a hash of the **extracted text**, not of the file
  bytes. ReportLab writes a random `/ID` into every PDF, so regenerating the mocks changes the byte
  hash while the content is identical. A byte-hash unique index would have deduplicated nothing.

- `requirements-spec.md` is the functional and non-functional requirements with an honest
  implementation status per requirement, plus assumptions and risks.
- `team-sync-notes.md` is the agenda for the group: what is done, what Neo is concerned about,
  the four decisions the group owes, and the work Neo is doing without waiting.

Deferred until the group gets there: Trigger/Approval, the Jira task, and splitting `status`
(data quality) from `approval_status` (business approval).

## How to work here

- **Spec before code.** Write the spec, get Neo's explicit approval, then implement. This is a
  standing rule, not a formality.
- **Do not push or open PRs without asking.** `origin` is Neo's fork; `upstream` is JJ's repo.
- Changes to JJ's or Luke's files go via pull request with evidence, not direct commits.
- Prefer measuring over asserting. Every claim in this file was verified by running something.

## Useful skills

- `/scrutinize` on a plan before building it, especially one Claude wrote
- `/grilling` to stress-test open design decisions
- `/code-review` on the diff before a PR goes to a teammate
- `/run` to actually launch the dashboard rather than assuming it works
- `/karpathy-guidelines` to keep changes surgical

Not applicable: `/api-spec-generator` (NestJS/Spring only, no REST API here), `/claude-api` (this
uses Ollama, not Anthropic).

## Context beyond this repo

Neo's Obsidian vault is the source of truth for project history and decisions:

- `~/Obsidian/AIOS/University/subjects/industry-project/industry-project.md` — read the
  "Where Neo left off (2026-08-26)" section first
- `.../extraction-schema-findings.md` — the measured evidence behind everything above
- `.../local-pipeline-pivot.md` — the still-unanswered question of whether Copilot is mandatory,
  which could invalidate this entire local branch

**Resolved 2026-08-28:** Copilot is confirmed **not** mandatory. The local stack stands as a
replacement for the Microsoft design, not a hybrid. This was the project's largest open unknown.
