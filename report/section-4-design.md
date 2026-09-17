# 4. Project Design and Development

## 4.1 Architecture

```
Gmail (IMAP)
   -> email_listener.py            attachments to inbox/, message metadata recorded
   |
   |-- the attachment ------------------------------------------------------------
   |   -> pypdf                    text layer extracted
   |   -> retrieval.py             prior correspondence from the same sender, as context
   |   -> Ollama (llama3.2)        extraction under a JSON schema, constrained decoding
   |   -> ConfidenceValidator      rule-based gate, scores and routes
   |
   |-- the message itself ---------------------------------------------------------
   |   -> email_pipeline.py        groups messages into threads by subject
   |   -> email_ai.py              category, summary, action items, each with its quote
   |   -> evidence validation      three attempts, then kept for review, never discarded
   |
   -> SQLite                       ten related tables, constraints enforced by the database
   -> archive/                     the file moves only after the write commits
   -> Streamlit                    review, approval
   -> tasks / outbound_messages    follow-on work and a notification queue
```

Every component runs on one machine and no document content leaves it.

**The second branch is newer and less finished than the first.** It runs end to end and its
output is stored and queryable, but unlike the extraction branch it has no ground truth, so it
can be inspected and not yet scored. §5.9 states that plainly rather than presenting the two
halves as equally evidenced.

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
| `processing_runs` | One row per pipeline execution: model, threshold, counts, timing, and which of the two pipelines it was |
| `invoices` | One row per distinct document, keyed on a content hash |
| `line_items` | One row per line, queryable |
| `email_messages` | Messages fetched, their bodies, which attachments they carried, and their thread |
| `tasks` / `outbound_messages` | Follow-on work, and notifications recorded but not sent |
| `email_analysis` | One row per message per run: category, summary, and whether its evidence held |
| `thread_analysis` | One row per thread per run, with how that thread was identified |
| `action_items` | One row per action the model found, under either an email or a thread |
| `thread_decisions` | One row per decision, because the model returns a list and a list is ordered |

Constraints are enforced by SQLite rather than by application code, so a defect in the
pipeline cannot write a row that violates them. Amounts are stored as integer cents. Every
schema change is a numbered, idempotent migration that backs up the database first and
reports what it changed; there are eleven, and the schema is at version 11.

**The last four tables were added in September, and the shape of the first six decided their
shape.** The original flat table held line items as JSON inside a column, and recovering them
cost a migration. When the email module returned two fields that are lists, the same question
arrived again and was answered the other way the first time: `latest_decisions` and
`outstanding_actions` are rows with an ordinal, not a delimited string. Repeating a mistake the
project had already paid for once would have been the worse outcome than the mistake itself.

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

### 4.3.11 One table for action items, two parents, and why that is not the earlier mistake

The email module returns action items in two places: attached to a single message, and
attached to a thread as work still outstanding. Both are the same four fields, defined once as
one class in the module.

Two tables would duplicate that definition, so every later change to it would cost two
migrations and "which actions does one person owe" would need a union. One table with two
nullable parent keys and a constraint that exactly one is set costs a column that is always
precisely half empty.

**The same shape was rejected earlier in the project and the difference matters.** Email
actions were kept out of the existing `tasks` table for exactly this reason: an invoice task
and an email action need different columns, so merging them produces rows half full of nulls,
which is the flat table the project spent a migration escaping. Here the rows are identical and
only their owner differs. Merging identical rows is normalisation. Merging different rows is
the flat table returning under a new name.

### 4.3.12 A deadline is stored twice, as written and as a date

The model is asked for the deadline exactly as the source words it and never to convert it. The
database then holds a second column, filled only where the wording is unambiguous.

`by 30 September 2026` becomes a date. `end of month`, `by Friday` and `30/09/2026` do not, and
the wording survives untouched. The slash form is refused deliberately: day-month order is not
recoverable from the string, and Australian and American conventions disagree, so a parser that
guesses is right most of the time and silently wrong the rest.

**The value of the pair is the gap between them.** How often a deadline was found that no
system can act on is a number, and the pipeline reports it. A single normalised column would
have hidden that behind a guess.

One detail is a bug fixed before it was hit: `dateutil` parses the ISO string `2026-03-12` as
3 December when asked to prefer day-first, because it applies that preference to the last two
components whatever the shape of the string. Anything already in ISO form is returned untouched,
and a test fails if that ever stops being true.

### 4.3.13 Thread identity is a guess, and the database says so on every row

Grouping messages into conversations is done by stripping the reply prefix from the subject and
matching what remains. The correct key is the `In-Reply-To` and `References` headers, which mail
clients use and which this system does not capture, because intake is another member's file.

Subject matching is wrong in a way the project's own test data demonstrates. Three vendors each
send a message titled "Monthly statement", and it merges them into one conversation: 1 of 16
threads in the mock mailbox is a collision, and it is counted rather than assumed.

Rather than pick between a correct method that is unavailable and a cheap one that is wrong,
every row records which was used. A summary computed over a guessed grouping is therefore never
reported as though it came from a real reply chain. This is the same device used elsewhere in
the schema for whether a total came from the model or from repair code, and for whether an email
body is real or generated. **The pattern is worth naming: where a system cannot be certain, the
uncertainty belongs in a column rather than in the memory of whoever wrote the code.**

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
