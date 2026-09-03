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
