# Spec: storing the AI module's email and thread analysis

**From:** Neo (storage and migrations). **Answering:** JJ, 2026-09-08.
**Status:** proposal, nothing implemented. Nothing lands until JJ agrees on the interface.

JJ asked whether to add dedicated `email_analysis`, `thread_summary` and
`email_action_items` tables, or extend the current schema, and noted that `tasks` requires
an `invoice_id`.

**He is right about `tasks`, and the answer is dedicated tables.** The reasoning, one
problem he has not hit yet, and four smaller decisions are below.

---

## 1. Why not extend `tasks`

Confirmed: `tasks.invoice_id` is `INTEGER NOT NULL REFERENCES invoices(invoice_id)`.

The obvious extension is to make it nullable and add `email_id`, giving one table for all
work. **Rejected**, for two reasons.

**A nullable foreign key needs a rule that nothing enforces well.** "Exactly one of
`invoice_id` or `email_id` is set" is expressible as a CHECK, but every query then has to
remember which kind of row it is looking at, and the day someone forgets is the day an
invoice task is counted as an email action.

**They are not the same thing.** An invoice task is *"a person must approve this
document"*: it has a type from a fixed list, it belongs to one document, and it closes when
that document is approved. An email action item is *"someone committed to do X by Y"*: it
has an owner, a deadline, and the sentence it was inferred from. Different fields,
different lifecycle, different source of truth.

Forcing both into one table would mean columns that are always NULL for half the rows,
which is the shape the original flat `workflow_records` table had before Phase 4.

**What they should share is the outbox.** `outbound_messages` already records a
notification decision without sending it. An email action item that needs a person told
should queue there exactly as an invoice task does, so there is one notification path
rather than two.

---

## 2. The problem JJ has not hit yet: there is no thread

`ThreadAnalysisRecord` has a `thread_id`. **Nothing in this codebase has any concept of a
thread.**

```
email_messages -> email_id, message_id, sender, subject, received_at,
                  fetched_at, attachment_count, body_text, body_source, attachment_names
```

No `thread_id`, no `in_reply_to`, no `references`. `email_listener.py` does not read those
headers, and it is Luke's file.

There are two ways to get one and they are not equivalent.

**From headers, correct.** RFC 5322 `In-Reply-To` and `References` define the reply chain.
This is what mail clients use. It needs a column, and it needs intake to capture the
headers, which is Luke.

**From the subject, approximate.** Strip `Re:`, `Fwd:` and match on the remainder. Cheap,
needs nobody, and **wrong often enough to matter**: two unrelated invoices both titled
"Monthly statement" become one thread, and a subject someone edited mid-conversation splits
one thread into two.

**Recommendation: do both, and record which was used.** A `thread_source` column
(`'headers'` or `'subject'`) means a thread summary computed over a guessed grouping is
never reported as though it came from a real reply chain. Same reasoning as `body_source`
and `total_source`.

**This is a decision JJ and Luke both need to be in**, and it is the piece most likely to
be discovered late.

---

## 3. Proposed tables

Three, as JJ suggested, with four changes.

```sql
CREATE TABLE email_analysis (
    analysis_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id      INTEGER NOT NULL REFERENCES email_messages(email_id) ON DELETE CASCADE,
    run_id        INTEGER REFERENCES processing_runs(run_id),
    category      TEXT    NOT NULL,        -- CHECK added once JJ supplies the label list
    summary       TEXT,
    processed_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (email_id, run_id)
);

CREATE TABLE email_action_items (
    action_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id   INTEGER NOT NULL REFERENCES email_analysis(analysis_id) ON DELETE CASCADE,
    task          TEXT    NOT NULL,
    owner         TEXT,
    deadline      TEXT,                    -- CHECK: NULL or YYYY-MM-DD
    evidence      TEXT,
    state         TEXT    NOT NULL DEFAULT 'Open'
                          CHECK (state IN ('Open','InProgress','Done','Cancelled'))
);

CREATE TABLE thread_analysis (
    thread_analysis_id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id          TEXT    NOT NULL,
    latest_email_id    INTEGER NOT NULL REFERENCES email_messages(email_id),
    run_id             INTEGER REFERENCES processing_runs(run_id),
    summary            TEXT,
    latest_decisions   TEXT,
    outstanding_actions TEXT,
    processed_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (thread_id, run_id)
);
```

### Change 1: `email_id`, not `message_id`

JJ's records key on `message_id`, the RFC 5322 string. Internally the foreign key should be
`email_id`, so the database enforces that the email exists and a delete cascades.

**This costs JJ nothing.** His module keeps passing `message_id`; storage resolves it,
exactly as `email_for_attachment` already resolves a file name. If a `message_id` has no
row, that is a real error and should be one, not a silently orphaned analysis.

### Change 2: `run_id` instead of `model_name` and `processed_at` per row

`processing_runs` already records the model, the threshold and the timing for a run. Adding
`model_name` to every analysis row duplicates it and lets the two disagree.

With a `run_id`, "which model produced these summaries" is one join, and **two runs over the
same mailbox with different models can be compared** rather than overwriting each other.
That is exactly the comparison the evaluation work needs, and without it a model comparison
on summarisation cannot be done at all.

`processed_at` stays on the row because a run can span time.

### Change 3: `UNIQUE (email_id, run_id)`

One analysis per email per run. Re-running the same run upserts rather than accumulating,
which is the defect Phase 4 fixed for invoices and should not be reintroduced here.

### Change 4: `evidence` is kept, and it is the best thing in JJ's design

`action_items` carrying the sentence an item was inferred from is provenance, and it is the
same idea as `total_source` and `body_source`. **It is what makes a wrong action item
diagnosable instead of merely wrong.** It should be stored, not dropped for space.

---

## 4. What I need from JJ

1. **The `category` label list.** I will not add a CHECK constraint until the values are
   fixed, and I will not leave it unconstrained permanently, because a free-text category
   drifts across runs and cannot be aggregated.
2. **Is `deadline` always a date, or can it be "end of month"?** The CHECK depends on it.
   `invoice_date` already carries the scar of a constraint written without checking the data.
3. **Are `latest_decisions` and `outstanding_actions` free text, or lists?** If lists, they
   should be rows rather than a delimited string. The `line_items` blob was exactly this
   mistake and cost a migration to undo.
4. **Agreement on `run_id`.** It is the one change with a real cost to his module.

## 5. What I need from Luke

Whether `In-Reply-To` and `References` can be captured at intake. Without them, threading is
a subject-line guess and every thread summary must be labelled as computed over a guessed
grouping.

## 6. What I will do once agreed

Migration 010, additive, three tables, nothing existing moved. Storage methods
`save_email_analysis` and `save_thread_analysis` taking `message_id` and resolving
internally. Tests covering the upsert, the cascade, and the constraints.

**No change to `main.py`, `tasks` or `outbound_messages`.** JJ's module and the invoice
pipeline stay independent until we decide they should meet at the outbox.
