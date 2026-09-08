# 5. Evaluation and Testing

## 5.1 What was measured, and why it was measured this way

The project's claim is that a locally hosted language model can extract structured data
from invoices reliably enough to be worth automating. That claim is only meaningful if it
can be checked, so the evaluation was built before the improvements it later measured.

Three properties were designed in from the start.

**Ground truth is transcribed independently.** The expected values in
`evaluation/ground_truth.json` were read from the rendered PDFs by a person, not imported
from `generate_mock_invoices.py`, the script that produced them. Deriving them from the
generator would test the generator against itself: if both the generator and the extractor
were wrong in the same way, the evaluation would report success.

**The harness measures the model, not the system.** The pipeline contains several pieces of
our own code that repair the model's output: a filename heuristic for the vendor, a set of
regular expressions for the header fields, and a caption stripper. Counting those as
extraction successes would measure our patches. The harness therefore disables every repair
by default, and reports both figures separately.

**Repairs are discovered, not listed.** This became important on 3 September 2026 and is
discussed in §5.7.

The unit of measurement is a **field-value**: three documents with five fields each gives
fifteen. All figures below are out of fifteen.

## 5.2 What this evaluation does not measure

Stated here rather than in the limitations section, because a reader needs it before the
numbers rather than after them.

- **n = 3.** Three documents is too few to distinguish a real difference from chance. By
  comparison, SROIE, a standard benchmark for the same task, uses 973 real scanned receipts
  and labels four fields where we label five.
- **The documents are synthetic.** They were generated with ReportLab and have a text
  layer, consistent spacing, and no logos, stamps, multi-column layouts or handwriting.
- **There is no OCR path.** Scanned or photographed invoices are skipped entirely, because
  `pypdf` requires a text layer. That is a large and common class of real invoice.
- **Language models are not deterministic.** Repeated runs of an identical configuration
  can differ, so a small difference between two figures is not necessarily a real one.
- **The gate's heuristics were fitted to these three documents.** `LABEL_WINDOW = 2`, the
  rule that an amount must sit within two lines of a total label, holds because ReportLab
  puts them there. An invoice placing the amount in a table cell would fail it.

None of these invalidate the findings below. They bound them, and the bounds are wide.

## 5.3 The baseline

Measured 26 August 2026, frozen in `evaluation/results_before.json`, and still reproducible
today with `evaluation/schema_comparison.py`:

```
invoice_number   0/3
vendor_name      3/3
date             0/3
total_amount     0/3
currency         0/3
                ----
OVERALL          3/15   (20%)

Validation gate: 0/3 Validated, 0% automation pass rate
```

Only the vendor name was extracted, and even that was partly recovered by a filename
heuristic rather than by the model.

The obvious reading of a 20% result is that the model is too small for the task. **That
reading is wrong**, and the rest of this section is the evidence.

## 5.4 Isolating the cause

### 5.4.1 The first comparison

A controlled comparison was built holding the model, the prompt, the documents and the
ground truth constant, and varying one thing: whether the Pydantic schema declared its
fields `Optional` with defaults, as shipped, or required.

```
Optional with defaults (as shipped)      3/15   (20%)
Required fields                         15/15  (100%)
```

The mechanism is specific and not obvious. Ollama applies constrained decoding using
XGrammar, which operates at token level: the grammar determines which tokens may come next,
and invalid tokens are masked during sampling. A Pydantic model whose fields are all
`Optional` emits an **empty `required` list** in its JSON Schema. Nothing then masks the
closing brace, so terminating the object early is a legal path through the grammar. The
model stops, and Pydantic's defaults silently backfill `None`, `0.0` and `"Unknown"`.

The failure is therefore invisible at every layer. The model returns valid JSON. Pydantic
validates it. The database stores it. Nothing errors.

This positions the finding inside existing work rather than beside it: JSONSchemaBench
(Geng et al., 2025) benchmarks six constrained-decoding frameworks including XGrammar
across 10,000 real-world schemas, and one of its three dimensions is precisely coverage of
constraint types.

### 5.4.2 The second comparison, which corrected the first conclusion

The comparison above varies the schema while holding the prompt fixed. That establishes the
schema as **a** cause. The project reported it as **the** cause.

On 8 September 2026 the prompt was rewritten independently, and a full two-by-two was run:

| Prompt | Schema | Result |
|---|---|---|
| Original | Optional | 6/15 |
| Original | Required | 15/15 |
| Improved | Optional | 15/15 |
| Improved | Required | 15/15 |

Three things follow.

1. **The schema effect is real.** Changing only the schema moves the result to 15/15.
2. **The prompt effect is equally large.** Changing only the prompt moves it to 15/15.
3. **They are not additive.** Either fix alone reaches the ceiling; together they add
   nothing.

The correct statement is therefore that **the failure required both the original prompt and
the permissive schema at once**, and either change alone removes it. The earlier claim, that
the schema was the cause and prompt engineering was not the issue, was too strong and has
been withdrawn.

This is a stronger result than the one it replaces. A single-cause explanation from a
single comparison is a weaker piece of evidence than a two-by-two that identifies an
interaction and quantifies both factors.

### 5.4.3 An unresolved discrepancy

The Original-Optional cell reads **6/15** in the two-by-two and **3/15** in
`schema_comparison.py`, which still reproduces exactly. The difference is entirely in the
`currency` field: 0/3 in one, 3/3 in the other.

The cause has not been identified. It may be run-to-run variation in the decoder, or a
difference in how the two scripts construct their calls. **Both figures are reported here
rather than one being chosen**, because selecting the more convenient of two numbers that
have not been reconciled is not a measurement.

## 5.5 What the fix costs

A fix that improves a headline number while making behaviour worse elsewhere has not been
evaluated until that second effect is measured.

Nine of the fields across the three documents are genuinely absent from the source
documents. Under each schema:

```
Optional schema:   admitted 9,  invented 0
Required schema:   admitted 6,  invented 3
```

Requiring fields forces the model to emit a value it cannot find. **It invents one for a
third of the genuinely absent fields.** The gain is real and so is the cost: blank fields
are exchanged for confident wrong ones, which are harder to detect downstream.

A mitigation was proposed, where the schema requires a sentinel string such as
`"NOT_FOUND"` rather than a value. It was measured and **performs identically to plain
required fields**: 12 of 13 extracted correctly, 3 of 9 absent fields invented. It was
therefore rejected. Recording a rejected hypothesis matters as much as recording the
accepted one, since it is the part that shows the alternatives were tested rather than
assumed away.

## 5.6 Model comparison

Six models were shortlisted against two constraints: they must fit in the usable memory of
the target machine (Apple M4, 24 GB unified, of which roughly 16 to 18 GB is available once
the operating system and dashboard are resident), and they must support Ollama's `format`
parameter for constrained decoding.

The shortlist varies two factors deliberately, so that the comparison answers questions
rather than producing a ranking: **size within a family** (`llama3.2:3b` against
`llama3.1:8b`) and **family at comparable size** (`gemma3:4b` against `llama3.2:3b`).

Compatibility was measured before accuracy, because a model that cannot return valid JSON,
or that ignores `required`, fails in exactly the way the schema defect does, and the two
would otherwise be indistinguishable.

| Model | Valid JSON | Fields under `required` | Seconds per call |
|---|---|---|---|
| llama3.2:3b | 3/3 | 5.0/5 | 2.1 |
| gemma3:4b | 3/3 | 5.0/5 | 4.0 |
| llama3.1:8b | 3/3 | 5.0/5 | 4.7 |
| qwen2.5:7b | 3/3 | 5.0/5 | 5.1 |
| phi4:14b | 3/3 | 5.0/5 | 9.2 |

**Every model handled constrained decoding correctly.** No model returned malformed JSON,
and none ignored `required`. A published comparison reporting a 27B model returning empty
JSON under constrained output was not reproduced on any model in this shortlist, and is
therefore not relied on.

The variable that separates these models is **latency**. `phi4:14b` is 4.4 times slower
than `llama3.2:3b` and, on this task, no more accurate. That is the justification for
running the smallest model in the pipeline: it is a measured decision, not a default.

## 5.7 What testing found

A test suite of 242 automated tests covers the storage layer, the migration chain, the
validation gate and the retrieval module. It has repaid its cost three times.

**A constraint that rejected every valid date.** The `invoices` table declared
`CHECK (invoice_date GLOB '____-__-__')`. In SQLite's `GLOB`, `_` is a literal underscore;
the single-character wildcard is `?`. The constraint therefore rejected every real date and
accepted only literal underscores. It had never fired, because the extractor returned
`NULL` for every date. **It would have fired on the first insert after extraction was
fixed, and would have appeared to be the extraction fix breaking the database.**

**An evaluation harness reporting a figure it could not support.** The harness disabled
repairs by neutralising one method by name. On 3 September 2026 a second repair was added
to the extractor. The harness did not know it existed, so it continued to print
`fallbacks: disabled` while that repair filled four of five fields, reporting **93.3% for a
model that scores 66.7%**. Anyone running the command that week would have recorded a false
figure in good faith. The switch now discovers every repair by naming convention and
**refuses to run if it finds none**, on the grounds that printing an accuracy figure while
claiming repairs are disabled, having disabled nothing, is the failure being prevented.

**A validation gate passing a document it existed to catch.** An extraction returning a
vendor of `'Vendor: Apex Cloud Solutions Pty Ltd'` and zero line items scored **1.00,
Validated**. The gate's agreement check tested whether the extracted vendor appeared as a
substring of the document. Because the model had copied the caption along with the value,
the wrong answer satisfied the check **more easily than the right one would have**: the
check becomes easier to pass the more of the document is copied, so a lazier extraction
scored higher. The gate now treats a perfect score as meaning a complete extraction, and
names which fields were empty.

All three defects share a shape. **None produced an error.** Each was a component behaving
exactly as written, where what was written was wrong.

## 5.8 End-to-end verification

The full workflow was run on 8 September 2026:

```
inbox (3 PDFs)
  -> text extraction (pypdf)
  -> extraction (llama3.2 via Ollama, constrained decoding)
  -> validation gate
  -> SQLite (normalised, five related tables)
  -> archive
  -> dashboard, approval, task creation, notification queue
```

All three documents were processed, stored and archived, and the dashboard rendered without
error. Scores were 0.85, 1.00 and 1.00; the 0.85 correctly reflects a document from which
no line items were extracted.

**Two steps in the intended design are not connected.**

The intake component does not write to the database. `record_email` and `has_seen_email`
are implemented and tested, but `email_listener.py` does not call them, so `invoices.email_id`
is `NULL` on every row and the link from a document back to the message that delivered it
does not exist in the data.

The retrieval module is not called by the pipeline. It works, is tested, and returns
relevant prior correspondence, but nothing in `main.py` invokes it, so no retrieved context
currently reaches the extraction step.

These are stated as unfinished rather than described as working, because a diagram of an
intended architecture is not evidence that the architecture runs.

## 5.9 Threats to validity

- **Construct validity.** `validation_score` measures field completeness and agreement with
  the source text. It is not a confidence score and does not estimate the probability that
  an extraction is correct. It was renamed in the database for this reason; the in-memory
  field retains the older name because it is a shared interface.
- **Score comparability.** The gate's weighting changed on 28 August and again on
  3 September 2026. Scores are not comparable across those dates and the report marks which
  period each figure belongs to.
- **Internal validity.** The 6/15 against 3/15 discrepancy in §5.4.3 is unresolved.
- **External validity.** Three synthetic documents from one generator. Nothing here
  supports a claim about performance on real invoices, and no such claim is made.
