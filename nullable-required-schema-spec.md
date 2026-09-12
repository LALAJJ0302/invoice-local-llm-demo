# Spec: Measure the nullable-required schema

**Status: awaiting Neo's approval. Nothing implemented.**
Written 2026-09-12. Ollama is running, so this is runnable the moment it is approved.

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
- A fourth row in `report/section-5-evaluation.md` §5.5
- Tests: the new schema's `model_json_schema()` lists every field in `required` **and** admits null.
  That is the whole mechanism, and it is worth asserting rather than trusting.

## What is explicitly out of scope

**Changing `ExtractedInvoice` in `main.py`.** This spec measures a candidate. Shipping it is a
separate decision, because it changes what the pipeline stores and interacts with the validation
gate's completeness weighting. Measure first, then decide.
