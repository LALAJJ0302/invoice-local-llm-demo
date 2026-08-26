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

**Heuristic fallbacks are off by default.** `_infer_vendor_fallback` infers a vendor from the
*filename* before falling back to document text. A filename-derived value is a guess, not an
extraction, and counting it as a hit measures the naming convention rather than the model. Use
`--with-fallback` to measure shipped behaviour instead.

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

Identical with `--with-fallback`, because the model returns a vendor on every sample, so
`_infer_vendor_fallback` never fires. It is dead code on this test set. The contamination risk it
poses is latent, not currently active.

Raw results are saved in `results_before.json` and `results_before_with_fallback.json`.

### Reading the result

The failures are not accuracy failures. Every field in `ExtractedInvoice` is `Optional` with a
default, so Pydantic emits an empty `required` list, and Ollama's constrained decoder is free to omit
fields. Pydantic then backfills `None`, `0.0` and `"Unknown"`. The output is indistinguishable from a
model that could not find the values.

The same model, same prompt, same documents, with the fields marked required returns 5/5.
