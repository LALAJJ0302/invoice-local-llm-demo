# Spec: Model Selection, Evaluation and Retrieval Support

**Owner:** Neo. **Assigned by:** JJ, 2026-09-08. **Status:** spec, nothing built.

JJ's division of work, verbatim:

> Neo: Model selection, evaluation, and retrieval support
> - Research and shortlist suitable models, checking hardware requirements and runtime compatibility.
> - Prepare test emails and manually labelled expected results.
> - Run model comparisons and analyse classification, summarisation, and extraction errors.
> - Build the historical email retrieval module for RAG, supplying relevant context to my AI module.
> - Lead the model comparison and experimental results sections of the report.
>
> Neo would provide candidate models and evaluation findings, while I would implement the
> improvements in the system.

This document turns that into work that can be started, and states plainly which parts cannot
be started yet and why.

---

## 0. What already exists, so nothing is rebuilt

Checked by running it on 2026-09-08, not assumed.

| Asset | State |
|---|---|
| `evaluation/run_eval.py` | Works. **Already takes `--model`**, so multi-model comparison is an extension, not a new tool |
| Repair switch | Discovers every `_infer_*_fallback` and `_clean_*` at runtime and disables them, so the model can always be measured alone |
| `evaluation/ground_truth.json` | **3 synthetic PDFs. No emails.** |
| `evaluation/schema_comparison.py` | Isolates the schema as a cause with one variable changed |
| `email_messages` table | Exists, migration 004. **0 rows** |
| `invoices.email_id` | **NULL on 3 of 3** |
| Classification | `storage.classify_document`, a marker check on the opening lines returning Invoice / Receipt / Unknown |
| Summarisation | **Does not exist anywhere in the codebase** |

## 1. Hardware, measured not guessed

```
Apple M4, 10 cores, 24 GB unified memory, 187 GB free
Pulled: llama3.2:latest only, 2.0 GB
```

Unified memory is shared with the OS, Streamlit and Python, so the usable budget is roughly
**16 to 18 GB**, not 24. A model needs about 1.2x its file size resident.

---

## Item 1: Shortlist candidate models

**Unblocked. Start here.**

### The compatibility test that actually matters

Not size, and not benchmark scores. **Whether the model honours Ollama's `format` parameter for
constrained JSON decoding.** The project's central finding is that a Pydantic schema emitting an
empty `required` list lets the decoder legally omit fields. A model that handles constrained
decoding badly reproduces that failure for a different reason, and the two would be
indistinguishable in the results unless tested separately.

So every candidate gets checked for: does it return valid JSON against a schema, and does it
respect `required` when the schema declares it.

### Candidates to test

| Model | Size | Fits 24 GB | Note |
|---|---|---|---|
| `llama3.2:3b` | 2.0 GB | yes | current baseline, already measured |
| `mistral:7b` | ~4.1 GB | yes | |
| `llama3.1:8b` | ~4.7 GB | yes | |
| `qwen2.5:7b` | ~4.7 GB | yes | strong at structured output |
| `gemma2:9b` | ~5.4 GB | yes | |
| `qwen2.5:14b` | ~9 GB | yes | likely the practical ceiling |
| `qwen2.5:32b` (quantised) | ~20 GB | **no** | exceeds the usable budget once the OS is counted |

### Deliverable

A table per model: file size, resident memory measured during a run, seconds per document,
valid-JSON rate, and whether `required` is honoured. Plus a recorded decision on which two or
three go forward, with the reason.

**Report value:** this is a hardware-constrained selection with measurements behind it, which is
a real engineering argument rather than "we picked llama3.2".

---

## Item 3: Run model comparisons and analyse errors

**Partly unblocked. Start the extraction half now.**

Taken before item 2 deliberately: the extraction comparison can run today against the three
existing documents. Waiting for the email dataset would waste the week.

### Build

`evaluation/model_comparison.py`, following `schema_comparison.py`: loop the shortlist, run the
existing harness per model with repairs disabled, write one JSON per model, print a comparison
table. Small, because `run_eval.py` already does the work.

### The error taxonomy

Counting right and wrong is not analysis. Each miss gets classified:

| Class | Meaning |
|---|---|
| omitted | field absent from the JSON entirely |
| invented | a value not present in the document |
| mislocated | a real value from the document, wrong field. The line-item total read as the grand total |
| malformed | correct value, unusable format |
| truncated | correct but cut short. `'Vendor: Apex Cloud'` for the full name |
| caption | the field label captured instead of the value, as seen on 2026-09-03 |

**This taxonomy is the report's experimental results section.** Which models fail in which way is
a finding; an accuracy column is a number.

### Blocked half

Classification and summarisation cannot be analysed. Classification today is a three-line marker
check, and summarisation does not exist. **See question 2 for JJ.**

---

## Item 2: Test emails with labelled expected results

**Blocked on a definition, not on effort.**

The existing ground truth is three synthetic PDFs. This asks for a new dataset of emails.

### What has to be decided before collecting anything

1. **What is labelled?** Extraction labels are clear: the five fields plus line items. Classification
   labels need a class list, which does not exist. Summarisation has no objective correct answer,
   so it needs either a reference summary written by hand or a rubric.
2. **Real emails or synthetic?** Real ones carry vendor names, addresses and bank details belonging
   to actual companies. That is a privacy question for a university project and should be answered
   before collecting, not after.
3. **How many?** n=3 is the current weakness and the evaluation method already says so. Fewer than
   about 20 will not support a claim that one model beats another.

### Once decided

Extend `ground_truth.json` with an `emails` section, transcribed independently of whatever
generated the test data. That independence is the reason the current ground truth is trustworthy
and it must not be dropped for convenience.

---

## Item 4: Historical email retrieval for RAG

**Hard-blocked. Cannot start.**

```
email_messages rows:            0
invoices with an email_id:      0 of 3
```

`record_email` and `has_seen_email` are implemented and tested. **`email_listener.py` never calls
them**, so nothing is ever written. That file is Luke's.

There is also a piece nobody has designed: how a saved attachment is associated with its
`email_id`. A sidecar file or a staging table both work. It needs Luke.

**A retrieval module with an empty table retrieves nothing.** This cannot be built, tested or
measured until intake writes rows.

### What can be done meanwhile

Design and question, not build:

- **What is retrieved, and to answer what?** Prior emails from the same vendor, to supply
  historical context? Something else? JJ has not said.
- **What interface does his module expect?** A list of texts, structured records, ranked passages?
- **Does this need embeddings at all?** At the scale of a student project, SQL plus keyword
  matching may beat a vector store, and it is far easier to explain and defend. **Worth measuring
  rather than assuming**, and the measurement itself is a report finding.

---

## Item 5: Lead the model comparison and experimental results sections

**Needs reframing before it can be done.**

The Final Report is an **individual** deliverable worth 80% of the subject. All three of us write
all eight sections ourselves. Nobody can lead a section of someone else's individual assessment.

**Reframed:** Neo produces the shared **evidence pack** for model comparison and experimental
results, which is the comparison tables, the error taxonomy with counts, the figures, and the
method statement including what it does not measure. All three then write their own prose around
it. That is the arrangement agreed on 2026-09-06: shared template and topics, separate writing.

Worth confirming JJ understands the report is individual, because if he is planning to write only
some sections, that becomes his problem in roughly one week.

---

## Order of work

Dependencies, not preference.

```
NOW      Item 1  shortlist and compatibility        no dependencies
NOW      Item 3  extraction comparison on the 3 PDFs   uses existing harness
THEN     Item 2  after JJ defines the labels
THEN     Item 5  follows 1 and 3
BLOCKED  Item 4  needs Luke's intake writing rows
```

## Scope note, said once and plainly

Five items, of which one is a retrieval module and one is building a labelled dataset from
scratch. Alongside an individual report of 30 to 45 pages that **has not been started**, with the
Week 8 draft roughly 6 to 13 days away and the final on 18 October.

This is deliverable if items 1 and 3 come first and produce report material as they go. It is not
deliverable if item 4 is treated as urgent, because item 4 cannot start at all.

## Four questions for JJ

1. **Which module classifies and summarises?** Neither exists. Extraction is all there is today.
2. **What are the classification labels, and what does a correct summary look like?** Item 2 cannot
   start without both.
3. **Who wires `email_listener.py` to `record_email`?** It is Luke's file, and item 4 is dead until
   it happens.
4. **Does "lead sections of the report" mean the shared evidence pack?** The report is individually
   marked.
