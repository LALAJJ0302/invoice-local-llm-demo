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

---

# Amendment, 2026-09-08: verify the date and the currency, and stop accepting the subtotal

## Why this amendment exists

The group decided on 2026-09-08 that a validation score of 1.00 skips the human approve click,
and that anything below 1.00 requires a person. The agreed wording was that 1.00 should mean
"nothing missing, and the extraction matched the original invoice or sender".

That changes what the number has to carry. The previous amendment made 1.00 mean *complete and
internally consistent*, which was the right bar for routing a document to a reviewer. It is not
the right bar for releasing money without one. Three of the five header fields are still either
unverified or verifiable by accident.

This amendment does not implement auto-approval. It makes the score mean what the group said it
means, so the decision can be implemented later without the number lying.

## Defect 1: a subtotal is accepted as the grand total

`TOTAL_LABELS` contains the bare string `"total"`, and the match is `label in line.lower()`.
`"total"` is a substring of `"subtotal"`, so a subtotal line is counted as a grand-total label.

The comment on the constant says the opposite:

> Deliberately narrower than the summary-row labels in storage.py: "subtotal" is excluded,
> because a subtotal sits before tax and must not be accepted as the payable amount.

It is not excluded. Measured:

```
'Subtotal: 1500.00'   -> counted as a total label? True  via ['total']
```

On a document reading Subtotal 1500.00 / GST 150.00 / Grand Total 1650.00, both amounts return
`verified`. An extraction that takes the pre-tax subtotal scores 1.00 and, under the new rule,
would be paid without a human seeing it, short by exactly the tax.

**This is latent, not live.** None of the three mock invoices has a Subtotal line, so no test in
the suite catches it. It fires on the first invoice with GST broken out, which is close to every
Australian invoice, and real documents are the next thing this pipeline is given.

## Defect 2: the date and the currency are never compared to the document

`complete` checks both for non-emptiness. `checks` contains `invoice_number_in_text` and
`vendor_in_text` and nothing for either of these. So at 1.00:

- `date` can be any non-empty value. A hallucinated `2026-01-01` scores exactly what the correct
  date scores.
- `currency` can be any value other than the sentinel `"Unknown"`.

A field that is present and wrong is indistinguishable from a field that is present and right.
That is the same shape as the vendor-caption defect the previous amendment fixed: nothing
compared the value to the document, only that something was there.

Every sample carries both labels in a fixed position, so the check the amount already uses
transfers directly:

```
  5 | Date of Issue: 2026-08-10
  7 | Currency: USD
```

## Defect 3: noted, not fixed here

Line 11 of all three samples is a bare `Total`, the column header of the items table. It is
counted as a label line, so with `LABEL_WINDOW = 2` a bare quantity two lines below it can be
read as a verified amount. Narrowing this needs a notion of where the table ends, which is a
larger change than this amendment, and it is recorded here so it is not rediscovered as new.

## Design

### 1. Label matching gains word boundaries and an explicit subtotal exclusion

Labels match on word boundaries rather than as free substrings, and any line naming a subtotal
is excluded outright regardless of what else it contains. Both are needed: the boundary alone
handles `Subtotal` and `Sub-total`, and the exclusion is what handles `Sub Total` written with
a space, where `total` is a genuine standalone word.

Measured against the boundary rule alone:

```
'Subtotal: 1500.00'    -> False      correct
'Sub-total 1500.00'    -> False      correct
'Sub Total 1500.00'    -> True       wrong, hence the exclusion
'Grand Total: 2650.00' -> True
'Total: 1650.00'       -> True
'Balance Due 900.00'   -> True
```

**Correction, found by measuring before shipping.** Excluding subtotal lines from the label
list is not sufficient, and the first version of this spec said it was. On the ordinary
Subtotal / GST / Grand Total block the subtotal *amount* sits two lines above the grand-total
*label*, which is inside `LABEL_WINDOW`, so it was still returned as `verified`, borrowed from
a label belonging to a different figure:

```
6 | Subtotal:      1500.00     <- amount here
7 | GST 10%:        150.00
8 | Grand Total:   1650.00     <- label here, distance 2
```

The rule therefore has two halves. A line naming a subtotal is not a label, **and** an amount
found on a line naming a subtotal is rejected outright whatever sits near it. With both, 1500.00
returns `absent` and 1650.00 returns `verified`.

This is the reason the preview script exists rather than a patch going straight into `main.py`.
The wrong fix passed inspection and failed measurement.

### 2. The date is verified the way the amount is

`verify_date` returns `verified` / `present` / `absent` on the same rule as `verify_amount`:
`verified` means the extracted date appears within `LABEL_WINDOW` lines of a date label,
`present` means it appears somewhere in the document but not beside one, `absent` means it does
not appear at all. `absent` is the case that matters, because it is invention.

Date labels are narrower than the amount's, and deliberately exclude anything meaning a due
date or a payment date, because those are different values that would both satisfy a loose match.

### 3. The currency is verified the same way

Same three outcomes. A currency code is short enough that a bare substring search would match
almost anything, which is exactly the weakness already noted in the invoice-number check, so
label proximity is the only form of this check worth having.

### 4. Weights

The two new checks need room, and the total must still be exactly 1.00 so that a perfect score
remains reachable. **The final numbers are not fixed by this spec.** They depend on what the new
checks actually score against the samples, which `evaluation/gate_verification_preview.py`
measures without touching the gate. Picking weights before that measurement would be choosing
numbers to produce a result rather than to describe one.

The constraint the weights must satisfy: no reweighting may raise the score of an extraction
that the current gate scores lower. This amendment can only be stricter.

### 5. Hard rules

Whether `date_state` and `currency_state` join the hard-rule list alongside `amount_state` is
**deliberately left open until measured.** Adding them could take the samples from 3/3 Validated
to 0/3, which would be a correct outcome if the dates genuinely cannot be verified and a bad one
if the label list is merely too narrow. The measurement decides which, and the spec should not
guess.

## What this still does not fix

Everything in this section survives the amendment, and the group should read it before treating
1.00 as licence to pay.

- **Line item contents are never compared to the document.** `has_items` asks only whether the
  list is non-empty. `reconcile` compares the sum, and awards `plausible` the same weight as
  `exact`, where `plausible` means the total exceeds the sum by any amount at all. One invented
  ten-dollar row on a two-thousand-dollar invoice scores full marks.
- **The vendor check cannot tell the vendor from the customer.** Both names are in the document,
  so extracting the "Bill To" party passes.
- **Every check here is document-internal.** A duplicate of an invoice already paid, an invoice
  from a vendor never ordered from, and a well-formatted fraud all score 1.00, because each is a
  document that agrees with itself. The score measures reading accuracy, not payment authority.
  Nothing in this file can close that gap; only a check against data outside the document can,
  which is what the sender match and the duplicate check are for.
- **n = 3, synthetic, one layout.** All three samples put the date on line 5 and the currency on
  line 7 with identical labels. A check tuned on them proves it can read this generator's output
  and nothing more. Real invoices are the test.

## How to know it worked

```bash
./.venv/bin/python evaluation/gate_verification_preview.py   # what the new checks would score
./.venv/bin/python -m pytest tests/ -q                       # nothing existing regressed
./.venv/bin/python evaluation/run_eval.py                    # extraction accuracy unchanged
```

The preview script is additive and reads only. It must be able to report the new states without
`main.py` changing at all, so the numbers in the decision are measured before the decision is
made.

## Consequence: the score scale changes a third time

`validation_score` will not be comparable across 2026-09-08, for the same reason it is not
comparable across 2026-08-28 or 2026-09-03. Three eras become four. Any figure quoted in the
report has to carry the date it was measured on.

## Result, measured 2026-09-08

```bash
./.venv/bin/python evaluation/gate_verification_preview.py --save results_gate_verification.json
```

Every check is run twice, once on the transcribed ground truth and once on a deliberately
invented value, because a check that accepts the truth and also accepts an invention has
measured nothing.

```
sample                             field     today      true      invented  verdict
sample_invoice_1_INV-2026-001.pdf  date      non-empty  verified  absent    pass
sample_invoice_1_INV-2026-001.pdf  currency  non-empty  verified  absent    pass
sample_invoice_2_INV-2026-002.pdf  date      non-empty  verified  absent    pass
sample_invoice_2_INV-2026-002.pdf  currency  non-empty  verified  absent    pass
sample_invoice_3_INV-2026-003.pdf  date      non-empty  verified  absent    pass
sample_invoice_3_INV-2026-003.pdf  currency  non-empty  verified  absent    pass

accepts the true value:   date 3/3   currency 3/3
rejects the invented one: date 3/3   currency 3/3
```

The `today` column is the finding. The gate reports `non-empty` for the correct date and would
report `non-empty` for `2026-01-01` as well. It cannot tell them apart, and at 1.00 that
difference is the difference between a read invoice and an invented one.

Subtotal regression, on a document reading Subtotal 1500.00 / GST 150.00 / Grand Total 1650.00:

```
amount       today        proposed
1650.00      verified     verified     correct, this is the payable amount
1500.00      verified     absent       fixed, this is the pre-tax subtotal
```

No regression on the real samples. All three totals that verify today still verify:

```
sample_invoice_1_INV-2026-001.pdf  1500.00  verified -> verified
sample_invoice_2_INV-2026-002.pdf  2650.00  verified -> verified
sample_invoice_3_INV-2026-003.pdf  2350.00  verified -> verified
regressions: 0
```

**What these numbers do and do not support.** They show the checks are implementable and that
they separate a true value from an invented one on this generator's layout. They do not show the
checks work on real invoices, because n = 3 and all three put the date on line 5 and the currency
on line 7 with identical labels. A check tuned on them proves it can read this generator's output.
The date comparison is also deliberately loose: it compares digit groups as a set, so it cannot
tell 08-10 from 10-08. That is recorded rather than hidden, and it is the first thing to test when
real documents arrive.

## Open, pending approval

The weights and the hard rules are still not set, and the measurement above does not settle them
on its own. Both are decisions rather than findings:

- whether `date_state` and `currency_state` join `amount_state` in the hard-rule list, which
  would make an unverifiable date block auto-approval outright
- how the 1.00 is redistributed to make room for two new checks, under the constraint that no
  extraction may score higher than it does today

Nothing in `main.py` has been changed. The preview script is additive and reads only.
