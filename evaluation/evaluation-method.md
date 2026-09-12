# Evaluation Method

Measures per-field extraction accuracy of the pipeline against hand-transcribed ground truth.

Added 2026-08-26. Reads nothing from `inbox/` or `archive/` and modifies no existing file in the
repository, so it can be run at any time without disturbing the live pipeline.

## Why this exists

The project had no quantitative evidence about its own core function. Progress was reported as
"processed successfully", which evidences that the pipeline ran without crashing, not that it
extracted correctly. Those are different claims and only one of them is testable.

## Design decisions

**Ground truth is transcribed independently, not imported from `generate_mock_invoices.py`.**
Deriving expected values from the generator would test the generator against itself and would pass
even if both were wrong. `ground_truth.json` is a separate artifact read from the rendered PDFs.

**Heuristic fallbacks are off by default.** A fallback is our own code recovering a value the
model did not return. Counting one as a hit measures our patches rather than the model, so the
default run switches them all off. Use `--with-fallback` to measure shipped behaviour instead.

**The switch covers every method matching `_infer_*_fallback`, found at runtime.** It is not a
list. It used to be a list of one, and on 2026-09-03 a second fallback was added to `main.py`;
the harness kept printing "fallbacks: disabled" while that fallback ran, and reported 93.3% for
a model scoring 66.7%. A hardcoded list cannot know about a fallback added after it was written.
If nothing matches the pattern the harness **refuses to run**, because printing an accuracy figure
that claims fallbacks are disabled, having disabled nothing, is the failure being prevented.

The output names what it switched off rather than asserting a state, so a pasted result can be
checked by whoever reads it:

```
=== Extraction accuracy: llama3.2 ===
    fallbacks disabled: _infer_missing_fields_fallback, _infer_vendor_fallback
```

Currently covered: `_infer_vendor_fallback` (vendor from filename or header) and
`_infer_missing_fields_fallback` (invoice number, date, total and currency by regular
expression).

**Fallbacks are disabled by monkey-patching at runtime.** `main.py` is not edited, so this harness
measures the code as committed.

**Samples live in `evaluation/samples/`, not `inbox/`.** `main.py` moves files out of `inbox/` as it
runs, so a harness pointed there would consume its own test set. Samples regenerate automatically if
missing, and are gitignored because they are reproducible from the generator.

## Scoring

A field counts as correct only on an exact match after normalisation:

| Field | Comparison |
|---|---|
| `vendor_name`, `invoice_number`, `currency` | casefolded, whitespace collapsed |
| `date` | compared on digits only, so `2026-08-10` and `10/08/2026` both match |
| `total_amount` | numeric, tolerance 0.01 |

A missing field (`None`, `0.0`, `"Unknown"`) counts as incorrect. That is deliberate: from the point
of view of someone relying on the output, a silently absent value and a wrong value cost the same.

## What this does NOT measure

Stated plainly, because the numbers are easy to over-read.

- **n = 3.** Three synthetic invoices from one generator, so one layout. This is a smoke test with
  ground truth, not a generalisable accuracy estimate.
- **Easiest possible input.** The samples have a clean text layer and label their fields explicitly
  (`Vendor:`, `Invoice Number:`, `Date of Issue:`, `Currency:`). Real invoices do not. A high score
  here would prove very little. A zero score is still decisive, since failure on the easy case
  implies failure on the hard one.
- **No scanned documents.** `pypdf` cannot read image-based PDFs at all, so the OCR path is entirely
  outside these numbers.
- **Single run per configuration.** Temperature is 0, but decoding is not guaranteed deterministic.
  Repeat trials would be needed for any variance claim.
- **Ground truth transcribed once, by one person.** Not double-coded.

## Running it

Ollama must be serving (`ollama serve`).

```bash
python evaluation/run_eval.py                      # fallbacks off (default)
python evaluation/run_eval.py --with-fallback      # shipped behaviour
python evaluation/run_eval.py --model llama3.2     # compare models
python evaluation/run_eval.py --save results.json  # record for before/after
```

## Baseline result, 2026-08-26

Against `main.py` as committed at `6ef6798`, model `llama3.2`:

```
Field              Correct    Accuracy
--------------------------------------
vendor_name            3/3      100.0%
invoice_number         0/3        0.0%
date                   0/3        0.0%
total_amount           0/3        0.0%
currency               0/3        0.0%
--------------------------------------
OVERALL               3/15       20.0%

Gate outcome: 0/3 Validated (0.0% automation pass rate, threshold 0.8)
```

Identical with `--with-fallback` **as measured on 2026-08-26**, because the model returned a
vendor on every sample, so `_infer_vendor_fallback` never fired.

Raw results are saved in `results_before.json` and `results_before_with_fallback.json`.

**These two files differ in exactly one byte-range: the `fallbacks` label reads `disabled` in one
and `enabled` in the other. Every row is identical.** Confirmed 2026-09-12 by diffing them. That
looks like a mislabelling and is not one, for a reason worth recording: on 2026-08-26 the only
repair that existed was `_infer_vendor_fallback`, and it never fired because the model returned a
vendor on all three samples. **The regex fallback for the header fields did not exist yet.** It
arrived on 2026-09-03 in commit `6ff1dbb`, a week after these files were frozen. So there was
genuinely nothing for the `--with-fallback` run to do.

Both files were committed on 2026-08-26 in `c3b4d26` and have never been regenerated, because
`--save` is opt-in. They are frozen snapshots. Do not read them as a measurement of what the
repairs are worth today: for that, see the 10/15 against 15/15 pair in the table below.

### This baseline is historical, not current

`main.py` changed on 2026-09-03: the extraction prompt was rewritten and a regex fallback added.
**The 3/15 above was measured against the previous prompt and no longer describes the code in the
tree.** It stays here because the report needs it and it is a real measurement, but it must be
cited as the 2026-08-26 baseline rather than as current behaviour.

Two claims in the paragraph above are also no longer true of the current code. The model does not
return a vendor on every sample: invoice 1 now returns `None`, reproducibly. And
`_infer_vendor_fallback` is no longer dead code on this test set, because it fires on that sample.

Four states have now been measured, all reproducible:

| State | Overall | What it is |
|---|---|---|
| 2026-08-26 baseline | 3/15 | Previous prompt, `Optional` schema, no field fallback |
| Model alone, 2026-09-03 | 10/15 | Current prompt, every fallback off, identical across three runs |
| Shipped, 2026-09-03 | 14/15 | Current prompt plus the regex fallback |
| Required fields | 15/15 | `schema_comparison.py`. Measured, **not shipped** |

The gap between rows one and two is the prompt. The gap between two and three is our regex, not
the model. The gap between two and four is the schema. Reporting only the last number would
attribute all three to the same cause.

### Reading the result

The failures are not accuracy failures. Every field in `ExtractedInvoice` is `Optional` with a
default, so Pydantic emits an empty `required` list, and Ollama's constrained decoder is free to omit
fields. Pydantic then backfills `None`, `0.0` and `"Unknown"`. The output is indistinguishable from a
model that could not find the values.

The same model, same prompt, same documents, with the fields marked required returns 5/5.
