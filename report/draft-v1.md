---
title: "Local-First Invoice Processing: A Measured Investigation"
subtitle: "32040 Industry Project, Assignment 1 (Option A), Final Report Draft v1"
author: "Neo Pitayasiri"
date: "8 September 2026"
---

# Draft status

**This is version 1 and it is incomplete.** Sections 3, 4 and 5 are written. The remaining
five are outlined with the evidence each will draw on, so that what is missing is visible
rather than disguised by placeholder text.

The three written sections are the three that carry the most marks: Analysis and Design (10),
Solution of project (20) and Quality of project outcomes (20).

| Section | Status | Evidence available |
|---|---|---|
| 1. Introduction | Outline | Objectives from `requirements-spec.md`; industry context **needs a citation** |
| 2. Literature and Environmental Review | Outline | Anchor paper found; **the only section needing genuine new research** |
| **3. Problem Analysis** | **Written** | Seven documented constraints with dates; six defects found by measurement |
| **4. Design and Development** | **Written** | Ten design decisions, each with its alternative and its evidence |
| **5. Evaluation and Testing** | **Written** | Controlled experiments, a negative result, three defects found by testing |
| 6. Discussion | Outline | Five lessons, all measured |
| 7. Recommendations | Outline | `database-spec.md` §8 |
| 8. Conclusion | Outline | Reflection against the four objectives |

Section 5 was written first because it holds the most evidence and requires the least
invention. Section 2 is scheduled last because it is the only section that cannot be
assembled from work already done.

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

# 5. Evaluation and Testing

*The full section is written and is included from `section-5-evaluation.md`.*

Summary of what it establishes:

- A baseline of 3/15 field-values (20%), with the gate passing 0 of 3 documents.
- A controlled two-by-two showing that **both** the permissive schema **and** the original
  prompt were causes, that either fix alone reaches 15/15, and that they are not additive.
  This corrects an earlier claim that the schema was the sole cause.
- The cost of the fix: requiring fields makes the model invent values for 3 of 9 genuinely
  absent fields, where the permissive schema invented none.
- A rejected mitigation, measured and ruled out rather than assumed away.
- Six models compared on the target hardware. All handled constrained decoding correctly;
  the variable that separates them is latency, not accuracy.
- Three defects found by testing, **none of which produced an error**.
- An unresolved discrepancy between two measurements of the same configuration, reported
  as unresolved rather than resolved by choosing the convenient number.

---

# 6. Discussion

*To be written. Five lessons, all measured rather than asserted.*

1. **A design can review correctly and fail on contact with data.** The byte-hash
   deduplication key.
2. **A claim repeated in documentation is not a measurement.** "Required fields fix it" sat
   in project notes for days before anything in the repository could reproduce it.
3. **Constraints written once are not tested by being written.** A `CHECK` constraint
   rejected every valid date and never fired, because no row ever carried one.
4. **A fix can move a failure rather than remove it.** Required fields took extraction to
   100% and made the model invent values for a third of genuinely absent fields.
5. **A single-cause explanation is fragile.** The schema was reported as the cause of the
   20% result for two weeks. A fuller experiment showed the prompt was an equally
   sufficient cause. The original comparison was not wrong; it was incomplete, and it was
   reported with more confidence than its design supported.

A sixth, on how this project was built: it was produced working with an AI assistant that
made several identifiable errors, each caught by a verification mechanism. The assistant's
worst errors were **the same shape as the model's**: a gap filled with something that
looked like an answer. The model filled it with a default; the assistant filled it with an
unverified assertion. Both were invisible, because `None` and a confident sentence both
look like data.

---

# 7. Recommendations and Future Work

*To be written.*

- **OCR.** Scanned invoices are skipped entirely. This is the largest functional gap.
- **Real documents.** Every heuristic is fitted to three self-generated files.
- **Retrieval strategy.** Two strategies are implemented and neither has been measured
  against the other. A third using embeddings is deliberately unbuilt until that
  measurement exists.
- **Deliberately unfinished.** The notification outbox holds only `Pending` rows. That is an
  honest boundary, not an incomplete feature, and should be presented that way.

---

# 8. Conclusion

*To be written. Reflection against the four objectives in §1.2.*

Objective 3, produce evidence rather than assertions, is the one this project delivered on
most completely, and it is also the objective that produced the correction in §5.4.2.

---

# References

*To be completed in APA format. Every figure and table, including ones generated from this
project's own code, requires a caption and a source statement.*

- Geng, S., Cooper, H., Moskal, M., Jenkins, S., Berman, J., Ranchin, N., West, R.,
  Horvitz, E., & Nori, H. (2025). *JSONSchemaBench: A rigorous benchmark of structured
  outputs for language models.* arXiv:2501.10868.
