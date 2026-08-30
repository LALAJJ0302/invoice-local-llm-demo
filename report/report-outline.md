# Final Report: Outline and Evidence Map

**Assignment 1, Option A, 32040 Industry Project.** Individual, 80 marks, 80% of subject.
~30-45 pages body. Draft due Week 8, final due end of Week 11. Must use the Canvas template.

**Status:** outline only, 2026-08-30. Nothing written yet.

This document maps each required section to the evidence that already exists, so drafting is
assembly rather than invention. Where a section has no evidence it says so, because those are the
gaps that need work rather than words.

---

## The argument the whole report makes

One sentence, and every section should serve it:

> A document-processing workflow was designed for a cloud platform, blocked by institutional
> constraints, rebuilt locally, measured honestly, found to be failing for a non-obvious reason in
> our own code rather than in the model, and corrected.

The weakest version of this report presents a working demo. The strongest version presents a
**measured investigation**. We have the evidence for the second, and it is unusual to have.

---

## Section map

### 1. Introduction

| Needs | Have | Source |
|---|---|---|
| Industry context: manual invoice handling | Partial | Briefing doc: 3-5 min per document, labour cost, entry errors |
| Objectives | Yes | `requirements-spec.md` §2, four objectives with how each would be judged |
| Scope and non-scope | Yes | `requirements-spec.md` §4 |

**Gap:** the industry context is asserted, not cited. Needs a real source for document-handling cost
and error rates. This is the one place the report needs outside literature rather than our own data.

### 2. Literature and Environmental Review

| Needs | Have | Source |
|---|---|---|
| Existing solutions | Weak | Only the Microsoft stack we tried |
| Local LLM extraction as a technique | **No** | — |
| Structured output / constrained decoding | Partial | Our own finding, no literature behind it |

**This is the thinnest section and the one that needs the most new work.** Everything else in the
report is assembly. Suggested angles: constrained decoding and JSON Schema in LLM tooling; document
understanding benchmarks; privacy arguments for local inference. The schema finding in §5 becomes
much stronger if it can be positioned against what is already known.

### 3. Problem Analysis

| Needs | Have | Source |
|---|---|---|
| Current issues, tiered by severity | Yes | `database-spec.md` §8, `team-sync-notes.md` §3 |
| Stakeholder needs | Partial | `requirements-spec.md` §3 |
| Constraints that shaped the design | Yes | Vault: Dataverse block, SharePoint block, MCP consent block, Copilot credits |

Strong section. The constraint history is documented with dates and is a genuine finding: repeated
administrative unavailability, not a single obstacle.

### 4. Project Design and Development

| Needs | Have | Source |
|---|---|---|
| Architecture, before and after the pivot | Yes | Briefing doc comparison table, `CLAUDE.md` |
| Data model | Yes | `database-spec.md` §3 ER diagram, §4 data dictionary |
| Design decisions with rationale | **Yes, unusually strong** | `database-spec.md` §5, nine decisions each with evidence |
| Ownership and collaboration model | Yes | `CLAUDE.md` ownership table |

The nine design decisions in `database-spec.md` §5 are the core of this section. Each states a
decision, the alternative, and the measurement that settled it. Lead with §5.1: the deduplication
key that reviewed fine on paper and would have deduplicated nothing.

### 5. Evaluation and Testing

| Needs | Have | Source |
|---|---|---|
| Method, including what it does not measure | Yes | `evaluation/evaluation-method.md` |
| Baseline | Yes | 3/15 (20%), `results_before.json`, frozen 2026-08-26 |
| Root cause, isolated | Yes | `schema_comparison.py`: 3/15 vs 15/15, one variable |
| Cost of the fix | Yes | `sentinel_comparison.py`: invents on 3 of 9 absent fields |
| A rejected hypothesis | Yes | The sentinel, measured and ruled out |
| Regression protection | Yes | 194 tests, two bugs found on their first run |

**The strongest section in the report, and it should be written first.** It contains a controlled
experiment, a negative result, and two defects found by testing. Most student reports have none of
these.

Do not lead with 100%. Lead with the method, then the before, then the cause, then the after, then
the cost. The number is the least interesting part.

### 6. Discussion

| Needs | Have |
|---|---|
| Why the pivot was the right call | Yes, with dates and supervisor advice |
| What the 20% actually means | Yes |
| Limits of the evaluation | Yes, stated by us |
| Lessons learned | Yes, four below |

Four candidate lessons, all measured rather than asserted:

1. **A design can review correctly and fail on contact with data.** The byte-hash dedup key would
   have deduplicated nothing, because ReportLab writes a random `/ID` into every PDF.
2. **A claim repeated in documentation is not a measurement.** "Required fields fix it" was in our
   notes for days before anything in the repository could reproduce it.
3. **Constraints written once are not tested by being written.** `GLOB '____-__-__'` rejected every
   valid date and never fired, because no row ever carried one.
4. **A fix can move a failure rather than remove it.** Required fields took extraction from 20% to
   100% and made the model invent values for a third of genuinely absent fields.

### 7. Recommendations and Future Work

| Needs | Have |
|---|---|
| Known limitations | Yes, `database-spec.md` §8 |
| What a later team should do | Yes: OCR, real documents, gate hardening, the integrations |
| Industry implications | Partial |

Be specific about what is deliberately unfinished and why. The outbox holding only `Pending` rows is
an honest boundary, not an incomplete feature, and should be presented that way.

### 8. Conclusion

Reflection against the four objectives in `requirements-spec.md` §2. Objective 3, "produce evidence
rather than assertions", is the one this project actually delivered on.

---

## Rubric coverage check

| Criterion | Marks | Where it is earned | Confidence |
|---|---|---|---|
| Objectives met | 10 | §1, §8 against `requirements-spec.md` §2 | Medium: extraction is not fixed yet |
| Completeness | 10 | §4, plus specs, migrations, tests | High |
| Analysis and Design | 10 | §3, §4, the nine decisions | High |
| **Solution of project** | **20** | §4 and §5 together, the causal chain | High |
| **Quality of outcomes** | **20** | §5, §6, honest limits | High |
| Report structure | 10 | Writing quality, APA, figures | Not started |

The two 20-mark criteria are half the marks and both are served by material that already exists.

---

## Figures to prepare

1. Architecture before and after the pivot (briefing doc has the comparison table)
2. ER diagram (`database-spec.md` §3, renders from mermaid)
3. Pipeline flow with phase ownership
4. The 2x2: schema against prompt, from `schema_comparison.py`
5. Before/after totals table from migration 001
6. Task and outbox lifecycle

---

## Suggested writing order

Not the reading order. Write where the evidence is densest first, so momentum comes early.

```
1. §5 Evaluation and Testing      most evidence, least invention
2. §3 Problem Analysis            documented with dates
3. §4 Design and Development      nine decisions already written
4. §6 Discussion                  four lessons above
5. §7 Recommendations             §8 of database-spec
6. §1 Introduction                easier once the body exists
7. §8 Conclusion                  last
8. §2 Literature Review           the only section needing genuine new research
```

§2 is listed last deliberately: it is the only section that cannot be assembled from what we have,
so it should not block the seven that can.

---

## Two things to confirm before drafting

1. **What week is it, and when exactly is the draft due?** The vault records "Week 8" without a
   semester start date. Two weeks and four weeks are different plans.
2. **Download the Canvas report template.** Section names must match it; the structure above follows
   the suggested structure in the assignment brief, not the template itself.
