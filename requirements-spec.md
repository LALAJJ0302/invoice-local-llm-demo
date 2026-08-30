# Requirements Specification

**Project:** Local-first invoice processing pipeline (UTS Industry Project)
**Team:** Luke (intake), JJ (extraction), Neo (storage, validation gate, dashboard)
**Version:** 1.0, 2026-08-27
**Status:** draft for group review

---

## 1. Context

The original design was a Microsoft cloud workflow: SharePoint for storage, Power Automate for
orchestration, Copilot for extraction, Power BI for reporting. We could not obtain the UTS tenant
permissions that design needs. On supervisor advice we de-scoped to a local open-source stack that
demonstrates the same workflow concept without the tenant dependency.

```
Gmail (IMAP) -> email_listener.py -> inbox/ -> main.py (pypdf + Ollama llama3.2)
             -> SQLite workflow_platform.db -> Streamlit dashboard
```

**Framing that must survive into the report.** This project does not build or train a language
model. It builds a pipeline that *uses* an existing local model. "We built a local LLM" and "we ran
a local LLM" are different claims, and only the second is true.

## 2. Objectives

| # | Objective | How we would know it was met |
|---|---|---|
| O1 | Demonstrate an end-to-end document workflow with no cloud tenant dependency | The pipeline runs start to finish on one laptop |
| O2 | Show where automation is safe and where a human must intervene | A measurable gate that separates the two |
| O3 | Produce evidence, not assertions, about how well it works | Per-field accuracy against independently transcribed ground truth |
| O4 | Leave a design a later team could extend | Documented schema, migrations, ownership boundaries |

O3 is the objective that distinguishes this project. Any group can demo a pipeline that appears to
work. Reporting that extraction scores 20% and explaining precisely why is the harder and more
defensible result.

## 3. Stakeholders

| Stakeholder | Interest |
|---|---|
| Supervisor | Whether the concept is sound and the evaluation is honest |
| Markers | Clarity of concept, evidence quality, report and presentation |
| Team members | Clear lanes, mergeable code, no surprise rework |
| Notional end user (finance clerk) | Would not have to retype invoice data, and would be told when to check |

## 4. Scope

**In scope:** email intake, PDF text extraction, LLM field extraction, a validation gate, relational
storage, a review dashboard, and an evaluation harness.

**Out of scope:** training or fine-tuning any model, real accounting integration, payment execution,
multi-user authentication, production deployment, and any cloud service.

**Explicitly deferred:** OCR for scanned documents, Jira and Teams integration beyond simulated
calls, and the Trigger/Approval round.

## 5. Functional requirements

Priority: **M** must have for the demonstration, **S** should have, **C** could have.

### Phase 1, intake (Luke)

| ID | Requirement | Pri | Status |
|---|---|---|---|
| FR-1.1 | The system shall connect to a mailbox over IMAP using credentials from a `.env` file | M | Implemented |
| FR-1.2 | The system shall save PDF attachments to `inbox/` | M | Implemented |
| FR-1.3 | The system shall not re-download an attachment it has already saved | S | **Schema and API ready, intake not yet calling it.** `email_messages.message_id` is UNIQUE and `has_seen_email` is tested. Needs a PR to Luke's `email_listener.py` |

### Phases 2 and 3, extraction (JJ)

| ID | Requirement | Pri | Status |
|---|---|---|---|
| FR-2.1 | The system shall extract the text layer from a PDF | M | Implemented |
| FR-2.2 | The system shall extract invoice number, vendor, date, total and currency into a typed structure | M | **Partial, 3/15 fields correct** |
| FR-2.3 | The system shall extract line items with description, quantity and unit price | M | Implemented |
| FR-2.4 | The system shall run entirely on a local model with no external API call | M | Implemented |
| FR-2.5 | The system shall process scanned or image-only PDFs | C | **Not implemented, no OCR** |

### Phase 4, storage (Neo)

| ID | Requirement | Pri | Status |
|---|---|---|---|
| FR-4.1 | The system shall store extracted data in queryable columns, not a serialised blob | M | Implemented |
| FR-4.2 | The system shall reject structurally invalid data at the database level | M | Implemented |
| FR-4.3 | Processing the same document twice shall update one record, not create two | M | Implemented |
| FR-4.4 | The system shall record which run produced each record, with model name and threshold | M | Implemented |
| FR-4.5 | The system shall store monetary values without floating-point representation | S | Implemented |
| FR-4.6 | The system shall distinguish a value extracted by the model from one derived by the system | M | Implemented as `total_source` |
| FR-4.7 | Schema changes shall be applied by versioned migrations that never silently skip | M | Implemented |
| FR-4.8 | A document's record shall be written before its file is archived | S | Implemented |

### Phase 5, validation and review (Neo)

| ID | Requirement | Pri | Status |
|---|---|---|---|
| FR-5.1 | The system shall score each extraction and classify it as Validated or NeedsReview | M | Implemented |
| FR-5.2 | The score shall verify extracted values against the source text, not only their presence | M | Implemented for `total_amount`. `invoice_number` and `vendor_name` still use a substring test, deliberately not hardened until there are real documents to harden against |
| FR-5.3 | A reviewer shall be able to see every record, its score, and its line items | M | Implemented |
| FR-5.4 | A reviewer shall be able to approve or reject a record | M | Implemented |
| FR-5.5 | Approval shall record a human decision without overwriting the pipeline's judgement or score | M | Implemented |
| FR-5.6 | The dashboard shall show which totals were derived rather than extracted | S | Implemented |
| FR-5.7 | The system shall check the stated total against the sum of the line items | S | Implemented as `reconciliation` |
| FR-5.8 | The system shall distinguish an invoice from a receipt | S | Implemented as `document_type`, by heuristic, untested against real receipts |

### Phase 6, post-approval (unowned since 2026-08-14)

| ID | Requirement | Pri | Status |
|---|---|---|---|
| FR-6.1 | Approval shall create the work that follows it | S | Implemented. Invoice to `Payment`, Receipt to `File`, Unknown to `Review`, rejection to nothing |
| FR-6.2 | Every intended outbound notification shall be recorded, not printed | S | Implemented as `outbound_messages` |
| FR-6.3 | The system shall send notifications to Teams, Jira or Planner | C | **Not implemented and deliberately so.** Every outbox row stays `Pending`; no code path contacts an external system |

### Cross-cutting, evaluation

| ID | Requirement | Pri | Status |
|---|---|---|---|
| FR-7.1 | The system shall measure per-field accuracy against independently transcribed ground truth | M | Implemented |
| FR-7.2 | Ground truth shall not be derived from the document generator | M | Implemented |
| FR-7.3 | The evaluation shall state what it does not measure | M | Implemented |
| FR-7.4 | The cause of an extraction failure shall be isolated by controlled comparison | M | Implemented as `schema_comparison.py`: 3/15 against 15/15, one variable |
| FR-7.5 | The cost of a proposed fix shall be measured, not assumed | S | Implemented as `sentinel_comparison.py`: required fields invent values on 3 of 9 absent fields |

## 6. Non-functional requirements

| ID | Requirement | Rationale | Status |
|---|---|---|---|
| NFR-1 | No document content leaves the machine | The reason for the local pivot. It is also a genuine privacy argument for the report | Met |
| NFR-2 | The pipeline runs on a standard laptop with no GPU requirement | Every team member must be able to run it | Met |
| NFR-3 | Any teammate can reproduce a run from a clean checkout | `requirements.txt` pins every direct dependency | Met |
| NFR-4 | Claims about performance are reproducible by running something | The project's own standard | Met. `evaluation/` for extraction and its causes, 194 tests for storage |
| NFR-5 | The dashboard remains readable while the pipeline writes | WAL is enabled on every connection | Met |
| NFR-6 | Files owned by one team member are changed by pull request, not direct commit | Three people, one codebase | Process, currently observed |

## 7. Data requirements

The schema, every field's meaning, and the design decisions behind them are specified in
[database-spec.md](database-spec.md), which is the single source of truth for the data layer. This
document does not duplicate it.

Summary: three tables (`processing_runs`, `invoices`, `line_items`) plus `schema_version`, with
constraints enforced in the database, money as integer cents, and deduplication on a hash of the
extracted text.

## 8. Assumptions

| # | Assumption | If it is wrong |
|---|---|---|
| A1 | ~~Copilot is not a mandatory tool for this unit~~ | **Confirmed 2026-08-28: Copilot is not mandatory.** No longer an assumption. The local design stands as a replacement, not a hybrid. |
| A2 | Synthetic invoices are acceptable evidence for the demonstration | We would need real documents, which raises privacy questions we have not addressed |
| A3 | A local 3B-class model is a fair stand-in for Copilot's extraction | Results would not transfer, and the comparison in the report weakens |
| A4 | The demonstration does not need to handle scanned documents | OCR moves from "could have" to "must have" |

## 9. Constraints

- No UTS tenant permissions, which is what forced the local design.
- Three developers sharing one repository with no CI.
- The unit's timeline, which is the reason several correct-but-large changes are deferred.
- `Enterprise_AI_Workflow_Briefing.docx` is tracked and binary, so git cannot merge it.

## 10. Risks

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| ~~R1~~ | ~~Copilot turns out to be mandatory~~ | ~~Rework of the project's framing~~ | **Closed 2026-08-28. Confirmed not mandatory.** |
| R2 | The 20% extraction result is read as "local models do not work" | Wrong conclusion in the report | State the schema root cause, and show the 2x2 that isolates it |
| R3 | A confident wrong total reaches the dashboard | The demo shows plausible but false data | FR-5.2, verify totals against source text |
| ~~R4~~ | ~~Teammates run different dependency versions~~ | ~~Results that cannot be reproduced~~ | **Closed 2026-08-28.** `requirements.txt` written and verified |
| ~~R5~~ | ~~Binary `.docx` conflict blocks a merge~~ | ~~Lost work at the worst time~~ | **Closed 2026-08-28.** Untracked and gitignored; `generate_briefing_docx.py` rebuilds it, with every file inside byte-identical |

## 11. Acceptance for the demonstration

Given the project is assessed on concept clarity and presentation rather than production readiness,
the demonstration is acceptable when:

1. The pipeline runs end to end, live, on one machine.
2. The dashboard shows correct stored totals with derived values visibly labelled.
3. The evaluation harness prints per-field accuracy with its limitations stated.
4. We can explain **why** extraction scores 20% and what the fix is, with evidence.
5. The schema and its constraints can be shown refusing invalid data.

Point 4 matters more than a high score. A team that reports 20% and explains the cause
demonstrates more understanding than a team that reports 90% and cannot say why.
