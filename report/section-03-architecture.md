# 3. System Architecture and Data Flow

## 3.1 Architecture


> **Gap: Figure 1 and Figure 3.** This subsection describes the architecture in prose and the
> report has no diagram of it. Two are planned: the architecture before and after the pivot,
> for which `Enterprise_AI_Workflow_Briefing.docx` already holds the comparison, and the
> pipeline flow with phase ownership. Both need drawing, a numbered caption and a source line
> saying they are the author's own.
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
can be inspected and not yet scored. §7.9 states that plainly rather than presenting the two
halves as equally evidenced.

**Before and after the pivot.** The mapping is not one-to-one, and §1.5 records what was
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

## 3.2 Data model


> **Gap: Figure 2.** An entity-relationship diagram belongs here. `database-spec.md` §3 already
> carries it as mermaid and it renders without new work; it needs to be exported, placed, given
> a numbered caption and cited to this project.
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

