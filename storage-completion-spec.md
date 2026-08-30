# Spec: Finish the Storage Scope, and Prepare the Post-Approval Hand-off

**Owner:** Neo. **Branch:** `neo/database-redesign`, continuing.
**Status:** written 2026-08-30 under a "finish storage first, then prepare post-approval"
instruction. Decisions are stated explicitly so any of them can be reversed cheaply.

Two migrations, kept separate because they answer different questions.

---

## Part 1 — Migration 006: finish the storage scope

Three gaps remain in Phase 4. All three are schema-level and none depends on document layout,
so none of them will need rebuilding when real documents arrive.

### 1.1 Nothing checks the total against the line items

We hold the same amount twice, obtained two different ways: `invoices.total_cents`, read by the
model from a total line, and the sum of `line_items`, read by the model from a table. If both are
right they should agree, or differ only by tax and shipping. Nothing compares them.

This matters more than it did last week. Measured 2026-08-30, required fields make the model
invent a total on a document that states none:

```
Statement of account, "for information only", no amount due
  Opening balance   1,200.00
  Payments received   800.00
model returned total_amount = 2000.0
```

It did not copy a wrong number, it computed a plausible one. Proximity checking cannot catch that,
because the model can point at real numbers. Arithmetic can.

**Proposed: a `reconciliation` column, computed on write.**

```
exact      total == sum of non-summary line items
plausible  total > sum, consistent with tax or shipping being added
short      total < sum, which tax and shipping cannot explain
unknown    there are no line items to compare against
```

Stored rather than computed on read, so the dashboard and any report query see the same value, and
so a later change to the rule does not silently rewrite history.

**It is a flag, not a constraint.** A real invoice with GST and freight is legitimately `plausible`,
not wrong. Only `short` is a genuine anomaly, because tax and shipping only ever increase a total.

### 1.2 `vendor_name` has no provenance, but `total_cents` does

`total_source` records whether a total came from the model or was derived. `vendor_name` has no
equivalent, even though `DocumentExtractor._infer_vendor_fallback` can silently replace a model
answer with a guess from the filename. Today that path does not fire, verified by disabling it and
re-running: the model finds all three vendors itself. But if it ever fires, nothing in the database
would say so, and a reader would assume the model read it.

**Proposed: `vendor_source`, same three values as `total_source`.**

The pipeline cannot currently tell the two apart, because the substitution happens inside JJ's
extractor before storage sees the record. So this ships as a column that is honestly always
`'model'` until the extractor reports which path it took. That is a small change in JJ's lane and
goes to him with the extraction PR, not before.

Adding the column now anyway, because it is free during a migration already in flight and because
the alternative is a second migration later.

### 1.3 An invoice and a receipt are stored identically

The schema cannot distinguish them, but they imply different work:

| | Invoice | Receipt |
|---|---|---|
| Money | not yet paid | already paid |
| Needs approval | yes | no |
| Next step | check, approve, schedule payment | file, reconcile |
| Cost of waving it through | wrong payment | inaccurate books |

**Proposed: `document_type`, one of `Invoice` / `Receipt` / `Unknown`, defaulting to `Unknown`.**

Set by a keyword heuristic over the document's opening lines, which is where documents label
themselves (`TAX INVOICE` on line 1 of all three samples). When the heuristic is not confident it
records `Unknown` rather than guessing, and a person can correct it from the dashboard.

**Not** added to `ExtractedInvoice`. That is a shared contract in JJ's file, and asking the model
to classify would be a second thing to measure. The heuristic reads text we already have.

---

## Part 2 — Migration 007: prepare the post-approval hand-off

**Preparation only. No Teams call, no Jira call, no Planner call.** The point is that the shape
exists and can be demonstrated, not that the integrations are real.

### 2.1 What is missing

Approval currently terminates. A person clicks Approve, `approval_status` becomes `Approved`, the
open task closes, and nothing follows. In the original eight-phase design, Phase 5 hands to Phase 6:
assign a task, raise a Jira ticket, notify a channel. Phase 6 has had no owner since 2026-08-14.

### 2.2 Two task types, and a rule that fires on approval

Extend `task_type` from `Review / Approve / Fix` to add:

```
Payment   opened when an Invoice is approved      the money still has to move
File      opened when a Receipt is approved       nothing to pay, it needs filing
```

So approval closes one task and opens the next, which is what makes it a hand-off rather than an
ending. A rejection opens nothing: the document was not accepted.

This is where `document_type` earns its place. Without it, the follow-on task cannot depend on what
the document actually is, only on how confidently it was read, which is the wrong question.

An `Unknown` document type opens a `Review` task instead, so an unclassified document reaches a
person rather than being routed by a guess.

### 2.3 An outbox, so a dispatch is recorded rather than printed

`DownstreamDispatcher` still prints its Teams line. A print vanishes with the terminal, so "what
have we sent downstream, and did it succeed" cannot be answered.

**Proposed: `outbound_messages`**, one row per thing that should be sent to an external system.

```
outbox_id, task_id, channel (Teams|Jira|Planner|Email), payload,
state (Pending|Sent|Failed), created_at, sent_at, external_ref, error
```

Nothing sends anything. Rows are written with `state = 'Pending'` and stay there. That is the honest
representation of where the project is: the decision to notify is made and recorded, the transport
is not built. When a real integration lands it reads this table and fills in `external_ref`.

This is worth more than a working integration for the demonstration. A queue that visibly holds
three pending notifications shows the workflow; a `print` that already scrolled past shows nothing.

---

## What this does not do

- No external system is contacted. Every `outbound_messages` row stays `Pending`.
- `vendor_source` will read `'model'` on every row until JJ's extractor reports its path.
- `document_type` is a keyword heuristic over three synthetic documents that all say `TAX INVOICE`.
  It is not validated against receipts, because we have none.
- Reconciliation cannot detect a total that is wrong but arithmetically consistent.

## Code changes

| File | Change |
|---|---|
| `migrations/006_storage_completion.py` | **New.** `reconciliation`, `vendor_source`, `document_type` |
| `migrations/007_post_approval.py` | **New.** New task types, `outbound_messages` |
| `storage.py` | Compute reconciliation and document type on write; `open_followup_task`, `queue_outbound`; `SCHEMA_VERSION = 7` |
| `app.py` | Show reconciliation and document type; approving opens the follow-on task |
| `main.py` | `DownstreamDispatcher` writes an outbox row alongside its print |
| `query_db.py` | Show reconciliation |
| `tests/` | Reconciliation states, classification, follow-on rules, outbox |

## How to know it worked

- All three current invoices reconcile `exact`, and a hallucinated total reconciles `short` or
  `plausible` but never `exact`.
- Approving an `Invoice` opens a `Payment` task; approving a `Receipt` opens a `File` task; an
  `Unknown` opens `Review`; rejecting opens nothing.
- The outbox holds a `Pending` row per dispatch and the dashboard can show the queue.
