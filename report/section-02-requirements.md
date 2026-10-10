# 2. Requirements

## 2.1 How the requirements were derived

This project had no external client. Requirements could not be elicited by interview, so they
were derived from three sources and each is traceable to one of them: the group's original
project proposal, the supervisor's advice to de-scope after the cloud platform proved
unavailable, and the constraints documented in §1 of this report.

Requirements are prioritised with MoSCoW, reduced to three levels because a fourth adds no
information at this scale: **M** must have for the demonstration, **S** should have, **C** could
have.

One property of the requirements specification is worth stating, because it is unusual and it
carries into the evaluation. **Every requirement records its implementation status, and the
status is written against what the code does rather than what the plan intended.** Requirements
documents are commonly written once and not revisited, which turns them into a record of
intent. Here the specification is maintained alongside the code, so a requirement that was not
met says so, and says why. Six of the forty-one functional requirements are not fully met, and
§2.5 names each one rather than reporting a completion percentage.

## 2.2 Objectives

| # | Objective | How we would know it was met |
|---|---|---|
| O1 | Demonstrate an end-to-end document workflow with no cloud tenant dependency | The pipeline runs start to finish on one laptop |
| O2 | Show where automation is safe and where a human must intervene | A measurable gate that separates the two |
| O3 | Produce evidence, not assertions, about how well it works | Per-field accuracy against independently transcribed ground truth |
| O4 | Leave a design a later team could extend | Documented schema, migrations, ownership boundaries |

**O3 is the objective that distinguishes this project.** Any group can demonstrate a pipeline
that appears to work. Reporting a measured extraction result and explaining precisely what
causes it is the harder and more defensible outcome, and §7 is built on that position.

One limitation of these objectives should be recorded rather than smoothed over. O1 to O4 were
written when the project handled invoices only. The email understanding work described in §5
either falls under O1's "end-to-end document workflow" or warrants an objective of its own, and
the group has not decided which. The objectives are reported as written rather than
retrospectively widened to fit the work that followed them.

## 2.3 Stakeholders

| Stakeholder | Interest |
|---|---|
| Supervisor | Whether the concept is sound and the evaluation is honest |
| Markers | Clarity of concept, evidence quality, report and presentation |
| Team members | Clear lanes, mergeable code, no surprise rework |
| Notional end user, a finance clerk | Would not have to retype invoice data, and would be told when to check |

The fourth is notional. No end user was interviewed, and the report does not claim otherwise.
Where a design decision is justified by user need, that need is inferred from the workflow and
is labelled as an inference.

**The workflow roles are a different list, and the design serves them in a specific order.**

The workflow serves three roles, and the design serves them in a specific order.

**Accounts payable** needs the payable amount to be right, and needs to know when it might
not be. This is why an amount that cannot be located beside a total label is never
auto-approved regardless of the overall score, and why the gate reports which fields were
empty rather than only a number.

**An approver** needs to know what to check. A score alone does not tell them; a reason does.
The gate therefore emits a sentence naming the specific problem.

**A later maintainer** needs to know why the system is shaped the way it is. This is why the
design decisions are recorded with their alternatives and the measurements that settled them,
and why rejected approaches are documented alongside accepted ones.

The order matters. Every automation decision in this project fails toward a person rather
than toward a guess: an unclassifiable document is routed to review rather than assigned a
likely type, and a `Validated` document still requires human approval, because automation
reduces the reading rather than removing the decision.

## 2.4 Scope of the Proof of Concept

**In scope.** Email intake, PDF text extraction, field extraction by a local language model,
email classification and summarisation, a deterministic validation gate, relational storage,
a human review and approval interface, and an evaluation harness.

**Out of scope.** Training or fine-tuning any model, real accounting integration, payment
execution, production deployment, and any cloud service. Multi-user authentication was out of
scope at specification time and has since been implemented by one team member; it is reported in
§5 as delivered work rather than as a met requirement, because no requirement called for it.

**Explicitly deferred.** Optical character recognition for scanned documents, live Jira and
Teams integration beyond a recorded outbound queue, and full OCR support for arbitrary invoice
layouts.

The boundary that matters most is the first exclusion. **This project does not build or train a
language model. It builds a pipeline that uses an existing local model.** "We built a local
LLM" and "we ran a local LLM" are different claims and only the second is true of this work.
Everything the evaluation in §7 measures is a property of a pipeline around a model, not a
property of a model.

## 2.5 Functional requirements

Forty-one functional requirements are specified across seven groups, aligned to the processing
phases. The full table, with a status note per requirement, is maintained in
`requirements-spec.md` and is not duplicated here. The summary below reports status by group.

| Group | Requirements | Met | Partial | Not met |
|---|---|---|---|---|
| FR-1 Intake | 3 | 2 | 1 | 0 |
| FR-2 Document extraction | 5 | 4 | 0 | 1 |
| FR-3 Email understanding | 9 | 7 | 1 | 1 |
| FR-4 Storage | 8 | 8 | 0 | 0 |
| FR-5 Validation and review | 8 | 7 | 1 | 0 |
| FR-6 Post-approval | 3 | 2 | 0 | 1 |
| FR-7 Evaluation | 5 | 5 | 0 | 0 |
| **Total** | **41** | **35** | **3** | **3** |

The six requirements that are not fully met are named below, because a completion percentage
hides which ones they are, and in this project the unmet requirements are more informative than
the met ones.

**FR-1.3, duplicate attachments are not yet prevented at intake.** The database schema and the
API both support it: `email_messages.message_id` carries a UNIQUE constraint and the guard
function is tested. Intake does not yet call it. This is an integration gap across an ownership
boundary rather than a missing capability.

**FR-2.5, scanned or image-only PDFs are not processed.** The extractor reads a text layer using
`pypdf`, and a document without one produces nothing. No OCR stage exists. This was accepted as
a deferred requirement at specification time and remains the largest functional limitation of
the system.

**FR-3.8, thread grouping is partial.** Messages are grouped by subject line and the method is
recorded on every stored row as `thread_source`. The correct key is the `In-Reply-To` and
`References` headers, which requires a change to intake. One of sixteen threads in the test
mailbox is a known subject collision, so the error is quantified rather than suspected.

**FR-3.9, the email half is not scored against ground truth.** This is the most significant
unmet requirement in the project. Classification, summarisation and action extraction can be run
and inspected, but there is no labelled set, so none of it has a number. The contrast with
extraction is deliberate and is drawn in §7: extraction has had independently transcribed ground
truth since 26 August 2026, and the email half has none.

**FR-5.2, source verification is complete for one field only.** The validation gate verifies
`total_amount` against the document text in three tiers, requiring the amount to appear near a
grand-total label before it can pass. `invoice_number` and `vendor_name` still use a substring
test. Hardening those was deliberately deferred until there are real documents to harden
against, because a rule tuned on three synthetic invoices would encode the generator rather than
the domain.

**FR-6.3, no notification reaches an external system.** Every intended outbound message is
recorded in an `outbound_messages` table and every row remains pending. No code path contacts
Teams, Jira or Planner during a pipeline run. This is a deliberate boundary rather than an
incomplete feature: it makes the integration seam visible and testable without requiring
credentials for a third-party service, and §4 reports what a real dispatch would send.

## 2.6 Non-functional requirements

| # | Requirement | Rationale | Status |
|---|---|---|---|
| NFR-1 | No document content leaves the machine | The reason for the local design, and the privacy argument in §4 | Met |
| NFR-2 | The pipeline runs on a standard laptop with no GPU requirement | Every team member must be able to run it | Met |
| NFR-3 | Any teammate can reproduce a run from a clean checkout | `requirements.txt` pins every direct dependency | Met |
| NFR-4 | Claims about performance are reproducible by running something | The project's own evidential standard | Met |
| NFR-5 | The dashboard remains readable while the pipeline writes | Write-ahead logging is enabled on every connection | Met |
| NFR-6 | Files owned by one team member are changed by pull request | Three developers, one repository, no continuous integration | Observed as process |

NFR-4 is the standard the rest of this report is written to. Where a figure appears in §7 it is
accompanied by the command that reproduces it. **Counts that change with the codebase, such as
the number of automated tests, are deliberately not fixed in prose**, because a number in a
document decays where a command does not.

NFR-6 is recorded as a process rather than a met requirement because it is enforced by agreement
and not by tooling. It was breached once and then held. Two commits changing files in another
member's area were pushed directly to the shared main branch on 3 September 2026; every
subsequent change by the same author, thirteen commits through 24 September, arrived through a
pull request.

The evidence is the first-parent history of the shared branch, where a directly pushed commit
appears on the chain itself and a reviewed one appears only behind a merge. **This is a case of
the project's own evidential standard applied to its process rather than to its output**, and it
illustrates why NFR-4 is written as it is: the group's own documentation recorded the author's
contribution as two commits, a figure that was accurate when written and had decayed by a factor
of seven by the time this section was drafted. The consequence of the breach is discussed in
§12.

## 2.7 Assumptions

| # | Assumption | Consequence if wrong |
|---|---|---|
| A1 | Copilot is not a mandatory tool for this unit | **Resolved 28 August 2026. Confirmed not mandatory**, so the local design stands as a replacement rather than a hybrid |
| A2 | Synthetic invoices are acceptable evidence for a demonstration | Real documents would be required, raising privacy questions the project has not addressed |
| A3 | A local 3B-class model is a fair stand-in for a commercial extraction service | Results would not transfer, and the comparison in §12 weakens |
| A4 | The demonstration need not handle scanned documents | OCR moves from a deferred requirement to a mandatory one |

A2 and A3 are the two that constrain how far the results generalise, and §7.2 and §9 return to
both. A1 was the project's largest open unknown for five days and its resolution is what allowed
the local design to be presented as a complete story rather than a contingency.

## 2.8 Constraints

- No administrative permissions on the university tenant, which is what ended the original
  design. §1 documents seven distinct blockers encountered in ten days.
- Three developers sharing one repository with no continuous integration, so correctness is
  enforced by a local test suite and by review rather than by a gate.
- A fixed academic timeline, which is the stated reason several specified and measured changes
  remain unshipped. §9 lists them.

## 2.9 Acceptance criteria for the demonstration

The project is assessed on concept clarity, evidence and presentation rather than production
readiness. The demonstration is therefore accepted when:

1. The pipeline runs end to end, live, on one machine.
2. The dashboard shows stored totals with derived values visibly labelled as derived.
3. The evaluation harness prints per-field accuracy together with its stated limitations.
4. The cause of the extraction result can be explained with evidence, separating the part
   attributable to the model from the part attributable to the project's own repair code.
5. The schema and its constraints can be shown refusing invalid data.
6. The email half runs live, and its output is stored and queryable.
7. It can be stated plainly which results are scored against ground truth and which are only
   inspected.

**Criterion 4 matters more than a high score, and criterion 7 is the same standard applied to
the newer half of the project.** A team that explains a cause demonstrates more understanding
than a team that reports a high number and cannot account for it. Criterion 7 exists because the
honest answer for the email half is currently "not scored", and an acceptance criterion that
allowed that to go unsaid would be the wrong criterion.
