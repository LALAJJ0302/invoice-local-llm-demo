# Spec: Make the Validation Gate Verify the Amount

**Owner:** Neo (`ConfidenceValidator` in `main.py` is Phase 5, his lane).
**Branch:** `neo/database-redesign`, continuing.
**Status:** written and implemented 2026-08-28 under the standing "start now" instruction. The
design decisions are stated explicitly below so they can be reversed cheaply if disagreed with.

## The defect

The gate never checks the extracted total against the source document. Reproduced on invoice 1,
a $1,500 invoice:

```
true total    1,500.00      -> (1.0, 'Validated')
hallucinated  999,999.99    -> (1.0, 'Validated')
'999999.99' appears in the document: False
```

A number that appears nowhere in the document scores a perfect 1.00 and passes identically to the
correct value. `invoice_number` and `vendor_name` are both checked against the text. Only the
amount, the field that decides how much money moves, is not.

This matters more now than a week ago for two reasons. The dashboard finally shows correct totals,
so a reader will believe what they see. And once the extraction schema is fixed, required fields
mean the model can no longer stay silent about a field it cannot find, so it may guess instead. The
gate is what is supposed to catch that, and it currently cannot.

## Goal

The gate must never mark a document `Validated` when it could not confirm the money against the
document text.

## Non-goals

Extraction accuracy (JJ's lane). Scoring `date` or `currency`, which the gate has never scored and
which is a separate question. Calibrating the score into a probability: it is not one and this
change does not make it one.

## Design

### 1. Amount verification has three outcomes, not two

```
verified    the amount appears within 2 lines of a grand-total label
present     the amount appears somewhere in the document, but not near such a label
absent      the amount does not appear at all
```

Two tiers rather than a plain substring test, because a plain test is not enough. On invoice 3 the
true total is 2,350.00, but 1,500.00 also appears as a line item total:

```
line 11: 'Total'          <- a column header
line 15: 'USD 1500.00'    <- a line item, 4 lines away
line 20: 'Grand Total'
line 21: 'USD 2350.00'    <- the real total, 1 line away
```

A hallucinated `1500.00` on invoice 3 would pass a presence test. Proximity rejects it.

Numbers are compared after normalising away thousands separators and currency symbols, because the
documents render `USD 1500.00` while the model returns `1500.0`.

### 2. The status rule is a hard gate, not a weighting

```
Validated  requires  score >= threshold  AND  amount verified
```

Stated as a rule rather than achieved by tuning weights, because a weighted score that happens to
land below the threshold is fragile: change one weight and the guarantee silently disappears. The
policy is "we do not auto-approve money we could not confirm", and it should be written that way.

This also keeps the split the database already uses: `validation_score` stays an honest
measurement, `validation_status` is the verdict.

### 3. Rebalanced weights, so the score agrees with the rule

| | completeness | verified against text |
|---|---|---|
| `invoice_number` | 0.15 | 0.20 |
| `vendor_name` | 0.10 | 0.15 |
| `total_amount` | 0.15 | 0.25 (0.125 when only `present`) |

Totals 0.40 completeness and 0.60 verification. Outcomes on invoice 1:

```
everything correct            1.00   Validated
hallucinated 999,999.99       0.75   NeedsReview  (below threshold and hard-blocked)
total present but not near a label
                              0.875  NeedsReview  (above threshold, hard-blocked)
```

The third row is the one the hard rule exists for.

## What this does not fix

**It is a heuristic tuned on three documents from one generator.** Proximity within two lines works
on this layout. A real invoice that puts the total in a table cell far from its label would be
scored `present` rather than `verified` and sent to a human. That is a false negative, and it fails
in the safe direction, which is the same principle already recorded for `minConfidence` initialising
to 0 rather than 0.8.

**It cannot detect a plausible wrong number.** If the model returns a total that genuinely appears
next to a grand-total label but belongs to a different document, this check passes it. Verifying
that a number is present is not verifying that it is correct.

**The score is still not a confidence score.** It measures completeness and text agreement. This
change makes it measure more, not measure something different.

## Interface

`evaluate(data, raw_text)` keeps returning `(score, status)`. `evaluation/run_eval.py` unpacks
exactly two values, and changing that contract would break the measurement harness for no benefit.

A separate `explain(data, raw_text)` returns the per-check detail, so the pipeline can put a real
reason on the `tasks` row instead of "Low confidence score."

## Code changes

| File | Change |
|---|---|
| `main.py` | `ConfidenceValidator`: amount verification, the hard rule, rebalanced weights, `explain` |
| `main.py` | `DownstreamDispatcher`: use the gate's reason for the task, instead of a fixed string |
| `tests/test_validation_gate.py` | **New.** The hallucination case, the proximity case, the weights |

Nothing in the database changes. No migration.

## How to know it worked

The hallucinated total on invoice 1 must score below the threshold and come back `NeedsReview`,
while the true total still comes back `Validated`. Both are asserted in the new tests.

## Result, measured 2026-08-28

```
case                             score   status        amount
true total 1,500.00               1.00   Validated     verified
hallucinated 999,999.99           0.75   NeedsReview   absent
line item 900.00                  0.88   NeedsReview   present    <- the hard rule earning its place
no total extracted                0.25   NeedsReview   absent
```

Cross-checked on invoice 3, whose true total is 2,350.00 while 1,500.00 appears as a line item:
2,350.00 verifies, 1,500.00 comes back `present` and is refused.

**A float bug was found by these tests, in the money-checking code, on its first run.** The
comparison was written as `abs(value - amount) < 0.01`, and

```
abs(1500.0 - 1500.01) == 0.009999999999990905   ->  less than 0.01, so a near miss compared equal
```

Now compared in integer cents through `storage.to_cents`, which is what the database already does
and for exactly this reason. Worth recording: the project moved money to cents in the storage layer
on 2026-08-26 while leaving a float comparison in the gate that decides whether money is real.

## Consequence: the score scale changed

The weights moved, so `validation_score` is not comparable across this change. The three sample
documents went from 0.40 to 0.25, because the old scoring gave 0.15 for a present-looking
`invoice_number` and 0.25 for a vendor substring match, while the new one weights verification more
heavily and gives nothing for an unverifiable amount. The lower number is the more honest one.

The automation pass rate did not move: 0/3 before, 0/3 after. Extraction is still 3/15. Any
before-and-after comparison in the report should use the pass rate, which is stable, not the raw
score.

---

# Amendment, 2026-09-03: 1.00 must mean a complete extraction

## The defect

On the merged `main`, invoice 1 stored a vendor of `'Vendor: Apex Cloud Solutions Pty Ltd'`
and **zero line items**, against a document containing three. The gate scored it **1.00,
Validated**, and opened an approval task.

Every component fired:

```
invoice_number_present   0.15
vendor_present           0.10
total_present            0.15
invoice_number_in_text   0.20
vendor_in_text           0.15   <-- the problem
amount verified          0.25
                         ----
                         1.00
```

`vendor_in_text` is `vendor_name.lower() in raw_text.lower()`. The model returned the caption
along with the value, so the extracted string matched the document **more easily than the
correct answer would have.**

That is a perverse incentive. The check gets easier to satisfy the more of the document you
copy: a vendor field containing the entire invoice would score full marks. **A lazier
extraction scored higher than a careful one**, which inverts what a gate is for.

Line items were a second, independent gap. `data.items` was never read, and
`storage.reconcile()` was already computing the cross-check and being ignored.

## The rule this amendment adopts

> **1.00 means the extraction is complete. Nothing is empty. Anything missing reduces the
> score.**

This is stronger than the previous design, which scored only the fields it happened to check.
`date` and `currency` were not scored at all: an extraction could return neither and still
reach 1.00.

## Design

### Completeness, 0.40 of the score

0.06 for each of `invoice_number`, `vendor_name`, `date`, `currency`, `total_amount`, and
**0.10 for line items**, which are worth more because they are a whole table rather than one
value. The `empty` key in `explain()` names exactly which of them were missing, so a task and
a dashboard row can say what failed rather than only that something did.

### Agreement with the document, 0.60

| Check | Weight |
|---|---|
| `invoice_number_in_text` | 0.10 |
| `vendor_in_text` | 0.08 |
| **`vendor_is_not_a_label`** | **0.07** |
| amount `verified` / `present` / `absent` | 0.25 / 0.125 / 0 |
| reconciliation `exact` or `plausible` / `unknown` / `short` | 0.10 / 0.05 / 0 |

**`plausible` scores full marks, deliberately.** A total above the line-item sum is the normal
state of any invoice carrying GST or freight. Penalising it would penalise every real
Australian invoice. Only `short` is an anomaly on its own.

### Two new hard rules

Joining the existing "the amount must be `verified`":

- **The vendor must not be a field caption.** Unrecognised captions are not penalised, so this
  check can only improve on not having it.
- **Reconciliation must not be `short`.** Line items exceeding the total they belong to is not
  something tax or shipping explains.

## The extraction repair, and why it stays visible

`DocumentExtractor._clean_vendor_label` strips a leading caption at the source. It is named to
match the convention `evaluation/run_eval.py` uses to switch repairs off, so the harness
neutralises it automatically along with the two fallbacks.

This matters more than the fix. Repairing the value silently would take the harness to 15/15
while the model carried on returning captions, **deleting the evidence that it happens**. That
is the same failure as the 93.3% reported on the morning of 2026-09-03. Both numbers now stay
true at once:

```
run_eval.py                  10/15   the model, with every repair disabled
run_eval.py --with-fallback  15/15   shipped behaviour
```

## Result, measured 2026-09-03

| Document | Before | After | Empty | Reconciliation |
|---|---|---|---|---|
| invoice 1 | 1.00 Validated | **0.85 Validated** | `items` | unknown |
| invoice 2 | 1.00 Validated | 1.00 Validated | none | exact |
| invoice 3 | 1.00 Validated | 1.00 Validated | none | exact |

Extraction is unchanged at 15/15 shipped and 10/15 model-only. The gate change moved no
extraction number, which is the check that it did what it claimed and nothing else.

Test suite: **194 to 216.** Two existing tests were rewritten rather than renumbered.
`test_the_hard_rule_blocks_even_above_the_threshold` had asserted that a line-item amount
scores 0.88 and is refused anyway; under the new weights that case scores 0.73, so the test no
longer demonstrated its own point. It now uses a **complete** extraction with a captioned
vendor, which scores 0.93 and is still refused. The point survives; the example changed.

## What this still does not catch

**Invoice 1 passes at 0.85 with no line items.** The score records the gap and the reason
names it, but the document is auto-approved.

Catching it properly means detecting that a document *looks* itemised, which needs a heuristic
reading the text for a Qty or Unit Price table. That is precisely the fitted-to-three-generated
files risk in `database-spec.md` §8.1: it would work on ReportLab output, and nobody can say
whether it works on a real invoice. **Deferred until there are real documents to fit against.**

## Consequence: the score scale changed again

`validation_score` is **not comparable across 2026-09-03**, the second such boundary after
2026-08-28. Three eras now exist:

| Era | Invoice 1 | Why |
|---|---|---|
| before 2026-08-28 | 1.00 | no amount verification at all |
| 2026-08-28 to 09-03 | 0.25, later 1.00 | amount verified; extraction improved under it |
| from 2026-09-03 | 0.85 | completeness scored; missing line items cost 0.10 |

Any before-and-after comparison in the report must state which era each number comes from.
