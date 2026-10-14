# Spec: Overview v2 (summary, attention, recent, progress)

**Status:** first pass built 2026-10-12 on the recommended answers in section 8, which Neo accepted
as a starting point to groom later. Parts 1 to 3 are built. Part 4 (progress) is not.
**Origin:** a teammate's suggestion of four parts for the Overview page (name to confirm).
**Builds on:** `fe-overview-spec.md` (OV-1 to OV-8, done). That spec finished the page; this one
reshapes what it leads with.

## 1. What the page is today, and what changes

Today: a three-tile strip (Waiting for you, Approved by the system, Oldest wait), then four
sections: Awaiting approval, Approved by the system, Outbox, History. Each section is capped
(three rows for the queue, five for the rest) and that cap is load-bearing, see section 6.

Proposed, in this order:

| Part | What it answers | Replaces |
|---|---|---|
| 1. Summary cards | How big is the situation? | The three-tile strip, grown to six |
| 2. Attention required | What needs a person, and why? | The Awaiting approval section, narrowed to documents with a reason |
| 3. Recent invoices | What came in lately, and where is each one? | History and Approved by the system, merged into one table |
| 4. Workflow progress | How many invoices are at each stage? | Nothing. New |

The Outbox section stays, shown only when a row has failed or is waiting to push.

## 2. Summary cards

Six cards: total invoices, total amount, awaiting approval, approved by a person, needing
attention, approved by the system.

- **Total amount is shown per currency**, never summed across currencies. `single_currency_total`
  already refuses to add different currencies; the card lists one line per currency instead.
- **Needing attention** counts the same set as part 2, so the two cannot disagree.
- Every number is computed from the dataframe the page already loads. No new query.

## 3. Attention required

A document appears when it has at least one reason. Each row shows vendor, invoice number,
amount, the reason in words, how long it has waited, and a **Review** button that opens the
existing review dialog. Review does not approve; approving stays in the dialog only.

| Reason | Source in the database | Built? |
|---|---|---|
| Total amount could not be verified | existing risk signal in `review_signals.py` | Yes |
| Missing line items | existing risk signal | Yes |
| No matching email | `email_id IS NULL` | Yes |
| Waiting too long | `now - processed_at` over a threshold (see decision 2) | Yes |
| Processing failure | `validation_status = 'Failed'` | **Partly.** A document the model could not read at all is not stored as a row, so it cannot be listed. Only stored failures appear |
| Possible duplicate | none | **No.** The pipeline upserts on a content hash, so a duplicate never becomes a second row. A fuzzy definition (same vendor, number and amount) would be new logic and is not in this spec |

## 4. Recent invoices

One compact table, newest first, capped at ten rows: vendor, invoice number, amount and
currency, received date, **validation status**, **workflow status**, and an Open action.

Validation and workflow stay separate columns, as the database already separates them
(`validation_status`, `approval_status`).

**Workflow status uses only states the data supports:** Awaiting approval, Approved by a person,
Approved by the system, Rejected, Queued for Jira (outbox row pending), Sent to Jira (outbox row
sent). There is no "Completed": no table records that a payment happened.

## 5. Workflow progress

A row of stage counts, left to right, each from one query:

Stored, Validated (gate passed), Awaiting approval, Approved (person or system), Queued in
outbox, Sent to Jira.

- "Received" and "Extracted" from the suggestion are **not separate stages here.** A file that
  fails extraction is never stored, so the data cannot count it, and every stored invoice is
  already extracted. They would be two copies of "Stored".
- **Sent to Jira will read 0** until the Jira path has run against a real project. The stage
  carries a caption saying so, so a zero is read as "not yet run" and not as "broken".

## 6. Verification

| Part | Verified by |
|---|---|
| Cards | Counts equal direct queries on a seeded database. Currencies are listed separately, never summed |
| Attention | Each reason maps to a real signal or column. A document with no reason does not appear. Needing-attention card equals the number of rows |
| Recent | Capped at ten. Validation and workflow are different columns. Open opens the dialog |
| Progress | Each count equals its query. The Jira stage shows 0 with its caption on a database with no sent rows |
| All | `pytest tests/ -q` green, 0 exceptions on every tab in `AppTest`, checked by eye in a browser against the design system |

## 7. Risks

- **Row cap.** Streamlit reruns every tab body on every rerun, and the dashboard now redraws on a
  timer. Each widget on Overview is built every time. The caps (three, five, ten) bound that and
  are part of the design.
- **Duplication.** Attention is a subset of the Awaiting tab, and Recent overlaps History. The
  Overview must stay a summary that points at the tabs, not a second copy of them.
- **Length.** Four parts plus the Outbox section is a long page. If it scrolls past one screen
  of useful content, part 4 is the first to cut.

## 8. Decisions (accepted as the starting point, to be groomed)

1. Attention **replaces** the Awaiting approval section on Overview. Built.
2. "Waiting too long" is **2 days**, one constant (`WAITING_TOO_LONG_DAYS` in `overview_data.py`).
3. "Possible duplicate" is **left out** and goes in the report's Future Work.
4. "Received" and "Extracted" are **dropped** from the funnel.
5. History and Approved by the system are **merged** into Recent invoices. Both keep their tabs.
6. Scope of this pass: **cards, Attention and Recent**. Workflow progress (part 4) is not built.

## 9. What changed in the tests, and why

The document card (with the covering-email chip, the signal sentence and the review button) used
to be drawn on Overview. It now lives only on the Awaiting approval destination, so the tests
about the card render that destination instead. Their assertions are unchanged. The Overview tile
test now finds tiles by label rather than by position, since the first tiles are no longer
Waiting and Approved.
