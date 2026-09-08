# 3. Problem Analysis

## 3.1 The original design, and the constraints that ended it

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

## 3.2 What the pivot cost

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
confidence score**, and §5.9 records why the field was renamed to reflect that.

**The Power BI dashboard.** No equivalent data source existed in the local design. Replaced
by direct queries against SQLite and a dashboard built on the same store.

One property was gained rather than lost: with inference running locally, invoice content
never leaves the machine. That was a consequence of the pivot rather than its motivation,
but it is a defensible position on its own terms and §2 develops it.

## 3.3 Defects in the first working version

The local pipeline ran end to end within days. It did not work. The defects below were found
by measuring rather than by use, and they are ordered by how badly they would have misled
someone relying on the system.

**Extraction returned almost nothing, and reported success.** 3 of 15 field-values correct.
Every field except the vendor name came back `None`, `0.0` or `"Unknown"`. No error was
raised at any layer, because each layer was behaving correctly: the model returned valid
JSON, Pydantic validated it, and the database stored it. §5.4 analyses the cause.

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

**A constraint rejected every valid date.** Discussed in §5.7.

These share a property that shaped the rest of the project. **None of them produced an error
message.** Every one was a component doing exactly what it had been told to do, where what it
had been told was wrong. A system can be fully operational and produce nothing of value, and
the only way to tell the difference is to measure it against known-correct answers.

## 3.4 Stakeholder needs

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
