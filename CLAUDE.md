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
./.venv/bin/python main.py                       # process inbox -> SQLite -> archive/
./.venv/bin/python query_db.py                   # inspect records
./.venv/bin/python -m streamlit run app.py       # dashboard on :8501
./.venv/bin/python evaluation/run_eval.py        # per-field accuracy vs ground truth
```

Use `./.venv/bin/python`, not bare `python3`. Dependencies are not pinned; there is no
`requirements.txt` yet (a known gap, see below).

`main.py` **moves** files out of `inbox/` into `archive/` and **appends** to SQLite on every run, so
repeated runs accumulate duplicate rows. Regenerate the mocks before each run.

## Ownership

Three people, one codebase. Stay in your lane or say so first.

| Area | Files | Owner |
|---|---|---|
| Intake (Phase 1) | `email_listener.py` | Luke |
| Extraction (Phases 2-3) | `DocumentExtractor` in `main.py` | JJ |
| Validation gate (Phase 5) | `ConfidenceValidator` in `main.py` | **Neo** |
| Storage & archive (Phase 4) | `StorageManager` in `main.py` | **Neo** |
| Dashboard & approval (Phase 5) | `app.py` | **Neo** |
| Schemas | `ExtractedInvoice` etc. in `main.py` | shared contract, change by agreement |

`main.py` is a single 293-line module holding four classes, which is the main source of merge
conflicts. Splitting it along its own section banners into `extraction.py` / `validation.py` /
`storage.py` / `pipeline.py` is proposed but not agreed.

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
  scores 1.00 and passes as `Validated`. Only the amount lacks a source-text check.
- **`confidence_score` is not a confidence score.** It measures field completeness and substring
  agreement. Rename to `validation_score`.
- **The approve button has never executed.** `app.py` uses `filtered_df["ID"]` where the frame has
  lowercase `id`, raising `KeyError` and killing the whole Detail Inspector section.
- **Correct totals are already in the database**, buried in the `raw_json` blob, while
  `total_amount` reads `0.00`. On invoice 2 the model captured `"Grand Total": 2650.00` as a line
  item. Normalising into a `line_items` table recovers all three totals with no extractor change.
- Scanned/image PDFs are skipped entirely; `pypdf` needs a text layer, no OCR yet.
- No `requirements.txt`, so teammates run different dependency versions.
- `Enterprise_AI_Workflow_Briefing.docx` is tracked and binary, so git cannot merge it.

## Work in progress

Branch `evaluation-and-gate-fixes`, local only, **nothing pushed**.

- `evaluation/` measures per-field accuracy against independently transcribed ground truth. Reads
  nothing from `inbox/` or `archive/` and edits no existing file. See `evaluation/evaluation-method.md`,
  including its honest "what this does NOT measure" section (n=3, synthetic, no OCR path).
- `database-redesign-spec.md` is the Phase 4 plan: `processing_runs` / `invoices` / `line_items`,
  constraints, content-hash dedup with upsert, integer cents, versioned migrations, and a backfill
  that recovers the trapped totals. **Blocked on three decisions listed at the end of that file.**

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

**Open and blocking:** the supervisor has not confirmed whether Copilot is still a mandatory tool.
JJ built as though the answer is no. If it is yes, this becomes a hybrid, not a replacement.
