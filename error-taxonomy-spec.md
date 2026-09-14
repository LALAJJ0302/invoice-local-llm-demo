# Spec: Extraction Error Taxonomy

**Owner:** Neo. **Item 15**, the second half of JJ's item 3 ("analyse extraction errors").
**Status:** **implemented 2026-09-12** in `evaluation/error_taxonomy.py`, with 23 tests in
`tests/test_error_taxonomy.py`. Spec written 2026-09-08. Section 7 records what changed between
the two, including one bug the implementation found in this document's own rules.

`model-evaluation-spec.md` item 3 says "counting right and wrong is not analysis" and lists six
error classes. Nothing implements it. This spec turns that list into a classifier that runs over
results already on disk, with a mechanical rule per class and no model re-run.

---

## 0. Why this is worth building, stated as a finding rather than a plan

The classification can be done by hand for the current dataset, and doing it by hand first proved
the tool is worth writing. `evaluation/sentinel_comparison.py` reports this line:

```
absent fields admitted, not invented   6/9      invented: 3
```

Three fields were filled in on documents where the field genuinely does not exist. Read the three
values against the document text they came from:

| Document | Field | Value | Is it in the document text? |
|---|---|---|---|
| `cafe_receipt` | invoice_number | `Table 4` | **Yes.** The receipt says "Table 4" |
| `sparse_note` | currency | `dollars` | **Yes.** The note says "45 dollars" |
| `no_total` | total_amount | `2000.0` | **No.** That document holds only 1,200.00 and 800.00 |

So "invented: 3" is one invention and two mislocations. They are not the same failure and they do
not have the same fix. A mislocation means the model found a real string and put it in the wrong
field, which better field descriptions or a verification check can catch, because the value is on
the page. An invention means the model produced a plausible dollar amount that appears nowhere in
the document, which no substring check can catch after the fact and which the gate cannot verify.

**The single invented number is the most dangerous result this project has recorded**, and it is
currently reported inside a count of three alongside two errors of a different kind.

That is the argument for the taxonomy in one table, and it corrects a number our own documents
already quote.

## 1. What data exists, corrected

The 2026-09-08 resume note said `results_prompt_schema_2x2.json` is the only file with enough
detail. Checked by reading every results file: **five files carry per-field expected/actual**.

| File | Detail | Useful for the taxonomy? |
|---|---|---|
| `results_prompt_schema_2x2.json` | expected/actual, 4 arms | Weakly. **Every error in it is `null`**, so it populates one class |
| `results_before.json` | expected/actual + score + status | Yes. Holds `0.0` and `'Unknown'`, the schema defaults |
| `results_before_with_fallback.json` | same shape | Yes, same rows |
| `results_schema_comparison.json` | expected/got, 2 variants | Weakly. All errors are `null` |
| `results_sentinel_comparison.json` | expected/got, present and absent blocks, 3 variants | **Yes. This is the interesting one** |
| `results_model_compatibility.json` | counts only | No |
| `results_retrieval.json` | ranking, not extraction | No |
| `results_gate_verification.json` | gate verdicts, not extraction | No |

The 2x2 is the wrong starting point. It ran on three well-formed invoices where the model either
got the field or returned null, so it produces a taxonomy with one populated class. **The sentinel
comparison is the right starting point**, because it ran on documents with fields deliberately
missing, which is the only condition under which a model has the opportunity to invent.

## 2. The classes and the rule that assigns each one

Applied to a field where `correct` is false, or where `admitted` is false. Rules run in order and
the first match wins, so every error lands in exactly one class.

| # | Class | Rule |
|---|---|---|
| 1 | `omitted` | Value is `null` or the key is absent |
| 2 | `placeholder` | Value is a no-value token: `'None'`, `'Not specified'`, `'Unknown'`, `'N/A'`, `''`, or numeric `0.0` |
| 3 | `malformed` | Normalising the value to the field's canonical form makes it equal the expected value. `'12 March 2026'` for `2026-03-12` |
| 4 | `caption` | Value starts with a field label from `ConfidenceValidator.VENDOR_LABELS`, and stripping that prefix makes it correct |
| 5 | `truncated` | Value is a strict, non-empty prefix of the expected value. `'Apex Cloud'` for `'Apex Cloud Solutions Pty Ltd'` |
| 6 | `mislocated` | Value appears in the document text but is not the expected value for this field |
| 7 | `invented` | Value does not appear in the document text at all |

Ordering matters and is deliberate. `'None'` is a substring of nothing useful and would fall to
`invented` without rule 2, which would overstate the count of the most serious class. `caption` is
tested before `mislocated` because a caption is technically also in the document.

Rules 6 and 7 need the document text. Rules 1 to 5 do not.

**Every rule is mechanical.** No error is classified by reading it and deciding. This follows the
relevance rule in `retrieval_eval.py`: if a human judgement is needed, the measurement is not
reproducible by the person marking it.

### Where the document text comes from

| Source file | Documents |
|---|---|
| `results_sentinel_comparison.json` | `sentinel_comparison.load_documents()`, imported, not copied |
| everything else | `evaluation/samples/*.pdf` read through `pypdf`, the same path the pipeline uses |

Numeric values are matched against the text both bare and comma-grouped, so `2000.0` is searched
as `2000` and `2,000`. Otherwise the `no_total` invention would be indistinguishable from a
formatting difference.

## 3. What the classification produces

**Measured**, by running `evaluation/error_taxonomy.py`. The hand-written predictions this section
carried before implementation are kept in section 7 against what actually came out.

```
arm                                       | omitted | placehold | malformed | caption | truncated | mislocate | invented | total
2026-08-26 baseline, repairs off          |       6 |         6 |         . |       . |         . |         . |        . |    12
2026-08-26 baseline, repairs on           |       6 |         6 |         . |       . |         . |         . |        . |    12
Optional with defaults (what ships today) |      12 |         . |         . |       . |         . |         . |        . |    12
Required fields (the proposed change)     |       . |         . |         . |       . |         . |         . |        . |     0
ORIGINAL prompt + Optional schema         |       9 |         . |         . |       . |         . |         . |        . |     9
ORIGINAL prompt + Required schema         |       . |         . |         . |       . |         . |         . |        . |     0
IMPROVED prompt + Optional schema         |       . |         . |         . |       . |         . |         . |        . |     0
IMPROVED prompt + Required schema         |       . |         . |         . |       . |         . |         . |        . |     0
sentinel: optional schema                 |      11 |         . |         1 |       . |         . |         . |        . |    12
sentinel: required schema                 |       . |         6 |         1 |       . |         . |         2 |        1 |    10
sentinel: sentinel schema                 |       . |         6 |         1 |       . |         . |         2 |        1 |    10
```

Three results hold the whole argument.

**The required schema trades omission for invention, and the taxonomy prices the trade.** The
Optional arms fail 100% by `omitted`: the model answers null and nothing false is stored. The
required arms omit nothing and instead produce 6 placeholders, 2 mislocations and 1 invention.
Both arms score similarly by field accuracy. They are not similarly safe, and no accuracy column
can show that.

**The one invention survives classification.** `2000.0` for a document whose only numbers are
1,200.00 and 800.00. It is the single value in every result this project has saved that could be
written into an approved invoice record without appearing anywhere in the source document.

**The 2x2 populates exactly one class**, which is asserted by a test rather than claimed here.
Three well-formed invoices give a model no opportunity to invent, so the file the 2026-09-08
resume note named as the starting point could not have produced this table.

### What counts as an error for an absent field

`sentinel_comparison.py` asks whether an absent field was `admitted`, and accepts the string
`'Not specified'` as an honest refusal. This tool asks a stricter question: the only right answer
for an absent field is null, so any non-null value is an error, and `'Not specified'` is class
`placeholder`.

Both are defensible and they count differently on purpose. The reason for the stricter rule is
that the pipeline stores what it gets: `vendor_name = 'Not specified'` reaches SQLite as text and
reads as a company name to the dashboard, the approval flow and any later query. A NULL does not.
The 6 placeholders per required arm are the size of that problem, and the older `admitted 6/9`
line does not show it.

### The two empty classes are reported as zero, not dropped

`caption` has zero instances in the saved results even though it is a real failure this project
saw: `_clean_vendor_label` at `main.py:172` exists because the model returned "Vendor: Apex Cloud
Solutions Pty Ltd" on 2026-09-03. The results files that would have recorded it predate the repair
or were run with the repair on. **A class with a zero count and a named reason is a finding.** A
class quietly removed because it was inconvenient is not.

`truncated` has never been observed here at all. It stays in the table so a future model that
truncates is classified rather than being absorbed into `invented`.

Both are pinned by `test_caption_and_truncated_are_empty_in_the_saved_results`, so if a later run
populates either, the suite fails and the claim in the report has to be rewritten rather than
quietly going stale.

## 4. What this does NOT measure

Following the section of the same name in `evaluation/evaluation-method.md`.

- **n is tiny.** 77 classified errors over 8 documents, and both the documents and several whole
  arms repeat. Class counts are illustrative, not rates.
- **One model.** Everything on disk is `llama3.2:latest`. "Which models fail in which way" is the
  finding item 3 asks for and it needs `model_comparison.py`, which does not exist. This spec
  builds the classifier that comparison would use, not the comparison.
- **Arms are not independent.** Two pairs of arms are byte-identical: the sentinel and required
  schemas produced the same output, and `results_before.json` and
  `results_before_with_fallback.json` hold the same rows despite one claiming repairs enabled and
  the other disabled. The script detects both, prints them under "Identical arms", and labels its
  cross-arm total as double-counted. **Cite per-arm numbers, not the total.**
- **Classification only, no cost weighting.** The claim that invented is worse than omitted is an
  argument in this document, not a number the script computes.
- **Extraction only.** Classification and summarisation errors are still out of reach for the
  reasons in `model-evaluation-spec.md` item 3. Unchanged.

## 5. Deliverable, as built

`evaluation/error_taxonomy.py`. Reads only. Imports `sentinel_comparison` for its documents and
`pypdf` for the sample PDFs. **Touches no shipped file, re-runs no model, needs no Ollama.**

```
./.venv/bin/python evaluation/error_taxonomy.py
./.venv/bin/python evaluation/error_taxonomy.py --detail
./.venv/bin/python evaluation/error_taxonomy.py --save results_error_taxonomy.json
```

`--detail` prints every error with the rule that fired and the value that triggered it, so any
single call can be checked by hand against the document. That is the output to put in an appendix.

`tests/test_error_taxonomy.py`, 23 tests: one per class, the three ordering cases, the
normalising the rules depend on, and six that run over the real saved results so the claims in
section 3 fail the suite rather than going stale. Full suite is **271 passing**, up from 248.

## 6. The one decision this needs

`sentinel_comparison.py` prints `invented: 3`. If the taxonomy is right, that line is wrong: it is
1 invented and 2 mislocated. Options:

1. **Leave `sentinel_comparison.py` alone.** The taxonomy supersedes it and the report cites the
   taxonomy. Nothing breaks, and the older number stays on disk to be contradicted.
2. **Rename its counter to `not_admitted`**, which is what the check actually measures, and point
   at the taxonomy for the breakdown. One word, one line of output, no logic change.

Neo's call. Option 2 is more honest and costs almost nothing, but it edits a file whose results
are already quoted in `report/section-5-evaluation.md`, so the report text has to move with it.

---

## 7. What implementation changed, and one bug it found in this spec

### The date rule was wrong as specified, in a way that mattered

Rule 3 says a value is `malformed` when it normalises to the expected value. The first
implementation normalised dates with `dateutil.parser.parse(value, dayfirst=True)`, on the
reasoning that the documents are Australian and `03/04/2026` is 3 April.

`dateutil` applies `dayfirst` to the last two components whatever the shape of the string, so it
reads the ISO ground-truth value `2026-03-12` as **3 December 2026**. The expected value was being
corrupted before the comparison. The `'12 March 2026'` case, the only `malformed` instance in the
entire dataset, was silently classified `mislocated` instead, and the first run of the tool
printed `malformed 0`.

Nothing in the rule was wrong. The normaliser the rule depends on was. `_as_date` now returns an
already-ISO value untouched and only reaches `dateutil` for everything else, pinned by
`test_iso_ground_truth_is_not_reordered`.

Worth recording because it is the taxonomy's own failure mode: a tool for finding silently-wrong
values was itself silently wrong, and the only reason it was caught is that section 3 predicted a
count before the code ran. **Predicting the number first is what found it.**

### Predictions against measurement

| Class | Predicted 2026-09-08 | Measured | Why they differ |
|---|---|---|---|
| `omitted` | ~30 | 44 | Prediction skipped the duplicate arms rather than counting them |
| `placeholder` | ~8 | 24 | Prediction missed that the absent-field placeholders repeat across three arms |
| `malformed` | 3 | 3 | Found only after the dateutil fix above |
| `mislocated` | 4 | 4 | |
| `invented` | 2 | 2 | |
| `caption` | 0 | 0 | |
| `truncated` | 0 | 0 | |

The two loose predictions were loose because they were written as `~`. The five exact ones were
exact. The classes that carry the argument are all in the second group.

### Smaller departures

- **Four results files are skipped, and the tool names them and why** rather than silently
  reading what it can. `results_model_compatibility.json` and `results_retrieval.json` hold counts
  and rankings, `results_gate_verification.json` measures the gate rather than the extraction.
- **Rule 5 lost a redundant clause.** The spec's two conditions for `truncated` were the same
  condition written twice.
- **`results_before.json` and `results_before_with_fallback.json` turned out to be identical**,
  which the spec did not anticipate. The duplicate detector reports it. Whether that run was
  mislabelled or the repairs genuinely changed nothing that day is **an open question for whoever
  produced those files**, not something this tool can settle.
