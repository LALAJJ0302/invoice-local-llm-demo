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

- **The email half is not measured at all.** Everything from §5.3 to §5.8 concerns extraction
  from invoice PDFs, where ground truth exists. Classification, summarisation and action
  extraction have no labelled set, so §5.10 reports what a run produces and explicitly claims
  no accuracy for it.

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

### 5.4.3 A third factor: the shape of the schema, not only its required list

The Original-Optional cell read **6/15** in the two-by-two above and **3/15** in
`schema_comparison.py`, which still reproduces exactly. The difference was entirely in the
`currency` field: 0/3 in one, 3/3 in the other.

This was initially recorded as an unresolved discrepancy. It has since been resolved, and
the cause is a finding in its own right.

Both scripts call the model with the same prompt, the same temperature and the same model.
The difference is what their `Optional` schema contains. `schema_comparison.py` imports
`ExtractedInvoice` from `main.py`, which carries a sixth field, `items`, a **nested list**
of line items. The two-by-two defined its own five-field schema and omitted it.

Holding everything else constant and varying only the presence of that one field:

```
Optional schema WITH items      3/15    invoice:0  vendor:3  date:0  total:0  currency:0
Optional schema WITHOUT items   6/15    invoice:0  vendor:3  date:0  total:0  currency:3
```

Repeated three times, identical every run. This is not decoder variation.

**Adding a nested list to the schema costs three scalar field-values elsewhere.** The field
lost is `currency`, the last scalar property before `items` in the declaration order. The
most plausible mechanism is that the larger and more complex grammar makes early
termination of the object more likely, and the field nearest the end is the one dropped.

This extends the finding in §5.4.1 rather than contradicting it. That section established
that an empty `required` list permits omission. This establishes that **the shape of the
schema also affects which fields are emitted**, independently of which are required. Schema
design is therefore a variable with at least two dimensions, not one.

It also aligns with the framing in JSONSchemaBench (Geng et al., 2025), whose second
evaluation dimension is *coverage of constraint types* rather than mere compliance: the
question is not only whether a schema is enforced, but how different schemas behave under
enforcement.

**Which figure the report uses.** Both are correct and they answer different questions.
**3/15 is the figure for the system as shipped**, because it uses the schema `main.py`
actually declares, and it is the number quoted throughout this report. 6/15 is a controlled
variant that exists to isolate the effect above, and is reported only in this subsection.

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

### 5.5.1 A fourth schema, and a prediction that was wrong

The two requirements in tension are "every field must be emitted" and "no value may be
invented". Both preceding arms treat these as a trade. A fourth variant dissolves it in
principle: declare each field `Optional[T]` with **no default**. Pydantic places a field with
no default into `required` even when its type admits null, so the emitted JSON Schema reads
`"required": [...all five...]` with each property typed `anyOf[{string},{null}]`. The
constrained decoder must therefore emit every key, and is never left without a legal way to
say "absent".

Measured on 2026-09-12, same model, same documents, same temperature:

| Schema | Extracted correctly | Absent admitted | Absent invented | Total errors |
|---|---|---|---|---|
| Optional with defaults | 1 of 13 | 9 of 9 | 0 | 12 |
| Required | 12 of 13 | 6 of 9 | 3 | 10 |
| Sentinel | 12 of 13 | 6 of 9 | 3 | 10 |
| **Nullable-required** | **12 of 13** | **7 of 9** | **2** | **4** |

It also reaches 15/15 on the three complete documents, so nothing was given up on the
ordinary case.

**The prediction recorded before the run was that it would invent zero, and that prediction
was wrong.** Two inventions survive. Stating this matters more than the favourable columns
either side of it: the hypothesis was that a legal null removes the pressure to invent, and
on this evidence a legal null only *reduces* it.

Classifying every error rather than counting it explains where the improvement actually came
from, and it is not where the proposal claimed. The classifier is `evaluation/error_taxonomy.py`,
specified in `error-taxonomy-spec.md`; the three classes used below are **placeholder**, a string
such as `"None"` that means "no value" but occupies the field, **mislocated**, a value that is
printed on the document but is not this field, and **invented**, a value that appears nowhere on
the document at all:

```
Required           6 placeholder,  2 mislocated,  1 invented,  1 malformed   = 10
Nullable-required  1 placeholder,  1 mislocated,  1 invented,  1 malformed   =  4
```

**The nullable field removed placeholders, not inventions.** Five of the six strings such as
`"None"` and `"Not specified"` became real JSON nulls, which is a genuine gain because a null
is machine-detectable and the string `"None"` is not. But the one true invention is untouched,
and one of the two mislocations remains.

That surviving invention is worth quoting in full, because it is the clearest single result
in this evaluation. On a statement of account containing no total, every required-family
schema returns `2000.00`. The document reads:

```
Opening balance      1,200.00
Payments received      800.00
```

**1,200.00 + 800.00 = 2,000.00.** The model is not hallucinating a number, it is performing
arithmetic on the two numbers it can see. Worse, the arithmetic is wrong in kind: a payment
received should be subtracted, not added, so the defensible answer would have been 400.00 and
the correct answer is that no total exists. A fabricated value drawn from nowhere might be
caught by checking whether it appears in the document, which is what the `mislocated` class
does. **A computed value defeats that check by construction**, and no schema can fix it,
because the model is obeying the schema exactly.

This is the strongest available argument that schema design and the validation gate solve
different problems. §5.5 asked what the fix costs; the answer is that the cost can be reduced
by more than half but not eliminated, and the remainder is not a schema defect at all.

**Recommendation.** Adopt nullable-required, on the evidence that it matches the best
extraction accuracy measured, produces the fewest errors of any arm, and converts five
undetectable placeholder strings into detectable nulls. Do not adopt it on the grounds that
it satisfies the "never invent" requirement, because it does not.

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

## 5.9 The email half, which is run but not scored

Everything above concerns the invoice attachment. In September the project also began reading
the message around it: classifying an email, summarising it, and extracting the actions people
committed to. **This subsection is deliberately shaped differently from the ones above, because
it cannot report accuracy.**

### 5.9.1 What a full run produces

One pass over the 18-message mock mailbox on 17 September 2026, `llama3.2:latest`, every email
with a body:

| | |
|---|---|
| Analysed | 18 |
| Failed | 0 |
| Evidence validation passed first attempt | 18 of 18 |
| Action items extracted | 5 |
| Action items with a deadline that could be normalised | 0 |
| Threads formed by subject | 16 |
| Threads that are a probable subject collision | 1 |

**None of these are accuracy figures.** "18 analysed, 0 failed" says the pipeline completed. It
says nothing about whether the 18 categories are right, whether the summaries are faithful, or
whether the 5 action items are the ones a person would have found. Those questions need a
labelled set that does not exist.

### 5.9.2 The one finding the run does support

All 18 messages were classified `Invoice`. The model had six labels available and used one.

This does not show the classifier is broken, and the report resists saying so. Every message in
this mailbox concerns an invoice, so `Invoice` may be right 18 times out of 18. **What it does
show is a property of the test data rather than of the model: this corpus cannot distinguish a
working classifier from one that answers `Invoice` unconditionally.** Both produce identical
output on it.

That is a more useful result than a score would have been at this stage, and it is the same
argument §5.2 makes about n = 3. A test set that every candidate passes measures nothing. The
remedy is a labelled set with messages that are genuinely not invoices, and building one is the
outstanding work rather than an afterthought.

### 5.9.3 Evidence validation, and the decision to keep failures

Every action item must carry a quote that occurs verbatim in the source. Where it does not, the
model is asked again, up to three times. **If it still fails, the analysis is kept and marked
for review rather than discarded**, with a machine-readable reason and the attempt count stored
alongside it.

The alternative, dropping the result, would have made the failure rate unobservable, and the
failure rate is exactly the measurement that would tell us whether this technique works. On this
run it never fired: all 18 passed on the first attempt. **A mechanism that has not yet failed
has not yet been tested**, and no claim is made for its effectiveness on this evidence.

### 5.9.4 A defect the run found that reading the code did not

Asked for a field that may be absent, the model twice returned the four-character string
`"null"` rather than an absent value. Stored literally, that is text, and SQL cannot tell it
apart from a real answer: every count of "action items carrying a deadline" would have been
inflated, including the one reported in §5.9.1.

It was caught by running two real messages end to end, not by reading either module. The fix
maps that string, and the three others the module already recognised, to a genuine null before
the row is written.

**The point is not the defect, which is small. It is that the test suite passed throughout.**
Twenty-eight tests covered this path and none of them used the literal string `"null"`, because
nobody writing a test thinks to. A model's actual output found in one run what constructed
inputs had not.

## 5.10 Threats to validity

- **Construct validity.** `validation_score` measures field completeness and agreement with
  the source text. It is not a confidence score and does not estimate the probability that
  an extraction is correct. It was renamed in the database for this reason; the in-memory
  field retains the older name because it is a shared interface.
- **Score comparability.** The gate's weighting changed on 28 August and again on
  3 September 2026. Scores are not comparable across those dates and the report marks which
  period each figure belongs to.
- **Internal validity.** The 6/15 against 3/15 discrepancy in §5.4.3 was traced to a
  schema difference and is resolved. It is retained in the report because finding it
  required treating two disagreeing numbers as a defect rather than choosing one.
- **External validity.** Three synthetic documents from one generator. Nothing here
  supports a claim about performance on real invoices, and no such claim is made.
- **Asymmetry between the two halves.** Extraction is scored against independently transcribed
  ground truth. The email half in §5.9 is not scored at all. Presenting them side by side risks
  implying the second is as well evidenced as the first, and it is not. Every figure in §5.9 is
  a count of what the pipeline did, never of what it got right.
