# Team Sync: where the data layer stands, and what I need from you

**From:** Neo
**Date:** 2026-08-28
**Read time:** about 10 minutes. The asks are in §4. Everything in §5 is done
and needs no discussion unless you disagree with it.

Everything in this document was verified by running something. Commands are included so you can
check any of it yourself.

---

## 1. Where we actually are

The pipeline runs end to end. Measured on our own mock invoices:

```
vendor_name 3/3 | invoice_number 0/3 | date 0/3 | total_amount 0/3 | currency 0/3
OVERALL 3/15 (20%)   Gate: 0/3 Validated, 0% automation pass rate
```

I want to be careful about how we say this, because it is easy to say something false in either
direction. It is not "the local model does not work", and it is not "the pipeline works fine". The
accurate statement is: **the pipeline executes correctly and the extraction step returns mostly empty
fields, for a reason we have identified.**

## 2. What I finished on the data layer

The database was a single flat table with no constraints, no uniqueness, and line items stuffed into
a JSON blob. That is now five related tables with constraints enforced by SQLite itself, reached
through four versioned migrations, covered by 138 automated tests.

Concretely, what changed for you:

- **Re-running no longer duplicates rows.** This is the "older results still in SQLite" problem JJ
  hit. It was a schema defect, not a cleanup chore. Four pipeline runs now produce three rows.
- **The invoice totals came back.** All three read `0.00` before. They now read 1500.00 / 2650.00 /
  2350.00, matching ground truth, recovered from the line items. **The extractor was not changed.**
- **Line items are queryable.** `SELECT ... FROM line_items WHERE unit_price_cents > 20000` works.
  Before, that data was text inside a column.
- **The approve button works.** It had never once executed. `app.py` read `filtered_df["ID"]` where
  the column is lowercase `id`, so it raised `KeyError` and killed the whole Detail Inspector.

To run it after pulling:

```bash
./.venv/bin/python -m pip install -r requirements.txt
for m in migrations/00*.py; do ./.venv/bin/python $m; done   # backs your .db up, safe to re-run
./.venv/bin/python main.py
./.venv/bin/python query_db.py
./.venv/bin/python -m pytest tests/ -q
```

The full schema and every field's meaning is in [database-spec.md](database-spec.md).

### One finding worth two minutes of your time

The first draft of my design deduplicated on a hash of the PDF file bytes. That would have
deduplicated nothing. ReportLab writes a random `/ID` into every PDF it generates, so regenerating
our mocks produces different bytes from identical content:

```
same 2454 bytes, same /CreationDate, only /ID differs
run a: /ID [<7c50d45da46b4a53b493a0bc9223a879>]
run b: /ID [<ca215dd54ff3413d6f45f7c475381abb>]
```

It now hashes the extracted text instead, which is stable. I mention it because it is a good example
of a design that reviews fine on paper and fails on contact with the actual data, and it is the kind
of thing worth putting in the report.

## 3. What I am concerned about

Ordered by how much damage it does, not by whose lane it is in.

### 3.1 Extraction returns empty fields because of our schema, not the model (JJ's lane)

Every field in `ExtractedInvoice` is `Optional` with a default. Pydantic therefore emits an empty
`required` list, and Ollama's constrained decoder is free to omit the fields entirely. The defaults
then backfill `None`, `0.0` and `"Unknown"`, which is exactly what we see in the database.

The same model and the same prompt with required fields returns 5/5. A 2x2 isolates the schema as
the causal variable.

**JJ, this is your file and I have not touched it.** I have the evidence and can open a PR against
your lane whenever you want it. I am flagging it rather than fixing it because it is yours, and
because it changes the meaning of every number in our evaluation.

Please do not let anyone attribute this to the model or try to fix it with prompt engineering. We
measured it. It is the schema.

### 3.2 Our validation gate approves hallucinated totals (my lane, my problem)

This is mine and I have not fixed it yet. The gate never checks the extracted total against the
source document. Reproduced today on invoice 1, which is a $1,500 invoice:

```
ยอดจริง  1,500.00      -> (1.0, 'Validated')
ยอดหลอน 999,999.99     -> (1.0, 'Validated')
'999999.99' appears in the document: False
```

A number that appears nowhere in the document scores a perfect 1.00 and passes as Validated,
identically to the correct value. Invoice number and vendor are both checked against the text.
Only the amount is not.

This matters more now than last week, because the dashboard finally displays correct totals, so
anyone looking at it will believe what they see.

### 3.3 ~~We cannot reproduce each other's runs~~ FIXED 2026-08-28

`requirements.txt` now pins every direct dependency. **You need to run
`pip install -r requirements.txt` once**, because it also adds two packages that were missing from
our environments: `pytest`, and `python-docx` which `generate_briefing_docx.py` has always needed.

### 3.4 ~~The `.docx` will cost us a day~~ FIXED 2026-08-28

`Enterprise_AI_Workflow_Briefing.docx` is untracked and gitignored. `generate_briefing_docx.py`
regenerates it, verified by deleting the file and rebuilding it: every file inside the document comes
back byte-identical.

The file as a whole does not, and that turns out to be the argument for untracking it. A `.docx` is a
ZIP archive, and the archive stamps each entry with the time it was written:

```
old   1738  08-24-2026 23:00   [Content_Types].xml
new   1738  08-28-2026 12:18   [Content_Types].xml
```

Same 40,129 bytes, different SHA-256. So regenerating an **unchanged** document still produces a file
git sees as modified. Tracking it would have meant a merge conflict git cannot resolve every time any
two of us rebuilt it.

### 3.5 ~~Nothing is pushed~~ MINE IS PUSHED 2026-08-30, yours probably still is not

`neo/database-redesign` is on my fork, 12 commits:

```
https://github.com/NattakritPitayasiri/invoice-local-llm-demo/tree/neo/database-redesign
```

No PR yet and `main` is untouched, deliberately: the five decisions in §4 should be argued before
they become the default.

**`upstream/main` has not moved since JJ's first commit**, so nobody else has pushed anything either.
JJ has clearly been working, since he sent a list of seven issues, so that work is on his laptop. The
longer it sits there the worse the eventual merge gets. We have both touched `app.py` and the
`filtered_df["ID"]` line specifically, which is the one place a conflict is close to certain.

### 3.6 ~~The Copilot question~~ RESOLVED 2026-08-28

**Copilot is confirmed not mandatory.** This was our highest-impact unknown and it is now closed.

What it means for us: the local stack stands as a **replacement** for the Microsoft design, not a
hybrid. We do not need to defend a missing Copilot integration, and the pivot narrative in the report
is now a complete, closed story rather than a contingency. Nothing in the code changes.

## 4. Decisions I need from the group

These touch shared contracts, so I am not making them alone.

| # | Decision | My recommendation | Cost if we defer |
|---|---|---|---|
| D1 | Rename `ProcessedRecord.confidence_score` to `validation_score` | Rename. It measures completeness and substring matching, not confidence, and calling it confidence misleads whoever reads the report | Low, but the name misleads the marker too |
| D6 | The database column `status` is now `validation_status` | **Already renamed** in migration 005. `model_result_status` was considered and rejected, because the value comes from our gate thresholding its own score, not from the model. Ratify or tell me to change it | Low. The rename is done and reversible with one more migration |
| D2 | Split `status` (data quality, written by the pipeline) from `approval_status` (business decision, written by a person) | Split. **Already implemented**: approving now writes `approval_status` and `reviewed_at` and leaves `status` and `validation_score` untouched. I need you to ratify it, or tell me to revert | Medium. A row can now read `NeedsReview` and `Approved` at once. That is intended, but you should agree it reads correctly before a marker sees it |
| D3 | Do we fix extraction (§3.1) before the demo, or demo the 20% and explain it? | **Fix it, and report both numbers.** Before and after is a stronger result than either alone | High. This shapes the whole presentation |
| ~~D4~~ | ~~Who owns sending the Copilot email~~ | **Closed 2026-08-28, Copilot confirmed not mandatory** | n/a |
| D5 | Does a `Validated` document still create an approval task? | **Yes.** Automation reduces the reading, it does not remove the approval. See the routing rule in §5 | Medium. It is a claim about our workflow, and it is already implemented, so silence means agreement |

On D3, I want to argue for something slightly unusual. Our most defensible result is not a high
accuracy number. It is that we found a specific, non-obvious root cause, measured it with a
controlled comparison, and fixed it. Most groups cannot show that. If we fix extraction quietly and
only report the final number, we throw away the best evidence we have that we understood our own
system.

## 5. What I am doing anyway, without waiting for anyone

These are all inside my own lane, so they need no agreement. Listed so nobody duplicates the work.

| # | Task | Status |
|---|---|---|
| S1 | `requirements.txt`, every direct dependency pinned | **Done 2026-08-28** |
| S2 | Untracked the `.docx`, kept the generator | **Done 2026-08-28** |
| S3 | Fix the gate to verify `total_amount` against source text | **Not started.** Needs its own spec first |
| S4 | Migration 002, `extraction_source` renamed to `total_source` | **Done 2026-08-28** |
| S5 | Approval wired to `approval_status` and `reviewed_at` | **Done 2026-08-28** |
| S6 | Tests for the storage layer and the migrations | **Done 2026-08-28**, 138 tests |
| S7 | `reprocess.py` | **Done 2026-08-28** |

**S2 touches a file we all share.** It is on my branch rather than committed to main, so treat it as
a PR. The `.docx` is now gitignored and `generate_briefing_docx.py` regenerates it byte for byte, so
nothing is lost. `python-docx` was missing from everyone's environment, which is why it is now in
`requirements.txt`.

**S6 paid for itself twice on the first run.**

The test suite found a constraint bug that had been in the schema since day one:
`invoice_date GLOB '____-__-__'` rejects every real date, because `_` is not a wildcard in GLOB
(that is `LIKE`). It had never fired only because the extractor returns NULL for every date.
**It would have fired on the first insert after JJ's extraction fix, and it would have looked like
JJ's fix breaking the database.** Migration 003 repairs it.

It then found a second one: migrations 002 to 004 reported failure when run against a database that
had already moved past them, so running the whole migration sequence twice would have looked broken
to whoever pulled next. Both are now regression-tested.

**S3 is the one thing left on my list.** It is the gate accepting hallucinated totals from §3.2. I
have not started it because it changes scoring behaviour and deserves its own spec rather than being
folded into database work.

### Two new tables, added 2026-08-28

The database now has `email_messages` and `tasks` (migration 004, additive, nothing existing moved).
They exist because two parts of our workflow were not represented in data at all.

**`email_messages`** closes the gap where a stored invoice could not say which email delivered it.
Keyed on the RFC 5322 `Message-ID`, which is globally unique, so it also gives intake the duplicate
protection FR-1.3 asks for.

**Luke, this needs a small change in your file and I have not touched it.** Before saving
attachments:

```python
result = storage.record_email(
    message_id=msg["Message-ID"], sender=msg["From"],
    subject=msg["Subject"], received_at=msg["Date"],
    attachment_count=len(attachments))
if result["already_seen"]:
    continue          # never re-download the same email
```

The API is written and tested. One piece is still undesigned: how a saved file gets associated back
to its `email_id`. A sidecar file or a staging table both work, and that is a decision for you, not
me.

**`tasks`** replaces `DownstreamDispatcher` printing fake Teams and Jira lines that vanished when the
terminal closed. Nothing was recorded, so "how many documents are waiting for a person" could not be
answered. Now the pipeline writes a row and the dashboard shows a queue.

The routing rule is worth a minute at the meeting, because it is a claim about our workflow, not
just a schema choice:

```
NeedsReview -> Review     the pipeline could not read it confidently
Validated   -> Approve    it was read cleanly, but money still needs a human signature
```

A `Validated` document still creates work. I think that is the honest model: automation reduces the
reading, it does not remove the approval. The alternative, opening tasks only for low-confidence
documents, quietly implies a confident extraction can pay an invoice by itself. **This is a
Trigger/Approval decision the group deferred, so I am flagging it rather than treating it as
settled.**

The Teams and Jira calls are still simulated. `external_ref` is there for a real Jira key and stays
NULL. We do not need a real integration to demonstrate the concept, and pretending we have one would
be worse than an honest placeholder.

### One column renamed, 2026-08-28

`invoices.status` is now `invoices.validation_status` (migration 005). The old name did not say
whose judgement it held, which stopped being acceptable once `approval_status` sat next to it.

I want to record why `model_result_status` was rejected, because the reasoning is the same one in
§3.1. That value never comes from the model:

```python
status = "Validated" if final_score >= self.threshold else "NeedsReview"
```

It thresholds a score our own gate calculates. Drop the threshold to 0.30 tomorrow and every row
flips to Validated while the model does identical work. Naming the column after the model would
have written the wrong cause into the schema, where it outlives everyone's memory of this
conversation. `validation_status` pairs with `validation_score`: one gate, a score and a verdict.

Nothing about the data changed. Same three rows, same totals, CHECK constraint carried across.

## 6. What this means for the report

We do not need a system that works perfectly. We need a concept that is clear, defensible and
presentable. On that measure we are in better shape than the 20% number suggests, because we can
show:

- A **documented pivot** from a cloud design to a local one, with the reason and the trade-offs.
- A **measured evaluation** against independently transcribed ground truth, including an explicit
  statement of what it does not measure.
- A **root cause analysis** that found a non-obvious schema defect, isolated it with a controlled
  comparison, and can show before and after.
- A **data model** with enforced constraints, versioned migrations, and a documented decision trail,
  including one design we corrected after measuring it (§2).
- **Honest limits**, stated by us rather than found by a marker.

The last one is worth real marks. Our evaluation method already contains a "what this does NOT
measure" section. Most student reports do not have one.

## 7. Suggested agenda for the next meeting

1. **Everyone pushes their branches.** Two minutes, do it first, it is the only open risk on my
   list. (§3.5)
2. **D3, how we handle the extraction result in the report.** The big one, and the only decision
   that changes what we present. (§3.1, §4)
3. **D5, does a Validated document still need approval.** Already implemented, so I need you to
   either agree or tell me to change it. (§4, §5)
4. **D1 and D2, the shared contract renames.** Small, but they need more than one person. (§4)
5. **JJ and Luke:** §3.1 is a PR I can open for you whenever you want it, and §5 has the four lines
   Luke's intake needs. Neither is urgent today.
6. Everything else is written down. Read [database-spec.md](database-spec.md) rather than having me
   talk through it.

D4 is closed: Copilot is confirmed not mandatory (§3.6).
