---
title: "Local-First Invoice Processing: A Measured Investigation"
subtitle: "32040 Industry Project, Assignment 1 (Option A), Final Report Draft v2"
author: "Neo Pitayasiri"
date: "13 September 2026"
---

# Draft status

**This is version 2 and it is incomplete in one section only.** Sections 3 to 8 are written.
Section 1 is drafted and needs one citation. **Section 2 is the only section not yet written**,
and it is the only one that cannot be assembled from work already done, because it requires
reading outside literature rather than reporting this project's own measurements.

What is missing is left visible rather than disguised by placeholder text.

| Section | Status | Evidence available |
|---|---|---|
| 1. Introduction | Outline | Objectives from `requirements-spec.md`; industry context **needs a citation** |
| 2. Literature and Environmental Review | Outline | Anchor paper found; **the only section needing genuine new research** |
| **3. Problem Analysis** | **Written** | Seven documented constraints with dates; six defects found by measurement |
| **4. Design and Development** | **Written** | Ten design decisions, each with its alternative and its evidence |
| **5. Evaluation and Testing** | **Written** | Controlled experiments, a negative result, three defects found by testing |
| **6. Discussion** | **Written** | Six lessons, each attached to the evidence that produced it |
| **7. Recommendations** | **Written** | `database-spec.md` §8, plus three measured-but-unshipped changes |
| **8. Conclusion** | **Written** | Reflection against the four objectives, answered one by one |

Section 5 was written first because it holds the most evidence and requires the least
invention. Section 2 is scheduled last for the opposite reason.

Sections 3 to 8 live in their own files and are concatenated at build time. `build-docx.sh`
does this in the correct order; running `pandoc` by hand with a glob will place sections 6 to 8
before section 3, because this file contains the front matter.

---

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

In scope: text-layer PDF invoices, local inference, a normalised store, a review dashboard.

Out of scope: OCR for scanned documents, payment execution, and integration with Teams,
Jira or Planner beyond a recorded queue.

---

# 2. Literature and Environmental Review

*To be written. This is the thinnest section and the only one that cannot be assembled from
work already done.*

Research completed on 8 September 2026 identified the following, recorded in
`model-research-findings.md`:

**Constrained decoding and structured output.** Geng et al. (2025), *JSONSchemaBench*,
benchmarks six constrained-decoding frameworks including XGrammar, the engine Ollama uses,
across 10,000 real-world JSON schemas. One of its three dimensions is coverage of constraint
types, which is where this project's central defect sits. This positions the finding in
§5.4 inside existing work rather than beside it.

**Document extraction benchmarks.** SROIE (626 train, 347 test real scanned receipts,
labelling company name, date, total and address), CORD (1,000 receipts, 30 hierarchical
entities), WildReceipt and DocILE. SROIE's label set is close to ours, which allows a direct
statement of how small this evaluation is.

**Local inference and privacy.** *OnPrem.LLM* (2025) is a privacy-conscious local document
intelligence toolkit and is the closest published system to the one built here.

> **Still to do.** Read the papers, not the abstracts. Position the schema finding against
> what is already known rather than merely citing near it.

---

*Sections 3 and 4 follow, in full.*

---

*Sections 3 to 8 follow in full, from `section-3-problem-analysis.md` through
`section-8-conclusion.md`, then the reference list.*
