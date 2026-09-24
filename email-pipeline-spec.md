# Spec: connecting the email AI module to storage

**From:** Neo. **Status:** 2026-09-17. **Implemented.** Branch `neo/email-pipeline`, stacked on
`neo/email-analysis-storage` (PR #6), because it calls the storage API that PR adds.

---

## 1. The gap this closes

Three things exist and none of them touch each other:

```
email_listener.py  fetches mail          (Luke, does not write to the database yet)
email_ai.py        analyses a message    (JJ, on main since PR #5)
storage.py         stores an analysis    (Neo, PR #6, not merged)
```

**No file reads an email, calls the model and saves the result.** `main.py` is the invoice
pipeline and knows nothing about any of this. So the email half of the project runs only when
a person types it by hand.

This is not "waiting on JJ". By the ownership table, extraction and storage are Neo+JJ's shared
lane, which means the connecting piece has no owner and will sit there while both sides wait.

## 2. What it is

One file, `email_pipeline.py`, mirroring what `main.py` does for invoices.

```
./.venv/bin/python email_pipeline.py                 # analyse every email with no analysis
./.venv/bin/python email_pipeline.py --limit 3       # stop after 3
./.venv/bin/python email_pipeline.py --threads       # also summarise each thread
./.venv/bin/python email_pipeline.py --dry-run       # say what would run, call no model
./.venv/bin/python email_pipeline.py --model qwen3:14b
```

It reads `email_messages`, calls `email_ai`, writes through `storage`. It adds no analysis of
its own, and it is the only file that imports both modules.

### Order of work

1. Open a run with `start_email_run(model)`.
2. Select emails with `body_text IS NOT NULL` that have no analysis for this run.
3. Per email: build `EmailMessageInput`, `analyse_email()`, `create_email_analysis_record()`,
   `save_email_analysis()`.
4. With `--threads`: group by `thread_id`, `summarise_thread()`, `save_thread_analysis()`.
5. `finish_run(run_id, count)`.
6. Print a per-email line and a summary table.

### What it must not do

- **No retry of its own.** `email_ai` already retries evidence validation three times and
  reports `attempt_count`. A second retry loop would make that number meaningless.
- **No repair of model output.** The invoice side has a regex fallback and the report has to
  separate "the model" from "our code repairing the model" because of it. The email side
  starts clean and should stay that way until there is a measured reason.
- **Nothing written to `main.py`, `app.py`, `email_ai.py` or `email_listener.py`.**

### Failure handling

An email that raises is counted, named, and skipped. One malformed message must not end a run
over twenty. The run still closes with `finish_run`, because an open run means a crash and
`reprocess.py --list` reads that.

## 3. Threading, and the honest version of it

`email_messages.thread_id` is NULL on every row, because intake does not capture `In-Reply-To`
or `References` and that is Luke's file.

So this adds `group_by_subject()`: strip a leading `Re:`, `Fwd:`, `FW:` or `RE:` chain, casefold
the remainder, and group on what is left. It writes `thread_id` and sets
`thread_source = 'subject'`.

**It is wrong in two known ways and the column is what makes that reportable.** Three different
vendors in this mailbox all send a message titled "Monthly statement", and subject grouping
merges them into one thread. A subject edited mid-conversation splits one thread into two.
`thread_source` means no summary computed this way is ever reported as though it came from a
real reply chain.

**The mock mailbox is the proof.** It holds three "Monthly statement" rows from three senders,
so the defect is demonstrable rather than theoretical, and the count of emails wrongly merged is
a number the report can state.

Sender is deliberately not part of the key. Adding it would fix "Monthly statement" and break
every real thread, because a reply comes from a different address than the message it answers.
That is the same result `retrieval_eval.py` already measured: keyword scores 1.00 on threads and
0.00 on vendors, and the two cannot be satisfied by one rule.

## 4. Requirements the documents do not cover

`requirements-spec.md` mentions the email AI module **nowhere**. Zero hits for `email_ai`,
`summaris`, or `action item`. There is no FR for it, it is absent from the acceptance criteria,
and the objectives were written when this was an invoice-only project.

That matters more than it sounds: the work is assessed from the documents, and a module on
`main` with four tables behind it is currently invisible in them.

**This spec adds a `Phase 2b, email understanding` group** describing what exists, with the same
honest status column the rest of the table uses. Three existing entries are also stale and are
corrected:

| Where | Says | Should say |
|---|---|---|
| FR-2.2 | "Partial, 3/15 fields correct" | 10/15 model alone, 15/15 shipped. Superseded by the 2x2 |
| NFR-4 | "194 tests for storage" | A command, not a number. Counts rot on every push |
| Acceptance 4 | "explain why extraction scores 20%" | The 2x2 replaced that single comparison |

**What this spec does not change: O1 to O4, and whether an objective should be added for the
email half.** Objectives belong to the group, not to one lane. The gap is written into the
document as a question for the group rather than answered unilaterally.

## 5. A defect in every migration before 010

Migrations 001 to 009 name their backup `<db>.bak-%Y%m%d-%H%M%S`. The quickstart runs them back
to back, so two land in the same second and the second copy overwrites the first. 010 and 011
already add a counter; this moves that into `storage.backup_path()` and uses it in all eleven,
so there is one definition rather than eleven.

Found on 2026-09-17 by running 010 and 011 in sequence and reading the two printed paths.

## 6. Tests

`tests/test_email_pipeline.py`, with the model stubbed. No test calls Ollama.

- Subject grouping: `Re:`, `Fwd:`, nested `Re: Re:`, case, surrounding whitespace.
- Three "Monthly statement" rows from three senders land in one thread, which is the defect,
  asserted as present rather than fixed.
- An email with no `body_text` is skipped, not failed.
- An email that raises is counted and the run still closes.
- `--limit` stops where it says.
- `--dry-run` calls no model and writes no row.
- Re-running the same run updates rather than duplicates.
- A second run with a different model produces a second set of rows.
- `backup_path` returns a free name when the obvious one is taken.

## 7. What is still not in our lane after this

- `In-Reply-To` and `References` at intake (Luke).
- Ground truth for classification, summarisation and action extraction (JJ, his item 2).
  Without it none of this can be scored, only run.
- Whether an email action item should reach `outbound_messages` (the group).


---

## 8. What was built, and the metric that was wrong first

`email_pipeline.py`, `tests/test_email_pipeline.py` (model stubbed, no test calls Ollama), the FR-3 group in
`requirements-spec.md`, and `storage.backup_path` used by all eleven migrations.

**The collision metric was wrong in its first version, and the fixture caught it.** It counted
threads spanning more than one sender. That flags every genuine conversation, because a reply
comes from a different address than the message it answers, and the real collision would have
been hidden inside that number. It now counts threads holding several messages where **none**
says `Re:`, which is several originals sharing a subject.

That version has a false positive of its own: somebody who replies after deleting the `Re:` is
counted as a collision. There is a test asserting exactly that, so the limitation is recorded
rather than discovered later. Subject matching cannot separate the two, which is the argument
for capturing `In-Reply-To` at intake rather than writing a cleverer heuristic here.

**A full run over the real mock mailbox, 18 emails, `llama3.2:latest`:** 18 analysed, 0 failed,
all `Validated` on the first attempt, 5 action items, 16 threads of which 1 is a known subject
collision.

**Every one of the 18 came back `category = "Invoice"`,** 1 label out of 6. That does not prove
the classifier is wrong, because every message in that mailbox is invoice-related. It does mean
the corpus cannot tell a working classifier apart from one answering "Invoice" unconditionally.
Recorded under FR-3.9, and it is an argument about the test data as much as about the model.
