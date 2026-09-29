# Spec: Measure the nullable-required schema

**Status: approved and implemented 2026-09-12. Result at the bottom.**
Written 2026-09-12.

## Why this exists

Three decisions from 2026-09-08, the first two of which conflict:

1. Make the schema required, so fields stop being silently omitted.
2. No invented values, ever. If the model cannot read a field, it must return null.

`evaluation/sentinel_comparison.py` already measured the conflict on `llama3.2`, over five
documents with fields deliberately removed:

| Variant | Extracted correctly | Absent fields admitted | Absent fields invented |
|---|---|---|---|
| `optional` | 1 of 13 | 9 | **0** |
| `required` | **12 of 13** | 6 | 3 |
| `sentinel` | **12 of 13** | 6 | 3 |

Requiring a `str` leaves the model no way to say "not present", so it makes something up.
`Optional` never invents but reads almost nothing. The sentinel string bought nothing at all.

**The proposed resolution is a fourth variant that has never been measured:** declare
`Optional[str]` with **no default**.

```python
invoice_number: Optional[str]          # required=['invoice_number'], type allows null   <- proposed
invoice_number: Optional[str] = None   # required=[]                                     <- ships today
invoice_number: str                    # required, no way to say absent -> invention      <- the 2x2 fix
```

Pydantic puts a field with no default into `required` even when its type is nullable. So the
constrained decoder **must emit the key**, which is the 2026-08-26 omission defect, and **may emit
null**, which is rule 2. If that holds, the variant is strictly better than both existing options
and there is no trade to argue about.

## What gets measured

**One new schema class, two existing scripts, no change to `main.py`.**

### Change 1: `evaluation/sentinel_comparison.py` gains a fourth arm

Add `NullableRequiredInvoice`, identical to `RequiredInvoice` except every field is
`Optional[T]` with no default and no `Field(default=...)`. It joins the existing variant list.
Nothing else in the script changes.

This is the script that matters, because it is the only one that runs on documents where fields
are genuinely absent, which is the only condition under which a model can invent.

### Change 2: `evaluation/prompt_schema_2x2.py` gains a fifth cell

`IMPROVED prompt x Nullable-required schema`, to confirm the variant still reaches 15/15 on
complete documents. The 2x2 stays a 2x2 in the report; this is reported as one extra row.

### Change 3: classify the new arm's failures

Run `evaluation/error_taxonomy.py` over the new results file. The 2026-09-12 taxonomy showed
`required`'s `invented: 3` is really **1 invention and 2 mislocations**, and that distinction is
the safety argument. The new arm gets the same treatment or it is not comparable.

## Predictions, recorded before the code runs

Writing these down first is the reason the date bug in `error-taxonomy-spec.md` was caught.

| Metric | Prediction | What it means if wrong |
|---|---|---|
| Extracted correctly | 12 of 13, matching `required` | Nullability costs accuracy; the trade is real after all |
| Absent admitted | 9 of 9 | The decoder treats nullable-required like plain required |
| Absent invented | **0**, matching `optional` | Rule 2 is unsatisfiable on this model and the group must choose |
| 2x2 cell | 15/15 | The `items` interaction from §5.4.3 is also in play here |

**The result that would kill the proposal** is invented > 0 with accuracy below 12/13. That is
worse than both existing options and the recommendation becomes plain `required` plus the gate.

## What this does NOT measure

- One model. `llama3.2`. The five-model compatibility run says all five accept constrained JSON,
  not that all five behave the same way on nullability.
- n=5 documents, synthetic, with absences placed by hand.
- Nothing about whether a null is better than a wrong value **for the business**. That is the
  gate's question, not the schema's. See `validation-gate-spec.md`.
- Decoder behaviour is not guaranteed stable across Ollama versions. Record the version.

## Deliverable

- `NullableRequiredInvoice` in `sentinel_comparison.py`, and a fifth cell in `prompt_schema_2x2.py`
- Regenerated `results_sentinel_comparison.json` and `results_prompt_schema_2x2.json`
- A fourth row in `report/section-08-evaluation.md` §8.5
- Tests: the new schema's `model_json_schema()` lists every field in `required` **and** admits null.
  That is the whole mechanism, and it is worth asserting rather than trusting.

## What is explicitly out of scope

**Changing `ExtractedInvoice` in `main.py`.** This spec measures a candidate. Shipping it is a
separate decision, because it changes what the pipeline stores and interacts with the validation
gate's completeness weighting. Measure first, then decide.

---

# Result, measured 2026-09-12

`llama3.2`, temperature 0, same five documents. 285 tests pass.

| Schema | Extracted correctly | Absent admitted | Absent invented | Classified errors |
|---|---|---|---|---|
| `optional` | 1 of 13 | 9 of 9 | 0 | 12 |
| `required` | 12 of 13 | 6 of 9 | 3 | 10 |
| `sentinel` | 12 of 13 | 6 of 9 | 3 | 10 |
| **`nullable-required`** | **12 of 13** | **7 of 9** | **2** | **4** |

Fifth 2x2 cell: `IMPROVED prompt x Nullable-required schema` scores **15/15** on the three
complete documents, matching every other improved-prompt cell.

## Predictions against measurement

| Metric | Predicted | Measured | |
|---|---|---|---|
| Extracted correctly | 12 of 13 | 12 of 13 | correct |
| 2x2 cell | 15/15 | 15/15 | correct |
| Absent admitted | 9 of 9 | 7 of 9 | **wrong** |
| Absent invented | 0 | **2** | **wrong** |

**The central prediction failed.** The proposal was that a legal null removes the pressure to
invent. It reduces it and does not remove it. The spec named this outcome in advance as the
one that "kills the proposal" only in combination with accuracy below 12/13; accuracy held, so
the proposal survives on different grounds than the ones it was argued from.

## Where the gain actually came from

The taxonomy, run over the regenerated results:

```
required           6 placeholder,  2 mislocated,  1 invented,  1 malformed   = 10
nullable-required  1 placeholder,  1 mislocated,  1 invented,  1 malformed   =  4
```

**Nullability converted placeholders into nulls. It did not prevent invention.** Five of six
strings such as `"None"` and `"Not specified"` became real JSON nulls. That is a real gain,
because a null is machine-detectable and the string `"None"` is a value that passes any
non-emptiness check, including the one the validation gate currently applies to `date` and
`currency`. It is not the gain that was claimed.

## The surviving invention, which no schema can fix

Every required-family arm returns `2000.00` as the total of a statement that states no total:

```
Opening balance      1,200.00
Payments received      800.00
```

1,200.00 + 800.00 = 2,000.00. **The model is doing arithmetic, not hallucinating.** And the
arithmetic is wrong in kind, since a payment received should be subtracted: the defensible
answer is 400.00 and the correct answer is that no total exists.

This matters beyond this spec. The `mislocated` class is defined by asking whether a value
appears in the document, and that test is what makes a mislocation recoverable. **A computed
value defeats that test by construction.** It is absent from the page, so it classifies as
`invented`, but unlike a hallucinated string it is arithmetically consistent with the page,
which is exactly what makes it convincing. No schema change addresses this, because the model
is obeying the schema precisely.

It belongs to the validation gate, and the gate does not currently catch it either: the
`reconcile` step awards `plausible` when the total exceeds the line-item sum by any amount, and
here the total **equals** the sum of two numbers that are not addends. See
`validation-gate-spec.md` and the five gaps recorded against auto-approval.

## Recommendation

**Adopt `nullable-required`**, on the measured grounds: it matches the best extraction accuracy
of any arm, produces the fewest classified errors of any arm (4 against 10 and 12), and turns
five undetectable placeholder strings into detectable nulls.

**Do not adopt it as satisfying the group's rule 3.** Rule 3 says a value must never be
invented. On this evidence no schema delivers that, and continuing to state rule 3 as
achievable would put a claim in the report that the project's own measurements contradict.

## Still out of scope, unchanged

Changing `ExtractedInvoice` in `main.py`. This measured a candidate. Shipping it changes what
the pipeline stores and interacts with the gate's completeness weighting, which is a separate
decision for the group.
