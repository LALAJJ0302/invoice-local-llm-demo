# Spec: storing the AI module's email and thread analysis

**From:** Neo (storage and migrations). **Status:** v2, 2026-09-17. **Implemented.**
Migrations 010 and 011 are applied, the storage API is written, and both are tested.
Branch `neo/email-analysis-storage`, not pushed.

**v1 (2026-09-08)** asked JJ four questions and refused to build until they were answered.
**JJ answered all four on 2026-09-17** and shipped the module changes. `email_ai.py` is now on
`upstream/main` via PR #5, so its record models are shared code rather than a side branch.

This version keeps the reasoning from v1 that still holds (§1, §2), replaces the proposed
tables with the settled ones (§4), and adds the migration plan, the storage API and the tests
(§5 to §8).

**Both decisions in §9 were taken as recommended.** One `action_items` table, and the
`processing_runs` rebuild. Luke's answer in §9 is still outstanding and does not block the
schema, only what `thread_source` can say.

The data dictionary for the four tables now lives in `database-spec.md` §4.7 to §4.10, which is
the single source of truth for the data layer. This document keeps the reasoning; that one keeps
the shape.

---

## 0. What JJ settled

| v1 question | JJ's answer | Effect here |
|---|---|---|
| The `category` label list | `Project update`, `Meeting`, `Invoice`, `Quotation`, `Issue`, `Other` | CHECK constraint can be written |
| Is `deadline` always a date? | No. Raw wording only, storage may normalise | Two columns: `deadline_text` and nullable `deadline_date` |
| Are `latest_decisions` / `outstanding_actions` lists? | Lists, not blobs | Both become child rows, not delimited strings |
| Is `run_id` acceptable? | Yes, `model_name` removed from both records | `run_id` is the join to `processing_runs` |

He also renamed `evidence` to `evidence_quote`, added a three-attempt evidence retry, and now
returns `validation_status`, `validation_reason` and `attempt_count` on every record.

### Two things in his message that the code does not match

Worth raising with him, because both change what the constraints can say.

**1. `validation_status` has only two values in the code, not three.** His message asks for
`Validated`, `NeedsReview` and `Failed`. `email_ai.py:169` declares
`Literal["Validated", "NeedsReview"]` on all four outcome and record models. `Failed` cannot be
produced by his module today.

That is the right behaviour and it should stay. The whole point of his change is that a failed
analysis is **retained** rather than dropped, and a retained result is exactly `NeedsReview`.
`Failed` means "no analysis exists", which is not a row.

**The database will allow all three anyway**, matching `storage.VALID_VALIDATION_STATUSES` and
the `invoices` table, with `Failed` reserved for a future caller that stores a row for an email
the model never returned parsable JSON for. Storage will not invent it.

**2. `attempt_count` is already capped at 3 in Pydantic** (`le=MAX_EVIDENCE_ATTEMPTS`, four
places). The database should **not** repeat that cap. See §4.5.

---

## 1. Why not extend `tasks` (unchanged from v1)

Confirmed: `tasks.invoice_id` is `INTEGER NOT NULL REFERENCES invoices(invoice_id)`.

The obvious extension is to make it nullable and add `email_id`, giving one table for all work.
**Rejected**, for two reasons.

**A nullable foreign key needs a rule that nothing enforces well.** "Exactly one of `invoice_id`
or `email_id` is set" is expressible as a CHECK, but every query then has to remember which kind
of row it is looking at, and the day someone forgets is the day an invoice task is counted as an
email action.

**They are not the same thing.** An invoice task is *"a person must approve this document"*: it
has a type from a fixed list, it belongs to one document, and it closes when that document is
approved. An email action item is *"someone committed to do X by Y"*: it has an owner, a
deadline, and the sentence it was inferred from. Different fields, different lifecycle, different
source of truth.

Forcing both into one table would mean columns that are always NULL for half the rows, which is
the shape the original flat `workflow_records` table had before Phase 4.

---

## 2. There is still no thread (unchanged from v1, and now urgent)

`ThreadAnalysisRecord.thread_id` is a required string. **Nothing in this codebase has any concept
of a thread.** `email_messages` has no `thread_id`, no `in_reply_to`, no `references`, and
`email_listener.py` reads none of those headers.

Where the value comes from today: `email_ai.create_thread_analysis_record` takes it from
`EmailThreadInput.thread_id`, which the caller supplies by hand. So it is currently whatever the
caller typed.

Two ways to get a real one, and they are not equivalent.

**From headers, correct.** RFC 5322 `In-Reply-To` and `References` define the reply chain. Needs
intake to capture them, which is Luke.

**From the subject, approximate.** Strip `Re:` and `Fwd:` and match the remainder. Needs nobody,
and wrong often enough to matter: two unrelated "Monthly statement" emails from different vendors
become one thread, and an edited subject splits one conversation into two.

**Do both and record which was used.** A `thread_source` column means a summary computed over a
guessed grouping is never reported as though it came from a real reply chain. Same reasoning as
`body_source` and `total_source`.

**Consequence for this spec:** without a `thread_id` on `email_messages`, `thread_analysis` is an
island. It stores a summary keyed to a string that joins to nothing. That is why the thread
columns are in migration 010 rather than left for later.

---

## 3. Two prerequisites the analysis tables cannot be built on top of

Both are in existing tables, and both are the reason this is two migrations rather than one.

### 3.1 `processing_runs` cannot describe an email run

```sql
threshold REAL NOT NULL CHECK (threshold BETWEEN 0 AND 1)
```

A run of the email AI has no threshold. Nothing distinguishes an email run from an invoice run
either, so `SELECT AVG(threshold) FROM processing_runs` silently mixes two different things once
email runs exist.

Three options considered.

**A. `start_email_run()` writes `threshold = 0.0`.** Free, and stores a number that means
nothing. This project puts provenance in columns rather than in someone's memory, and a
meaningless 0.0 is the opposite of that.

**B. Rebuild `processing_runs`: add `run_kind`, make `threshold` nullable, tie the two together.**
Recommended.

```sql
run_kind  TEXT NOT NULL DEFAULT 'invoice' CHECK (run_kind IN ('invoice','email')),
threshold REAL CHECK (threshold IS NULL OR threshold BETWEEN 0 AND 1),
CHECK ((run_kind = 'invoice') = (threshold IS NOT NULL))
```

The paired CHECK is the pattern already used twice in this schema:
`(state = 'Sent') = (sent_at IS NOT NULL)` on `outbound_messages`, and
`(state IN ('Done','Cancelled')) = (resolved_at IS NOT NULL)` on `tasks`.

Cost: a 12-step table rebuild on a table `invoices.run_id` points at. Migration 005 already
rebuilt a table, so there is precedent and a test (`tests/test_migration.py`) that asserts a
migrated schema matches a freshly created one.

**C. A separate `analysis_runs` table.** Keeps `processing_runs` untouched, at the price of two
run-id spaces where "run 3" is ambiguous in conversation, and where comparing one model's
invoice accuracy against its summarisation quality needs a union.

**Recommendation: B.** The point of `run_id` was to make two runs comparable. Two run tables
undoes that.

### 3.2 `email_messages` has no thread identity

Two nullable columns, additive, no row rewritten:

```sql
thread_id     TEXT,
thread_source TEXT CHECK (thread_source IS NULL OR thread_source IN ('headers','subject','manual'))
```

`seed_mock_emails.py` can backfill the mock mailbox by subject matching. Header capture stays
Luke's, and until it lands every thread in the database says `subject` and every report sentence
about threads has to say so.

`email_messages` is storage's table (migration 004, Neo). `email_listener.py` is Luke's. Adding
the columns does not touch his file.

---

## 4. The tables

Four. `email_analysis` and `thread_analysis` are the two records; `action_items` and
`thread_decisions` hold the two lists JJ confirmed are lists.

### 4.1 `email_analysis`

```sql
CREATE TABLE email_analysis (
    analysis_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id          INTEGER NOT NULL REFERENCES email_messages(email_id) ON DELETE CASCADE,
    run_id            INTEGER NOT NULL REFERENCES processing_runs(run_id),
    category          TEXT    NOT NULL CHECK (category IN
                              ('Project update','Meeting','Invoice','Quotation','Issue','Other')),
    summary           TEXT,
    validation_status TEXT    NOT NULL
                              CHECK (validation_status IN ('Validated','NeedsReview','Failed')),
    validation_reason TEXT    CHECK (validation_reason IS NULL OR validation_reason NOT GLOB '* *'),
    attempt_count     INTEGER NOT NULL CHECK (attempt_count >= 1),
    processed_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (email_id, run_id),
    CHECK ((validation_status = 'Validated') = (validation_reason IS NULL))
);

CREATE INDEX ix_email_analysis_status ON email_analysis(validation_status);
CREATE INDEX ix_email_analysis_run    ON email_analysis(run_id);
```

### 4.2 `thread_analysis`

```sql
CREATE TABLE thread_analysis (
    thread_analysis_id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id          TEXT    NOT NULL,
    thread_source      TEXT    NOT NULL CHECK (thread_source IN ('headers','subject','manual')),
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
```

**`latest_email_id` is nullable on purpose.** `ThreadAnalysisRecord.latest_message_id` is
`str | None`, because it is `thread.messages[-1].message_id` and `EmailMessageInput.message_id`
is itself optional. A thread analysed from text that was never in the mailbox has nothing to
point at. Storage resolves it when it is there and leaves NULL when it is not, rather than
refusing the row.

### 4.3 `action_items`, and the decision Neo has to make

JJ's `ActionItem` is one Pydantic class used in two places: `EmailAnalysis.action_items` and
`ThreadSummary.outstanding_actions`. Same four fields either way.

**Option 1, two tables** (`email_action_items`, `thread_outstanding_actions`), identical columns.
Every future change to `ActionItem` costs two migrations, and "which actions does Neo owe" needs
a UNION.

**Option 2, one table with two nullable parent keys and an exactly-one CHECK.** Recommended.

```sql
CREATE TABLE action_items (
    action_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id        INTEGER REFERENCES email_analysis(analysis_id) ON DELETE CASCADE,
    thread_analysis_id INTEGER REFERENCES thread_analysis(thread_analysis_id) ON DELETE CASCADE,
    item_no            INTEGER NOT NULL,
    task               TEXT    NOT NULL,
    owner              TEXT,
    deadline_text      TEXT,
    deadline_date      TEXT    CHECK (deadline_date IS NULL OR
                                deadline_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    evidence_quote     TEXT    NOT NULL,
    CHECK ((analysis_id IS NULL) <> (thread_analysis_id IS NULL)),
    -- A normalised date can only exist where there was wording to normalise. This is what
    -- stops storage from producing a deadline with nothing behind it.
    CHECK (deadline_date IS NULL OR deadline_text IS NOT NULL)
);

CREATE UNIQUE INDEX ux_action_items_email  ON action_items(analysis_id, item_no)
    WHERE analysis_id IS NOT NULL;
CREATE UNIQUE INDEX ux_action_items_thread ON action_items(thread_analysis_id, item_no)
    WHERE thread_analysis_id IS NOT NULL;
CREATE INDEX ix_action_items_owner ON action_items(owner);
```

**This looks like the nullable foreign key §1 rejects, and the difference is worth stating.**
§1 rejects a union of two things that need *different columns*, which produces rows half full of
NULLs. Here the two parents hold the *same* row shape, defined once as one Pydantic class. The
NULL is in the parent key alone, and the CHECK makes exactly one of them true. Merging identical
rows is normalisation; merging different rows is the flat table Phase 4 undid.

If you disagree, Option 1 is the safe call and costs a second CREATE TABLE.

**`state` is deliberately absent.** v1 proposed `state IN ('Open','InProgress','Done','Cancelled')`
on action items. Dropped, for a reason found while writing the upsert: re-running an analysis
deletes the children and re-inserts them, the way `save_invoice` does with `line_items`. A `state`
column would be silently reset to `Open` on every re-run, so a person's completed work would
disappear because a model was re-run. `action_items` is a record of what the model said, and it is
reproducible from the run. Lifecycle belongs with `tasks` and the outbox, once §9 is decided.

### 4.4 `thread_decisions`

```sql
CREATE TABLE thread_decisions (
    decision_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_analysis_id INTEGER NOT NULL
                       REFERENCES thread_analysis(thread_analysis_id) ON DELETE CASCADE,
    decision_no        INTEGER NOT NULL,
    decision           TEXT    NOT NULL,
    UNIQUE (thread_analysis_id, decision_no)
);
```

`latest_decisions` is `list[str]`, so one row per string. `decision_no` preserves order, which
rows otherwise lose and which a list has. Same shape as `line_items.line_no`.

### 4.5 Three constraints that are deliberately looser than they could be

**`attempt_count >= 1`, not `BETWEEN 1 AND 3`.** The cap already exists in four places in
`email_ai.py` as `le=MAX_EVIDENCE_ATTEMPTS`. Copying it into a CHECK means the day JJ raises the
constant to 5, every insert fails and the fix is a migration. `invoice_date` carries the scar of a
constraint written tighter than the data. One cap, in his module, where the retry loop is.

**`validation_reason` is not constrained to a value list.** Two codes exist today,
`empty_evidence_quote` and `evidence_quote_not_found`, and a third will appear the first time the
validator learns a new failure. The CHECK only enforces that it is a code and not a sentence, by
rejecting spaces. That is enough to keep `GROUP BY validation_reason` countable, which is the
whole reason JJ asked for a separate reason field.

**`category` is constrained**, because it is model output rather than code output. An LLM will
drift to "Project Update" or "Meeting request" and a free-text category cannot be aggregated
across runs. If JJ adds a seventh label it is a one-line migration, and it should be one, because
the label list is a shared contract.

---

## 5. `deadline_date`: what storage will and will not normalise

JJ sends the original wording and does not convert. Storage populates `deadline_date` **only when
the date is unambiguous**, and leaves NULL otherwise.

| Wording | `deadline_date` | Why |
|---|---|---|
| `2026-09-30` | `2026-09-30` | Already ISO. Returned untouched, never re-parsed |
| `by 30 September 2026` | `2026-09-30` | Month named, no ordering ambiguity |
| `September 30, 2026` | `2026-09-30` | Same |
| `30/09/2026` | **NULL** | Day-month order is not knowable from the string. AU and US disagree |
| `by Friday` | **NULL** | Needs an anchor date and a timezone. Which Friday is a guess |
| `end of month` | **NULL** | JJ's own example |
| `ASAP`, `EOD` | **NULL** | Not a date |

**The ISO passthrough is not an optimisation, it is a bug fix written before the bug.**
`dateutil.parser.parse('2026-03-12', dayfirst=True)` returns **3 December**, because `dayfirst`
is applied to the last two components whatever the shape of the string. This was found on
2026-09-12 in the error taxonomy work. Any date normaliser in this project returns already-ISO
values untouched, and this one will have a test that fails if it does not.

**Slash dates staying NULL is a deliberate choice worth reporting.** The share of action items
where `deadline_text IS NOT NULL AND deadline_date IS NULL` is a measurable number: it says how
often the model found a deadline that no system can act on. That is a better report line than a
normaliser that guesses and is right most of the time.

---

## 6. Storage API

Five methods on `StorageManager`, plus one module-level helper.

```python
def start_email_run(self, model_name: str) -> int
def save_email_analysis(self, record: Mapping[str, Any]) -> Dict[str, Any]
def save_thread_analysis(self, record: Mapping[str, Any], *, thread_source: str) -> Dict[str, Any]
def email_analysis_for(self, message_id: str, run_id: Optional[int] = None) -> Optional[Dict]
def thread_analysis_for(self, thread_id: str, run_id: Optional[int] = None) -> Optional[Dict]

def coerce_deadline_date(text: Optional[str]) -> Optional[str]   # module level, per §5
```

**Storage will not import `email_ai`.** `storage.py`'s docstring says it is deliberately free of
any dependency on `main.py`, and the same reasoning applies harder here: `email_ai.py` does
`from ollama import chat` at module scope, so importing it would make the storage layer, the
migrations and most of the test suite require Ollama to be installed.

So the methods take a plain mapping. The caller does `record.model_dump(mode="json")`, which is
one line at the integration point and keeps Pydantic on JJ's side of the boundary. The same
pattern `save_invoice` already uses for extracted invoice data.

**They take `message_id` and `thread_id`, not internal ids.** Storage resolves `message_id` to
`email_id` itself, exactly as `email_for_attachment` resolves a file name. An unknown
`message_id` raises rather than writing an orphan, because an analysis of an email the database
has never seen is a real error.

**Upsert on `(email_id, run_id)`**, via `ON CONFLICT DO UPDATE ... RETURNING`, then delete and
re-insert the children. Identical to `save_invoice` and `line_items`. Re-running the same run
updates in place rather than accumulating, which is the defect Phase 4 fixed for invoices.

`thread_source` is a keyword argument on `save_thread_analysis` rather than a field of JJ's
record, because it describes how the grouping was formed and his module does not know.

---

## 7. Two migrations, not one

Splitting because the risky part is small and should be isolated. Each is separately revertable
and each has its own backup.

**Migration 010, prerequisites.** Rebuilds `processing_runs` per §3.1 and adds the two
`email_messages` columns per §3.2. The rebuild is the only step in either migration that rewrites
an existing table, and `invoices.run_id` points at it.

**Migration 011, the analysis tables.** Four `CREATE TABLE`s and the indexes. Purely additive,
nothing existing touched, trivially reversible by dropping four tables.

`SCHEMA_VERSION` goes 9 to 10 to 11. Both follow the house pattern in `migrations/009`: refuse
unless the version matches, `.bak` copy first, `--dry-run`, print before and after counts, and
fail loudly without committing.

**`storage.DDL` gains the same four tables and the changed `processing_runs`**, so a fresh
database matches a migrated one. `tests/test_migration.py` already asserts that and will catch a
mismatch, including column order, which is why the new `email_messages` columns must be appended
last in the DDL exactly as `vendor_source` and friends were.

---

## 8. Tests

In `tests/test_email_analysis_storage.py`, plus additions to the migration test.

- Save an analysis, read it back with `email_analysis_for`, fields match.
- Re-save the same `(message_id, run_id)`: one row, updated, children replaced not duplicated.
- Save the same message under two different `run_id`s: two rows, both readable. This is the
  model-comparison case that `run_id` exists for.
- Unknown `message_id` raises rather than writing an orphan.
- Deleting an email cascades to its analysis, its action items and its decisions.
- A seventh category is rejected by the CHECK.
- `validation_status='Validated'` with a reason is rejected, and `NeedsReview` without one is too.
- A `validation_reason` containing a space is rejected.
- `attempt_count = 5` is **accepted** by the database, per §4.5.
- An action item with both parent keys set is rejected, and with neither.
- `deadline_date` without `deadline_text` is rejected.
- `coerce_deadline_date` on every row of the §5 table, including the ISO passthrough.
- Decision order survives a round trip.
- A thread analysis with a NULL `latest_email_id` saves.
- Fresh DDL and migrated schema match, including column order.

---

## 9. Still open

**For Luke.** Can `In-Reply-To` and `References` be captured at intake? Until then
`thread_source` is `subject` on every row, and every thread claim in the report has to say so.

**For the group, and out of scope for these two migrations.** `outbound_messages.invoice_id` is
`NOT NULL`, so an email action item cannot queue a notification today. v1 said the two kinds of
work should share the outbox and that is still right, but it needs `invoice_id` nullable with an
exactly-one CHECK, which is a third migration and a decision about whether email actions notify
anyone at all. Recording it here so it is not rediscovered later.

**For Neo. Answered 2026-09-17, both as recommended.** §4.3 is one `action_items` table with two
parents. §3.1 is option B, the `processing_runs` rebuild.

---

## 10. What was executed rather than asserted

The constraints in §4 were run against an in-memory SQLite database on 2026-09-17 before this
spec was circulated. Fourteen inserts, each one predicted first: `Validated` with a reason,
`NeedsReview` without one, a reason containing a space, `attempt_count` of 0, an action item with
both parents, with neither, with a date and no wording, and a duplicate `item_no` under the same
parent were all rejected. `attempt_count = 5` and the same `item_no` under a different parent were
both accepted, which is what §4.5 and the partial indexes intend. All fourteen matched.

The `dateutil` behaviour quoted in §5 also reproduces on this machine today:
`parser.parse('2026-03-12', dayfirst=True)` returns `2026-12-03`.


---

## 11. What was built, and what changed while building it

Migration 010 (`run_kind`, nullable `threshold`, the two `email_messages` thread columns) and
migration 011 (the four tables) are applied to `workflow_platform.db`, which is now at schema
version 11. `storage.py` gained `start_email_run`, `save_email_analysis`, `save_thread_analysis`,
`email_analysis_for`, `thread_analysis_for` and `coerce_deadline_date`.

Three things came out of building it that the spec above did not predict.

**The two migrations were overwriting each other's backups.** Every migration in this project
stamps its `.bak` with `%Y%m%d-%H%M%S`. Run two back to back, which is exactly what the quickstart
tells a teammate to do, and both land in the same second: the second copy silently replaces the
first, so the state before the pair is gone. 010 and 011 now add a counter when the name is taken.
**001 to 009 still have this**, recorded in `database-spec.md` §6 and §8.6. Found by running them,
not by reading them.

**`email_ai.py` is on `upstream/main`**, merged as PR #5 while this spec was being written, so the
record models are shared code rather than a side branch. That is what made the last test below
possible.

**The tests round-trip his real Pydantic objects, not just a transcription of them.**
`tests/test_email_analysis_storage.py::TestAgainstTheRealRecords` builds real
`EmailAnalysisRecord` and `ThreadAnalysisRecord` values, calls `model_dump()` and feeds the result
straight to storage, so a rename on his side fails here rather than in the group's integration. It
also asserts that the six categories in his `Literal` and the six in the CHECK constraint are the
same set. The class skips where `ollama` is not installed, which is the reason storage itself must
never import that module.

**A defect the real model found, after the spec was written.** Asked for a nullable string,
`llama3.2` answered `deadline_text` with the four-character string `"null"` on a real message
from the mock mailbox. `normalise_owner` in `email_ai.py` already maps that to None, but it is
attached to `owner` alone, so it reached storage as text. `'null'` is not NULL, so the
`undated_count` §5 calls a number worth reporting would have been wrong every time it happened.
`storage.absent_string` now maps exactly the four values his validator maps, and a test fails if
he widens his set without storage following. **The clean fix is one line on his side**: add
`deadline_text` to the `field_validator` that already covers `owner`. Storage keeps its copy
regardless, because a record can reach it from somewhere other than his models.

**What still has to happen on JJ's side:** call `start_email_run()` once per run, then
`save_email_analysis(record.model_dump(mode="json"))` per email and
`save_thread_analysis(record.model_dump(mode="json"), thread_source=...)` per thread. Nothing
writes to the four tables until he does, and they are empty by design until then.
