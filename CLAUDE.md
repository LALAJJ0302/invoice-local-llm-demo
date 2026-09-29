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

### First run on a new machine

**Verified end to end on 2026-09-24** against a clean copy with no database, no PDFs and no
`.env`: this sequence ends with 587 tests passing. Nothing else is needed and nothing here is
optional.

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
ollama serve &                                   # separate install, https://ollama.com
ollama pull llama3.2
./.venv/bin/python generate_mock_invoices.py     # writes 3 sample PDFs to inbox/
./.venv/bin/python seed_mock_emails.py           # loads the mock mailbox into email_messages
./.venv/bin/python main.py                       # 3/3 stored in about 30s
./.venv/bin/python -m pytest tests/ -q           # 587 passed
./.venv/bin/python -m streamlit run app.py       # dashboard on :8501
```

**Do not run the migrations on a new machine.** They upgrade an existing database and a fresh
clone has none. `StorageManager.__init__` creates the whole schema at the current version on
first use, which is what `main.py` triggers. Running them anyway is harmless, they detect the
missing database and stop, but it wastes a step and suggests they are part of setup.

**`seed_mock_emails.py` is not optional.** Without it every invoice stores `email_id = NULL`,
the covering-email chip never appears on a card, and
`test_the_card_carries_everything_the_approved_design_carries` fails. It is idempotent, keyed on
the RFC 5322 Message-ID, and reads the tracked `evaluation/mock_mailbox.json`, so it needs no
mail credentials. It was missing from this list until 2026-09-24.

**The database is gitignored, so a clone has no data.** Until `main.py` has run, the dashboard
renders "No documents have been processed yet" and stops. That is correct behaviour, not a
failure. Screen tests that need a document will fail until the sequence above is complete; the
suite no longer dies at collection, which it did before 2026-09-24.

**The dashboard asks you to sign in.** Since Luke's authentication landed, `app.py` stops at a
login form before anything renders. The accounts are created on first run by
`auth.ensure_default_users`, so there is nothing to set up:

| Username | Password | Then |
|---|---|---|
| `luke`, `neo` or `jj` | `changeme` | The first login forces a password change |

`changeme` is `auth.DEFAULT_PASSWORD`. It is a hardcoded default in a local prototype with no
network exposure, which is acceptable here and would not be anywhere else; the report's Security
and Privacy section is where that gets discussed rather than quietly fixed.

Tests do not use this login. They seed a session through `tests/signed_in.py`, so that `app.py`
keeps exactly one way in. Verified 2026-09-28 on a clean copy: bare clone is 585 passed with 18
failing for lack of documents and no collection error, and the sequence above reaches **609
passed**.

### Everything else

Requires Ollama serving and a virtualenv.

```bash
./.venv/bin/python migrations/001_normalise.py   # one-off: flat table -> normalised schema
./.venv/bin/python migrations/002_rename_total_source.py
./.venv/bin/python migrations/003_fix_date_check.py
./.venv/bin/python migrations/004_email_and_tasks.py
./.venv/bin/python migrations/005_rename_validation_status.py
./.venv/bin/python migrations/006_storage_completion.py
./.venv/bin/python migrations/007_post_approval.py
./.venv/bin/python migrations/008_email_body.py
./.venv/bin/python migrations/009_attachment_names.py
./.venv/bin/python migrations/010_run_kind_and_threads.py
./.venv/bin/python migrations/011_email_analysis.py
./.venv/bin/python migrations/012_invoice_ai_fields.py
./.venv/bin/python migrations/013_reviewer_login.py
./.venv/bin/python migrations/014_must_change_password.py
./.venv/bin/python migrations/015_review_note.py
./.venv/bin/python main.py                       # process inbox -> SQLite -> archive/
./.venv/bin/python email_pipeline.py --threads   # analyse the stored mailbox -> SQLite
./.venv/bin/python query_db.py                   # inspect records
./.venv/bin/python -m streamlit run app.py       # dashboard on :8501
./.venv/bin/python evaluation/run_eval.py        # per-field accuracy vs ground truth
./.venv/bin/python -m pytest tests/ -q           # storage, migration, gate, retrieval and taxonomy tests
./.venv/bin/python evaluation/schema_comparison.py  # Optional 3/15 vs required 15/15
./.venv/bin/python evaluation/sentinel_comparison.py # what required fields cost
./.venv/bin/python evaluation/error_taxonomy.py --detail # classify every error already on disk
./report/build-pdf.sh report/presentation.md     # markdown -> PDF for reading offline
./.venv/bin/python reprocess.py --list           # what is stored, and is its file still there
```

Use `./.venv/bin/python`, not bare `python3`. Dependencies are pinned in `requirements.txt`.

**Docker alternative:** `docker compose up -d ollama`, `docker compose run --rm pipeline`,
`docker compose up app` runs the same pipeline without a local Python/Ollama install. See
`deployment-spec.md` and the README's "Option C". Requires `cp .env.example .env` first.
Not a production deployment story — `requirements-spec.md` still rules that out — just a
packaged way to run the same local stack.

`main.py` **moves** files out of `inbox/` into `archive/`. Since the Phase 4 redesign it **upserts**
on a content hash, so regenerating the mocks and re-running updates the same three rows instead of
accumulating duplicates. It writes to SQLite first and archives only after the commit.

The migrations are one-off and idempotent. 001 renames `workflow_records` to `workflow_records_v1`,
keeps it, and takes a `.bak` copy of the database first. Running any of them twice is a no-op.
`storage.SCHEMA_VERSION` is the authority on the current version, not this sentence. 010 is
the only one since 003 that rewrites an existing table.
**Never renumber a migration that is already on `main`.** 012 exists because Luke's
008 and 009 were written against numbers that were already taken and already applied;
the SQL was fine, the numbering was not, and a renumbered migration refuses to run on
any database that already has the original.

**It happened a second time, 2026-09-24, and the rule decided it the same way.** The review
note was written as 013 against a main that had no 013. Luke's `013_reviewer_login.py` and
`014_must_change_password.py` landed on main first, so the review note moved rather than his,
and it is now `015_review_note.py` starting at version 14. The rule is about which branch
reached main, not about who wrote what or when.

## Ownership

Three people, one codebase. Stay in your lane or say so first.

| Area | Files | Owner |
|---|---|---|
| Intake (Phase 1) | `email_listener.py` | Luke |
| Task assignment | tasks and routing, `task_dispatch.py`, `jira_client.py` | Luke |
| Deployment | `Dockerfile`, `docker-compose.yml`, `.env.example` | Luke |
| Extraction (Phases 2-3) | `DocumentExtractor` in `main.py` | **Neo + JJ** |
| Validation gate (Phase 5) | `ConfidenceValidator` in `main.py` | **Neo + JJ** |
| Storage & archive (Phase 4) | `storage.py`, `migrations/`, `query_db.py`, `reprocess.py` | **Neo + JJ** |
| Dashboard & approval (Phase 5) | `app.py` | **Neo + JJ** |
| Evaluation | `evaluation/` | **Neo + JJ** |
| RAG / real-document extraction | not yet created | **Neo + JJ** |
| Schemas | `ExtractedInvoice` etc. in `main.py` | **Neo + JJ**, tell Luke before changing |
| Email AI | `email_ai.py` | JJ, on `main` since PR #5 |
| Email pipeline | `email_pipeline.py` | **Neo + JJ**. The only file importing both `email_ai` and `storage` |

**Two tables of action items, and they are different things.** `invoice_action_items`
holds what the model found inside an invoice document; `email_action_items` holds what it
found in the message that carried it. Both were briefly called `action_items`, on two
branches at once. Neither name said which parent it belonged to, so both were renamed
before either reached `main`.

**Changed 2026-09-03.** Neo and JJ merged their lanes and now work as one on extraction,
validation, storage and the dashboard. Luke keeps a separate lane: intake and task assignment.
The PR-for-someone-else's-file rule now applies only across the Neo+JJ / Luke boundary.

`main.py` is now 606 lines holding three classes: the storage layer moved out to `storage.py` in the
Phase 4 redesign. Splitting the rest along its own section banners into `extraction.py` /
`validation.py` / `pipeline.py` is proposed but not agreed, and the case for it grows with the file.

**Luke commits as `zethio44`.** Recorded in `report/meeting-2026-09-08.md`, and worth repeating
here because `git log` shows a name that appears nowhere in the ownership table. Two commits, both
2026-09-03, both already on `main`. `6ff1dbb` added the 92-line regex fallback to
`DocumentExtractor` and rewrote the extraction prompt, which together turn 3/15 into 15/15. Both
are Neo+JJ's lane by the table above, and they were pushed straight to `upstream/main`.

## Current state (2026-09-12)

**The pipeline runs and now extracts correctly, for reasons worth separating.** Measured on the
project's own mock invoices:

```
./.venv/bin/python evaluation/run_eval.py                  10/15 (66.7%)   the model alone
./.venv/bin/python evaluation/run_eval.py --with-fallback  15/15 (100%)    shipped behaviour
Gate: 3/3 Validated, one of them at 0.85 because it returned no line items
```

**Both numbers are true and the report needs both.** The gap between them is our own code
repairing the model's output: a regex fallback for the header fields and a caption stripper for
the vendor. `run_eval.py` discovers every repair by naming convention and switches them all off
by default, so the model can always be measured alone.

Four states have been measured, all reproducible. See `evaluation/evaluation-method.md`:

| State | Overall | What it is |
|---|---|---|
| 2026-08-26 baseline | 3/15 | Previous prompt, `Optional` schema, no field fallback |
| Model alone, 2026-09-03 | 10/15 | Current prompt, every repair off |
| Shipped, 2026-09-03 | 15/15 | Current prompt plus the repairs |
| Required fields | 15/15 | `schema_comparison.py`. Measured, **still not shipped** |

~~The prompt accounts for the first gap, our repairs for the second, the schema for the fourth.~~
**Superseded 2026-09-08 by `evaluation/prompt_schema_2x2.py`.** Attributing the gap to the prompt
was a single comparison reported with more confidence than its design supported. The full two-by-two
on `llama3.2:latest`:

```
ORIGINAL prompt + Optional schema     6/15
ORIGINAL prompt + Required schema    15/15
IMPROVED prompt + Optional schema    15/15
IMPROVED prompt + Required schema    15/15
```

**Either fix alone reaches the ceiling. They are not additive.** Frame this as a more careful
measurement producing a better result, not as a correction of something false.

The 6/15 here and the 3/15 in the table above both reproduce, and the difference is entirely
`currency`. Traced and resolved in `report/section-5-evaluation.md` §5.4.3: `schema_comparison.py`
imports the real `ExtractedInvoice`, which carries a sixth field, `items`, a nested list. The
two-by-two defined its own five-field schema without it. Adding that nested list costs three
scalar field-values, and the field lost is the last scalar before `items` in declaration order.
**3/15 is the shipped figure and the one the report quotes**; 6/15 is a controlled variant that
exists only to isolate that effect.

**The schema is still `Optional` with defaults** in `ExtractedInvoice`. The 15/15 comes from
repairs layered on top, not from fixing the root cause. `schema_comparison.py` isolates the
schema as a cause with one variable changed. That decision is still open.

### Other confirmed defects

- ~~**The gate never verifies `total_amount`.**~~ **Fixed 2026-08-28.** See
  `validation-gate-spec.md`. The gate now checks the amount against the document in three tiers
  (`verified` / `present` / `absent`) and refuses to mark anything `Validated` unless the amount
  sits within two lines of a grand-total label. `999999.99` now scores 0.75 and goes to review.
  Note the weights changed, so `validation_score` is not comparable across that date: the samples
  moved from 0.40 to 0.25. The automation pass rate is unchanged at 0/3.
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

**Where the branches are, 2026-09-12.** `origin` is Neo's fork; `upstream` is JJ's repo.

```
main == upstream/main == 6ff0ddc      unmoved since 2026-09-08 (PR #2)
origin/main                            9 behind upstream, deliberately left alone
neo/gate-verification (current)        3 commits, NOT pushed, 271 tests green
upstream/jj/email-ai                   email_ai.py, 521 lines, NOT in main
```

PR #1 (2026-09-03) and PR #2 (2026-09-08) are both merged. The five decisions that kept PR #1 open
are on `main`; migration 005 and the `status` / `approval_status` split are live rather than
provisional, and reverting any of them costs a migration rather than a branch delete.

**JJ's `email_ai.py` is still not in `main`.** Its branch last moved on 2026-09-08 and that commit
only merged `main` into itself, so it added nothing. It runs locally on `neo/email-ai-integration`
at 13.4s for 3 calls. Its thread summary returned the subject line instead of a summary, which is
the first labelled failure case the group has for summarisation.

**`email_listener.py`, his assigned lane, was written by JJ in the initial prototype.** That
was still true on 2026-09-12 and the sentence that used to sit here, "Luke has authored no commit
under that name", is not: he has been committing steadily since 2026-09-03.

**Contributor counts are deliberately not written here any more.** The figure recorded on
2026-09-12 was `zethio44` 2, which was accurate then and had decayed sevenfold by 2026-09-28.
Count it when you need it, and prefer the first-parent history, because that is what separates a
direct push from a reviewed one:

```bash
git log --all --author=zethio44 --oneline | wc -l          # everything they authored
git log --first-parent --pretty="%h %an %s" upstream/main   # what landed without review
```

On 2026-09-28 that showed fifteen commits, of which two, both on 2026-09-03, were pushed
straight to main and thirteen arrived through pull requests.

**Current direction, agreed 2026-09-03:** Luke takes task assignment. Neo and JJ work together
on RAG, feeding real invoice PDFs rather than the three generated samples.

### Measured since 2026-09-03, none of it shipped

| Script | Question it settles | Result |
|---|---|---|
| `prompt_schema_2x2.py` | Is it the prompt or the schema? | Either alone reaches 15/15, not additive |
| `model_compatibility.py` | Can other local models do constrained JSON? | 5 measured, all 5 can |
| `retrieval_eval.py` | Sender vs keyword vs hybrid? | Keyword is 1.00 on threads, **0.00 on vendors** |
| `gate_verification_preview.py` | What would the gate amendment change? | `date`/`currency` move to `verified`, invented values to `absent` |
| `error_taxonomy.py` | Are the errors equally dangerous? | `invented: 3` is really 1 invention + 2 mislocations |
| `sentinel_comparison.py` | Does nullable-required satisfy both rules? | Fewest errors of any arm (4), but **still invents** |

**The one invention no schema can fix.** Every required-family arm returns `2000.00` as the
total of a statement that states no total, because the document shows `1,200.00` and `800.00`
and the model adds them. It is arithmetic, not hallucination, and the sum is wrong in kind
anyway since a payment received should be subtracted. A fabricated string can be caught by
asking whether it appears on the page; a computed value defeats that test by construction. This
belongs to the gate, and the gate does not catch it either: `reconcile` awards `plausible` when
the total exceeds the line-item sum, and here it equals the sum of two numbers that are not
addends.

**The taxonomy's unplanned result is the most useful one for the report.** It prices the schema
trade rather than scoring it. Optional arms fail 100% by omitting, so nothing false is stored.
Required arms omit nothing but produce 6 placeholders, 2 mislocations and 1 invention. Similar
accuracy, different safety. That is the argument for `Optional[str]` with no default.

**Before real documents arrive, `evaluation/ground_truth.json` covers only the three synthetic
files.** Adding real invoices without extending it means running experiments with no way to
tell whether they helped.

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
- Changes to Luke's files go via pull request with evidence, not direct commits.
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
