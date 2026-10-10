# 7. Evaluation

## 7.1 What was measured, and why it was measured this way

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
discussed in §7.7.

The unit of measurement is a **field-value**: three documents with five fields each gives
fifteen. All figures below are out of fifteen.

## 7.2 What this evaluation does not measure

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

- **The email half is not measured at all.** Everything from §7.3 to §7.8 concerns extraction
  from invoice PDFs, where ground truth exists. Classification, summarisation and action
  extraction have no labelled set, so §7.9 reports what a run produces and explicitly claims
  no accuracy for it.

None of these invalidate the findings below. They bound them, and the bounds are wide.

## 7.3 The baseline


> **Gap: Figure 5.** The before-and-after totals recovered by migration 001 are stated in prose
> and belong in a table with a caption, drawn from the migration rather than retyped.
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

### 7.3.1 The defects behind that number

The local pipeline ran end to end within days. It did not work. The defects below were found
by measuring rather than by use, and they are ordered by how badly they would have misled
someone relying on the system.

**Extraction returned almost nothing, and reported success.** 3 of 15 field-values correct.
Every field except the vendor name came back `None`, `0.0` or `"Unknown"`. No error was
raised at any layer, because each layer was behaving correctly: the model returned valid
JSON, Pydantic validated it, and the database stored it. §7.4 analyses the cause.

**The validation gate never checked the amount.** A hallucinated total of 999,999.99 on a
1,500.00 invoice scored 1.00 and passed as `Validated`, identically to the correct value.
The gate scored field *presence*, not agreement with the document.

**Correct totals were present but unreachable.** All three stored totals read 0.00 while the
correct values sat inside a JSON blob in a text column, unqueryable.

**Re-running the pipeline duplicated every row.** There was no uniqueness constraint, so four
runs over three documents produced twelve rows and no way to tell which were current.

**The approve button had never once executed.** It referenced a column by the wrong case,
raising `KeyError` and destroying the entire detail panel. The feature had been demonstrated
as working because the exception surfaced as an empty area of the page rather than an error.

**A constraint rejected every valid date.** Discussed in §7.7.

These share a property that shaped the rest of the project. **None of them produced an error
message.** Every one was a component doing exactly what it had been told to do, where what it
had been told was wrong. A system can be fully operational and produce nothing of value, and
the only way to tell the difference is to measure it against known-correct answers.

## 7.4 Isolating the cause


> **Gap: Figure 4.** The two-by-two of schema against prompt is the report's central piece of
> evidence and appears only as a code block. It should be a captioned table or a small chart
> generated from `evaluation/prompt_schema_2x2.py`, so that the figure and the number cannot
> drift apart.
### 7.4.1 The first comparison

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

### 7.4.2 The second comparison, which corrected the first conclusion

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

### 7.4.3 A third factor: the shape of the schema, not only its required list

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

This extends the finding in §7.4.1 rather than contradicting it. That section established
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

## 7.5 What the fix costs

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

### 7.5.1 A fourth schema, and a prediction that was wrong

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
different problems. §7.5 asked what the fix costs; the answer is that the cost can be reduced
by more than half but not eliminated, and the remainder is not a schema defect at all.

**Recommendation.** Adopt nullable-required, on the evidence that it matches the best
extraction accuracy measured, produces the fewest errors of any arm, and converts five
undetectable placeholder strings into detectable nulls. Do not adopt it on the grounds that
it satisfies the "never invent" requirement, because it does not.

## 7.6 Model comparison

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

## 7.7 What testing found

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

## 7.8 End-to-end verification

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

## 7.9 The email half, which is run but not scored

Everything above concerns the invoice attachment. In September the project also began reading
the message around it: classifying an email, summarising it, and extracting the actions people
committed to. **This subsection is deliberately shaped differently from the ones above, because
it cannot report accuracy.**

### 7.9.1 What a full run produces

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

### 7.9.2 The one finding the run does support

All 18 messages were classified `Invoice`. The model had six labels available and used one.

This does not show the classifier is broken, and the report resists saying so. Every message in
this mailbox concerns an invoice, so `Invoice` may be right 18 times out of 18. **What it does
show is a property of the test data rather than of the model: this corpus cannot distinguish a
working classifier from one that answers `Invoice` unconditionally.** Both produce identical
output on it.

That is a more useful result than a score would have been at this stage, and it is the same
argument §7.2 makes about n = 3. A test set that every candidate passes measures nothing. The
remedy is a labelled set with messages that are genuinely not invoices, and building one is the
outstanding work rather than an afterthought.

### 7.9.3 Evidence validation, and the decision to keep failures

Every action item must carry a quote that occurs verbatim in the source. Where it does not, the
model is asked again, up to three times. **If it still fails, the analysis is kept and marked
for review rather than discarded**, with a machine-readable reason and the attempt count stored
alongside it.

The alternative, dropping the result, would have made the failure rate unobservable, and the
failure rate is exactly the measurement that would tell us whether this technique works. On this
run it never fired: all 18 passed on the first attempt. **A mechanism that has not yet failed
has not yet been tested**, and no claim is made for its effectiveness on this evidence.

### 7.9.4 A defect the run found that reading the code did not

Asked for a field that may be absent, the model twice returned the four-character string
`"null"` rather than an absent value. Stored literally, that is text, and SQL cannot tell it
apart from a real answer: every count of "action items carrying a deadline" would have been
inflated, including the one reported in §7.9.1.

It was caught by running two real messages end to end, not by reading either module. The fix
maps that string, and the three others the module already recognised, to a genuine null before
the row is written.

**The point is not the defect, which is small. It is that the test suite passed throughout.**
Twenty-eight tests covered this path and none of them used the literal string `"null"`, because
nobody writing a test thinks to. A model's actual output found in one run what constructed
inputs had not.

## 7.10 Threats to validity

Construct validity, score comparability, internal and external validity, and non-determinism
all bound the results above. They are stated with the report's other limits in §9.3, rather
than twice.

---

## 7.11 Where the validation score comes from, and what it actually decides

Added after the supervisor asked for the reasoning behind the score to be made explicit. It is
placed here for now and belongs early in the evaluation section once the report is renumbered,
because everything in §7.3 to §7.5 is a statement about extraction quality and this is the
mechanism that judges it.

Every figure below is reproduced by:

```bash
./.venv/bin/python evaluation/score_breakdown.py
```

which recomputes each score from the archived PDF and the stored extraction rather than reading
the number back out of the database.

### 7.11.1 It is not the model's confidence

The system never asks the model how sure it is. A language model's self-reported certainty is
not a measurement of anything, and treating it as one would put the pipeline's most important
safety decision in the hands of the component being checked.

Instead the score is computed by `ConfidenceValidator`, deterministically, after extraction.
Every term compares the model's output back against the text of the document it came from. The
database column was renamed from `confidence_score` to `validation_score` for exactly this
reason: the original name described something the system does not measure.

### 7.11.2 Seven terms, in two families

The score is a weighted sum out of 1.00.

| Term | Maximum | The question it asks |
|---|---|---|
| Completeness, 0.06 for each of five header fields | 0.30 | Is the field non-empty? |
| Line items stored | 0.10 | Were any rows extracted at all? |
| Invoice number appears in the document | 0.10 | Does the value exist in the source? |
| Vendor appears in the document | 0.08 | The same question for the vendor |
| Vendor is not a field label | 0.07 | Did extraction capture `Vendor:` instead of the name? |
| **Amount check** | **0.25** | `verified` 0.25, `present` 0.125, `absent` 0 |
| Reconciliation | 0.10 | `exact`/`plausible` 0.10, `unknown` 0.05, `short` 0 |

The two families are worth naming because they fail differently. **Completeness, 0.40 of the
total, asks whether anything is missing.** **Agreement with the document, 0.60, asks whether
anything is invented.** A field left empty is a visible failure that a person can see and
correct. A field filled with a plausible value that is not in the document is an invisible one,
which is why the second family carries more weight.

The amount check is the heaviest single term. `verified` requires the extracted total to appear
within two lines of a grand-total label. `present` means the value is somewhere on the page but
not beside such a label, which is what a line-item total looks like. That distinction is not
theoretical: on one of the sample documents the true total is 2,350.00 while 1,500.00 also
appears, as a line item.

### 7.11.3 The score does not decide. Four conditions do

```python
passes = (score >= self.threshold
          and amount_state == "verified"
          and reconciliation != "short"
          and checks["vendor_is_not_a_label"])
```

**Three of those four have nothing to do with the score.** The design note in the source states
the reason: *"Hard rules, not weightings. A weighted score that happens to land below the
threshold is fragile: change one weight and the guarantee disappears silently."*

If the gate were only `score >= 0.80`, then the guarantee that unverified money is never
auto-approved would be an accident of arithmetic. Anyone retuning a weight, for any reason,
could remove it without noticing. Stating the conditions separately makes the guarantee
independent of the weights, and makes it reviewable: a reader can check what the system promises
without recomputing a weighted sum.

### 7.11.4 A worked pair: 0.87 is refused and 0.85 is approved


> **Gap: screenshot, and an open decision.** `report/screenshots/approval-screen-before-prompt-fix-2026-09-28.png`
> is the only record of this pair on screen and is not placed in the report. It also cannot be
> captioned in the present tense: the prompt fix in §7.12 removes the state it shows, and the
> group has not decided whether that fix ships before submission. Whatever is decided, the
> caption must carry the date.
The current sample set contains the clearest available demonstration that the score and the
verdict are different measurements. Two documents sit side by side in the review queue:

| | Harbour Review Supplies, INV-2026-004 | Apex Cloud Solutions, INV-2026-001 |
|---|---|---|
| Score | **0.87** | **0.85** |
| Verdict | **NeedsReview** | **Validated** |

Both are perfect on five of the seven terms. They differ in one place each.

**Harbour loses 0.125 on a single term.** Its stated total of 990.00 does appear in the
document, but not within two lines of a grand-total label, so the amount check returns `present`
rather than `verified`. Everything else, including reconciliation, is full marks.

**Apex loses 0.15 across two linked terms.** No line items were stored, which costs 0.10
directly and a further 0.05 through reconciliation, because with no rows there is nothing to
check the total against and the state falls to `unknown`. Its total, however, is `verified`.

So Harbour scores higher for an arithmetically ordinary reason: one missing location check costs
less than missing rows plus the reconciliation that depends on them.

The verdict inverts that, and the second condition is why:

| Condition | Harbour 0.87 | Apex 0.85 |
|---|---|---|
| score at or above 0.80 | pass | pass |
| **amount located beside a grand-total label** | **fail**, `present` | pass, `verified` |
| rows do not exceed the total | pass, `exact` | pass, `unknown` |
| vendor is a name, not a caption | pass | pass |

**A reader shown only the two numbers would rank these documents the wrong way round.** That is
the argument for showing the gate's verdict beside the score wherever the score appears, and it
is the reason the approval screen never displays the number on its own.

It is also a direct answer to Objective O2, which asks the project to show where automation is
safe and where a person must intervene. The score measures how complete and how corroborated an
extraction is. The conditions decide whether it is safe to act on unattended. Harbour
demonstrates that a high score is not the same as a safe one.

### 7.11.5 One score in the current set sits on a rounding boundary

Found on 2026-09-28 by the test that checks the breakdown against the gate, and recorded
because it is a property of the arithmetic rather than a defect in either.

Harbour's seven terms are `0.30 + 0.10 + 0.10 + 0.08 + 0.07 + 0.125 + 0.10`, which is exactly
0.875. Summed in the order the validator evaluates them, floating-point representation gives
0.8749999999999999, and Python rounds that to **0.87**. Summed as a list of the same seven
values it gives exactly 0.875, which rounds to **0.88**.

The terms are identical and the difference is one part in ten thousand million million. It is
visible only because the value lands precisely on a half-cent boundary, and only because the
score is displayed to two decimals.

**Nothing downstream depends on which side it falls.** Both 0.87 and 0.88 clear the 0.80
threshold, and the document was refused for an unrelated reason. The finding is reported for
two narrower purposes: a reader reproducing the figure by hand will get 0.88 and should know
why, and it is a small illustration of the standard this report is written to, which is that a
figure is worth only as much as the procedure that produced it. The test asserts the terms
account for the score to a tolerance tighter than the displayed precision rather than asserting
an exact match, because an exact match would fail on any document that lands on a boundary.

### 7.11.6 What the score cannot do

Three limits, each of which constrains how far §7.11.4 generalises.

**Two of the three text checks are substring matches.** `invoice_number` and `vendor_name` are
counted as corroborated if the value appears anywhere in the document. Only `total_amount`
receives real location verification. This is recorded as a partial requirement rather than a met
one, and it was deliberately not hardened: a matching rule tuned against three synthetic
invoices would encode the generator rather than the domain.

**A computed value defeats the check by construction.** The question the gate asks is whether an
extracted value appears in the document. A model that adds two numbers together and produces a
sum which happens to appear on the page will pass that test. §7.5.1 records exactly this
behaviour on a payment statement, where every required-schema variant returned 2,000.00 as the
total of a document that states no total, by adding 1,200.00 and 800.00. A fabricated string can
be caught by asking whether it is on the page. A computed one cannot.

**A score of 1.00 does not mean the document is genuine.** It means the extraction is complete
and internally corroborated. Every check reads the document itself, so a duplicate invoice, an
invoice from an unknown vendor, and a well-formatted forgery all score 1.00. Nothing in the gate
looks outside the page. This is the most important limitation in the system and §9.4 returns to
it, because it is the gap that no amount of extraction accuracy closes.

---

## 7.12 One word in the prompt, and what it cost to not notice it

Measured 2026-09-28. Reproduce with:

```bash
./.venv/bin/python evaluation/prompt_label_ablation.py
```

This is the largest single effect the project has measured, and it was found by reading a diff
rather than by looking for it.

### 7.12.1 The defect

The extraction prompt contains a worked example followed by the document to be read. Both were
introduced with the same words:

```
        Example:
        Document Content:
        """
        Vendor: Bright Star Media Pty Ltd
        ...
        """
        Expected JSON:
        {...}

        Document Content:
        """{raw_text}"""
```

**The label `Document Content:` appears twice and means two different things.** The first
introduces an invented invoice the model should imitate the handling of. The second introduces
the real invoice it must extract from. Nothing in the prompt distinguishes them.

### 7.12.2 The measurement

Changing the second label to `Current Document Content:`, and changing nothing else, was run
three times in each configuration against the same three documents, same model, same
temperature, with every field repair disabled:

| Prompt label | Runs | Overall |
|---|---|---|
| `Document Content:` | 10/15, 10/15, 10/15 | **10/15, 66.7%** |
| `Current Document Content:` | 15/15, 15/15, 15/15 | **15/15, 100%** |

Nine runs, no variance in either arm. The gate's automation pass rate moves from 2/3 to 3/3 at
the same time.

**Every one of the five failures was on one document**, `sample_invoice_1`, which returned
`None` for the vendor, the invoice number and the date, `0.0` for the total and `Unknown` for
the currency. A model that finds a document hard returns some fields and misses others. A model
that returns nothing usable for a single document, while handling two others perfectly, is not
struggling with the document. It is reading the wrong block.

### 7.12.3 What this does to the rest of the evaluation

Three earlier results in this report have to be read differently in light of it.

**The gap this report attributes to repair code closes to zero.** §7.3 and §7.5 are built on the
distinction between what the model produces alone, 10/15, and what the system ships, 15/15,
with the difference credited to a 92-line regex fallback and a caption stripper. With the label
corrected, the model alone returns 15/15, and running the evaluation with every repair enabled
also returns 15/15. **On this sample set the repair code now repairs nothing.** It is not
removed, because three synthetic documents are not evidence that it is unnecessary in general,
but the claim that it is what produces the shipped figure is no longer true here.

**It is a third confirmation of §7.4.2's conclusion.** The two-by-two in
`evaluation/prompt_schema_2x2.py` found that either an improved prompt or a required schema
alone reaches the ceiling, and that the two are not additive. This is the cleanest instance of
the prompt half of that finding available: not a rewritten prompt, one word.

**It caused the ceiling in the first retrieval comparison.** The initial experiment described
in §6 compared baseline and retrieval on these same three documents and found no difference,
both at 100%. This label change shipped in the same branch as retrieval, so that comparison had
no baseline errors for retrieval to correct. A later 30-document benchmark was added for that
reason; it removes the ceiling and measures an improvement from 91.3% to 94.0%. The first result
remains uninformative, but it is no longer the final evidence for the RAG method.

### 7.12.4 Why it matters beyond this project

The finding generalises further than most in this report, and it is uncomfortable.

**The defect is invisible to every form of testing this project performs.** The prompt is
syntactically fine. The schema is satisfied. The output parses, validates, and passes type
checking. The pipeline reports success. Two of the three documents extract perfectly, so the
system does not look broken. The only visible symptom is an accuracy figure that is lower than
it should be, and there is no baseline that says what it should be.

**It was found by reading a diff, not by measurement.** Nobody was looking for it. It surfaced
because an unrelated pull request happened to touch that line for an unrelated reason, and
because the diff was read closely enough to ask what the changed word did. Had the same change
arrived in a larger commit, it would have landed silently and the project's headline numbers
would have moved with no recorded cause.

That is the practical argument for a discipline this report has otherwise applied to itself: a
figure is worth only as much as the procedure that reproduces it. This project keeps its
evaluation runnable, so the effect could be isolated in minutes once suspected. What it did not
have was anything that would have raised the suspicion. **For a system whose output is a
confident, well-formed, wrong answer, a passing test suite is not evidence of correctness**, and
the gap between "the pipeline ran" and "the pipeline was right" is exactly where this defect
lived for the length of the project.
