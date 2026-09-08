# 4. Project Design and Development

## 4.1 Architecture

```
Gmail (IMAP)
   -> email_listener.py            attachments to inbox/, message metadata recorded
   -> pypdf                        text layer extracted
   -> retrieval.py                 prior correspondence from the same sender, as context
   -> Ollama (llama3.2)            extraction under a JSON schema, constrained decoding
   -> ConfidenceValidator          rule-based gate, scores and routes
   -> SQLite                       five related tables, constraints enforced by the database
   -> archive/                     the file moves only after the write commits
   -> Streamlit                    review, approval
   -> tasks / outbound_messages    follow-on work and a notification queue
```

Every component runs on one machine and no document content leaves it.

**Before and after the pivot.** The mapping is not one-to-one, and §3.2 records what was
lost.

| Original | Local replacement |
|---|---|
| SharePoint document library | `inbox/` and `archive/` on local disk |
| Power Automate flow | `main.py`, a sequential pipeline |
| Copilot agent extraction | Ollama with a locally hosted model |
| Teams approval card | Streamlit dashboard with an approval action |
| Dataverse | SQLite, normalised |
| Power BI | Streamlit, reading the same store |
| 0.8 Copilot confidence gate | Rule-based validation score, **not a confidence score** |

## 4.2 Data model

The first working version stored everything in one flat table with no constraints, no
uniqueness and line items held as JSON text inside a column. It is now five related tables.

| Table | Holds |
|---|---|
| `processing_runs` | One row per pipeline execution: model, threshold, counts, timing |
| `invoices` | One row per distinct document, keyed on a content hash |
| `line_items` | One row per line, queryable |
| `email_messages` | Messages fetched, their bodies, and which attachments they carried |
| `tasks` / `outbound_messages` | Follow-on work, and notifications recorded but not sent |

Constraints are enforced by SQLite rather than by application code, so a defect in the
pipeline cannot write a row that violates them. Amounts are stored as integer cents. Every
schema change is a numbered, idempotent migration that backs up the database first and
reports what it changed; there are nine.

## 4.3 Design decisions

Each decision below states the alternative that was rejected and the evidence that settled
it. They are the substance of this section; the full set is in `database-spec.md` §5.

### 4.3.1 The deduplication key is extracted text, not file bytes

**Lead with this one, because it is the clearest example of a design that reviewed correctly
and would have failed silently.**

The first design deduplicated on a SHA-256 hash of the PDF file bytes. That is the obvious
choice, it passed review, and **it would have deduplicated nothing.** ReportLab writes a
random `/ID` into every PDF it generates, so regenerating an identical document produces
different bytes. A byte-hash unique index would have admitted every regenerated file as new.

The key is a hash of the extracted text instead. The file hash is still stored, because it
answers a different question: whether the archived file is the one that was processed.

Nothing in code review would have caught this. It was found by asking what the hash would
actually be equal to.

### 4.3.2 Money is stored as integer cents

Floating-point currency accumulates representation error. The decision looks trivial until
the comparison it enables is examined: an early version compared amounts with
`abs(a - b) < 0.01`, which looks reasonable and is wrong. `abs(1500.0 - 1500.01)` evaluates
to 0.009999999999763531, which is less than 0.01, so a near miss compared equal. Integer
cents make the comparison exact.

### 4.3.3 The score is named for what it measures

The field was originally `confidence_score`. It measures field completeness and agreement
with the source text. It is not a confidence score, does not estimate the probability that
an extraction is correct, and cannot, because the model exposes no calibrated confidence.

It was renamed `validation_score` in the database. The alternative considered was naming it
after the model. That was rejected: the value comes from our own gate thresholding its own
arithmetic, not from the model. Lowering the threshold tomorrow would flip every row to
`Validated` while the model did identical work, and a name implying otherwise would write the
wrong cause into the schema, where it outlives everyone's memory of the discussion.

### 4.3.4 Data quality and business approval are separate columns

`validation_status` is written by the pipeline and says how well the document was read.
`approval_status` is written by a person and says whether it was accepted. A row can read
`NeedsReview` and `Approved` simultaneously, which is intended: a person can approve a
document the system was unsure about, and the record should show both facts rather than
overwriting one with the other.

Approving therefore never modifies `validation_score`. An earlier version overwrote it with
1.0, which destroyed the evidence of how the document had actually been read.

### 4.3.5 The gate fails toward a person

Two rules are hard rules rather than weightings, and the distinction is deliberate. An
amount that cannot be located beside a grand-total label is never auto-approved regardless
of its score, and a vendor value that is actually a field caption is never auto-approved
either.

Stated as rules because a weighted score that merely happens to land below a threshold is
fragile: change one weight and the guarantee disappears with no test failing. §5.7 records
the case that made this concrete.

### 4.3.6 A perfect score means a complete extraction

`validation_score` reaches 1.00 only when nothing is empty. An extraction missing its line
items scores lower and names `items` as the gap. Before this change, a document with a
malformed vendor and no line items scored 1.00 and was auto-approved.

### 4.3.7 Reconciliation is a flag, not a constraint

The stated total is compared against the sum of the line items, giving `exact`, `plausible`,
`short` or `unknown`. **`plausible` (total above the line sum) scores full marks**, because
that is the normal state of any invoice carrying GST or freight; penalising it would penalise
every genuine Australian invoice. Only `short` blocks, because a total below its own line
items is not explained by tax or shipping.

### 4.3.8 Write to the database, then archive

The database write commits before the file is moved. If the move then fails, the file stays
in `inbox/` and the next run upserts onto the same row. The previous order produced the worse
failure: a file archived with no record of it.

### 4.3.9 Repairs to model output must remain measurable

Several components repair the model's output: a filename heuristic for the vendor, regular
expressions for header fields, a caption stripper. Each is named to a convention, and the
evaluation harness discovers them at runtime and disables them all by default.

The alternative, a list of repairs maintained by hand, was tried and failed: it named one
method, a second was added, and the harness reported 93.3% for a model scoring 66.7% while
printing that repairs were disabled. §5.7 records this. The harness now **refuses to run** if
it finds no repairs matching the convention, because reporting an accuracy figure while
claiming repairs are off, having disabled nothing, is the failure being prevented.

### 4.3.10 Retrieval implements two strategies and no embeddings

`sender` returns a vendor's prior messages newest first. `keyword` scores subject and body by
inverse document frequency. An embedding-based strategy is **deliberately not implemented**.

At the current corpus size, whether embeddings help is a measurement nobody has taken. Adding
them first would mean never learning the answer. "The system uses a vector database" is not a
finding; "at this corpus size, keyword retrieval was measured against embeddings and the
result was X" is one.

## 4.4 Development process

Three people, one repository, with ownership boundaries recorded in the repository itself.
Changes to another person's files went through pull requests carrying evidence rather than
direct commits.

That discipline was tested and paid for itself. On 3 September 2026 two commits were pushed
directly to the main branch, into files owned by two other members. One of them added a
second repair path to the extractor. It was reasonable code. It silently invalidated the
evaluation harness, which continued to report a figure it could not support for as long as
nobody re-read it. A pull request would have surfaced it in review; a direct push did not.

Two hundred and forty-eight automated tests cover the storage layer, the migration chain, the
validation gate and retrieval. Their value is documented in §5.7: they found defects that had
been latent since the schema was written, including one that would have appeared to be
somebody else's fix breaking the database.
