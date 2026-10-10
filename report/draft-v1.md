---
title: "Local-First Invoice Processing: A Measured Investigation"
subtitle: "32040 Industry Project, Assignment 1 (Option A), Final Report Draft v4"
author: "Neo Pitayasiri"
date: "29 September 2026"
---

# Draft status

**Every section is written.** 23,850 words across thirteen sections. What remains is assembly
and verification rather than composition, and all of it is marked in place in the section it
belongs to rather than left implicit here.

**The structure is the ten-section Proof of Concept spine the team agreed**, in the order it
was circulated, with Literature, Discussion and References following it as sections 11 to 13.
Those three have no place in a ten-section technical document and are kept because this is an
individually marked report: Discussion carries the seven lessons and feeds a 20-mark criterion,
and Literature is the only part of the report whose claims rest on sources rather than on this
project's own measurements.

**The email half is reported without a single accuracy figure, on purpose.** It runs end to end
and its output is stored, and there is no ground truth for classification or summarisation, so
§7.9 reports counts and states plainly that none of them is a score. The temptation to present
the two halves as equally evidenced is what §9.3 warns against.

**Section 11 is written against sixteen verified sources.** Every author list, title and
identifier was checked on 16 September 2026 against the arXiv abstract page, ACL Anthology
record or publisher record. Nothing is cited from memory, because a fabricated citation in a
report about fabricated field values would be the worst available irony.

**One claim was weakened by the review rather than supported by it.** An earlier draft of
§11.6 claimed the locally-valid-but-semantically-wrong failure mode as this project's own
finding. Reddy et al. (2026) describe it in general terms, so §11.6 now claims something
narrower.

## Sections

| # | Section | Words | Status |
|---|---|---:|---|
| 1 | Introduction and PoC Scope | 1,558 | Written. §1.1 **needs a citation**, see below |
| 2 | Requirements | 2,300 | Written. 41 functional and 6 non-functional requirements, each with a status |
| 3 | System Architecture and Data Flow | 741 | Written. **Needs three figures** |
| 4 | Security and Privacy | 1,192 | Written from the code: two outbound calls, eight fields to Jira, and what is not protected |
| 5 | Implementation | 1,816 | Written. Thirteen design decisions, each with its alternative and its evidence. **Needs a diagram and two screenshots** |
| 6 | RAG Method | Updated 2026-10-10 | Written from merged PR #12 and its 30-document benchmark. Baseline 91.3%; RAG 94.0% |
| 7 | Evaluation | 6,434 | Written. Controlled experiments, a negative result, three defects found by testing. **Needs two figures**, and §7.11.4 rests on an open decision |
| 8 | Deployment and System Requirements | 1,103 | Written. Hardware and footprint measured on the machine the report was produced on |
| 9 | Limitations and Future Work | 1,752 | Written. Consolidates the limits stated throughout, deliberately read together |
| 10 | Conclusion | 998 | Written. Answers the four objectives one by one, plus a fifth strand deliberately not claimed |
| 11 | Literature and Environmental Review | 1,821 | Written against sixteen verified sources |
| 12 | Discussion and Lessons | 1,996 | Written. Eight lessons, each attached to the evidence that produced it |
| 13 | References | 989 | APA. All sixteen entries cited in the body, checked 29 September 2026 |

## What is still missing

Five things, in the order they cost marks.

**1. The report has no figures at all.** Not one image is placed and not one table carries a
numbered caption. Six are planned and three screenshots already exist in the repository,
dated and unused. Each gap is marked in the subsection where the figure belongs: Figures 1 and
3 in §3.1, Figure 2 in §3.2, Figure 4 in §7.4, Figure 5 in §7.3, Figure 6 and two screenshots
in §5.1, and one screenshot in §7.11.4. Every figure also needs a source line, including the
ones drawn from this project's own code.

**2. §1.1 states an industry-context figure with no source.** Three to five minutes per document
comes from this project's own briefing document, which is not a source. It must be replaced with
a citable figure or removed. §13 names two candidate papers.

**3. Page numbers and DOIs need a final check** on the entries in §13 that carry them.

**4. §7.12 records a one-word prompt fix that has now shipped.** It takes extraction from 66.7%
to 100% on the original sample and changes the meaning of the worked example in §7.11.4. That
screenshot and caption must be presented as historical pre-fix evidence rather than as the
current interface.

**5. One confirmation is owed, and it is not blocking.** Luke should check §4.2's list of what a
Jira issue carries against what he intended the integration to send. §6 has been re-read against
the merged PR #12 result and the frozen 30-document benchmark.

## How this document is built

Each section lives in its own file and they are concatenated by `report/build-docx.sh`, which
holds one ordered list read twice, once to build and once to count. A missing source fails the
build rather than producing a shorter report. `scripts/check_report_refs.py` verifies that every
§ cross-reference points at a section that exists; it reports zero dangling references.

The markdown is the source of truth. Rebuilding overwrites the `.docx`, so edits made in Word
are lost.

---

*Sections 1 to 13 follow in full, each from its own file.*
