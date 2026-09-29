# 1. Introduction

## 1.1 Industry context

*To be written.* Manual invoice handling in mid-sized organisations is estimated internally
at three to five minutes per document, with data-entry error as the main quality risk.

> **Gap.** That figure comes from our own briefing document and has no source. It needs a
> real citation on document-handling cost and error rates, or it must be removed. This is
> the one place in the report that requires outside literature about the problem rather
> than about the technology.

## 1.2 Objectives

Four objectives, each stated with how it would be judged. From `requirements-spec.md` §2.

1. Ingest invoices from email and process them without manual re-keying.
2. Extract the fields an accounts-payable process needs, into a queryable store.
3. **Produce evidence rather than assertions** about how well it works.
4. Keep a person in the approval path rather than automating the decision away.

## 1.3 Scope

In scope: text-layer PDF invoices, local inference, a normalised store, a review dashboard,
and since September, reading the email around the invoice rather than only its attachment:
classifying a message, summarising it, and extracting the actions people committed to in it.

Out of scope: OCR for scanned documents, payment execution, and integration with Teams,
Jira or Planner beyond a recorded queue.

**§1.4 is the authoritative statement of the boundary**, agreed and dated. This subsection is
the one-paragraph form and is kept because a reader arriving at the introduction should not have
to read a table to learn what the project is.

**A note on the objectives above.** All four were written when this project handled invoice
attachments and nothing else. The email work either falls inside objective 1, read broadly, or
deserves an objective of its own. This report does not settle that, because objectives belong to
the group rather than to one member, and §8.9 is explicit that the email half has no measured
result yet to claim against any objective.

## 1.4 The Proof of Concept boundary

Agreed by the group on 28 September 2026, following the supervisor's request for the deliverable
to be bounded explicitly rather than described. A scope statement with no date and no owner is
not a boundary, so both are recorded here.

**What "Proof of Concept" means in this project.** It means the pipeline runs end to end on one
machine, every stage produces evidence that can be re-run, and the parts that were not built are
named rather than left as implied future work. It does not mean a reduced version of a
production system. Several exclusions below would not be optional in a production system and are
not presented as though they were merely deferred.

### In scope, and built

| Capability | Where it is evidenced |
|---|---|
| Local processing of invoice PDFs, email bodies and attachment text, with no external API call | §4, §6 |
| Structured field extraction into a typed schema | §8.3 to §8.5 |
| Classification, summarisation, action items and deadlines from email | §8.9 |
| A deterministic validation gate with an explicit pass condition | §8.11 |
| Relational storage with constraints enforced in the database | §4.2 |
| Thread grouping and per-thread summaries | §8.9, with the limitation in §10.7 |
| Human review and approval through a browser interface | §6 |
| A local retrieval experiment over 5 to 10 anonymised invoice examples | §7 |

The retrieval experiment was added to the scope on 28 September, later than the rest. It is
listed as in scope because it was built and measured, and §7 reports what that measurement can
and cannot support.

### Out of scope, and why

| Excluded | Reason |
|---|---|
| Training or fine-tuning any model | This project uses an existing local model. See §3.4 |
| Production scalability | No load testing, no concurrency beyond one reader and one writer |
| Automatic payment execution | Nothing in the system moves money, and §10.4 argues it should not until three prerequisites exist |
| Compliance certification | No claim is made about any accounting or audit standard |
| Full OCR support | Extraction requires a PDF text layer. The largest functional gap, §10.7 |
| Support for every invoice layout | Heuristics are fitted to three generated documents, §10.1 |
| Real accounting system integration | No consumer exists for the outbound queue, §10.7 |
| Any cloud service | Not a preference. Seven administrative permissions the team could not obtain ended the original design, and that account is given earlier in this section |

### One exclusion that stopped being one

**Multi-user authentication was out of scope at specification time and has since been built.** It
is reported in §6 as delivered work rather than as a met requirement, because no requirement
called for it. This is recorded rather than tidied away: a scope boundary that silently absorbs
whatever gets built describes nothing, and the honest statement is that the team delivered
something outside the agreed scope and the scope document was updated afterwards rather than
before.

### What the boundary is protecting

Two of the exclusions do real work and are worth separating from the rest.

**Payment execution is excluded on safety grounds, not effort grounds.** §10.4 sets out why
reading accuracy is not payment authority, and why a document that is duplicated, fraudulent or
from an unknown vendor scores exactly as well as a legitimate one. Building payment execution
would have been straightforward and would have been wrong.

**OCR is excluded on honesty grounds.** Adding an OCR stage was within the team's technical
reach. It was not added because there is no labelled set of scanned documents to evaluate it
against, and shipping an unmeasured stage into a project whose central claim is that it measures
what it builds would have cost more than the capability was worth. §10.7 lists it first among
the functional gaps for exactly that reason.

## 1.5 The original design, and the constraints that ended it

The project began as a Microsoft-hosted workflow: SharePoint for document storage, Power
Automate for orchestration, a Copilot agent for extraction, and Power BI for reporting.
That design was chosen because the university tenant already provided the platform and
because the group had prior exposure to it.

It was abandoned. The reason is worth stating precisely, because "we changed technology" and
"we were prevented from building on the platform we chose" are different findings and only
the second one is true here.

| Constraint | First encountered |
|---|---|
| Premium Jira connector, unowned | 14 August 2026 |
| Dataverse self-service creation disabled for students | 17 August 2026 |
| SharePoint site creation unavailable to students | 18 August 2026 |
| Microsoft 365 MCP connector requires administrator consent | 18 August 2026 |
| Premium HTTP connector unavailable, unresolved | 22 August 2026 |
| Microsoft 365 Group creation for Planner, untestable | 22 August 2026 |
| Copilot agent credit limits reached | 23 August 2026 |

**Seven distinct blockers in ten days, none of which were technical.** Each was an
administrative permission the group did not hold and could not obtain within the project
timeline. No single one would have ended the design. Their accumulation did.

This is a finding rather than a complaint, and it generalises: a student or contractor team
building on an institutionally managed cloud tenant is dependent on permissions it does not
control, and that dependency is not visible at design time. The proposal that produced this
design was sound on paper. It failed on contact with the tenant's access model.

The pivot to a local stack was made on documented supervisor advice on 25 August 2026. That
distinction matters for how the project is assessed: deviating from a proposal is drift when
undocumented, and engineering judgement when the constraint, the advice and the date are all
recorded.

## 1.6 What the pivot cost

A pivot presented only in terms of what it unblocked is not an honest analysis. Three
capabilities in the original proposal have no equivalent in the local design.

**The Teams approval card.** The proposal's headline human-in-the-loop safeguard. Replaced
by a Streamlit dashboard with an explicit approval action, which preserves the function of a
person approving before anything is treated as final, but loses the property of reaching
approvers where they already work.

**The calibrated confidence gate.** The proposal specified a 0.8 confidence threshold. A
locally hosted model exposes no calibrated per-field confidence, so this was replaced by
rule-based validation: the amount must parse, be positive, and be located beside a
grand-total label in the source text; the invoice number must be present and appear in the
document. This is deterministic and auditable, and it yields an exception rate. **It is not a
confidence score**, and §8.11 records why the field was renamed to reflect that.

**The Power BI dashboard.** No equivalent data source existed in the local design. Replaced
by direct queries against SQLite and a dashboard built on the same store.

One property was gained rather than lost: with inference running locally, invoice content
never leaves the machine. That was a consequence of the pivot rather than its motivation,
but it is a defensible position on its own terms and §2 develops it.
