---
title: "Local-First Invoice Processing: A Measured Investigation"
subtitle: "32040 Industry Project, Assignment 1 (Option A), Final Report Draft v4"
author: "Neo Pitayasiri"
date: "16 September 2026"
---

# Draft status

**This is version 4.** Version 3 completed a draft of every section. Version 4, 17 September
2026, brings the report level with what the system now does: the project also reads the message
around the invoice, and that work had been built while the report still described an
attachment-only pipeline.

**The email half is reported without a single accuracy figure, on purpose.** It runs end to end
and its output is stored, and there is no ground truth for classification or summarisation, so
§5.9 reports counts and states plainly that none of them is a score. The temptation to present
the two halves as equally evidenced is exactly what §5.10 warns against.

What remains is verification, not composition, and it is marked in place rather than left
implicit.

**Section 2 is written against 19 verified sources.** Every author list, title and identifier
was checked on 16 September 2026 against the arXiv abstract page, ACL Anthology record or
publisher record. Nothing is cited from memory, because a fabricated citation in a report about
fabricated field values would be the worst available irony.

**One claim was weakened by the review rather than supported by it.** An earlier draft of §2.6
claimed the locally-valid-but-semantically-wrong failure mode as this project's own finding.
Reddy et al. (2026) describe it in general terms, so §2.6 now claims something narrower.

**Section 1.1 still has no source** for its industry-context figure, and the GDPR figure that
appeared in an earlier draft of §2 has been removed rather than cited from a secondary source.
Page numbers and DOIs need a final check on the four entries that carry them.

| Section | Status | Evidence available |
|---|---|---|
| 1. Introduction | Drafted | Objectives from `requirements-spec.md`; industry context **still needs a citation**. Scope updated 2026-09-17: the email half is in scope, and whether it needs an objective of its own is left to the group |
| **2. Literature and Environmental Review** | **Written** | 19 sources, all verified against their records on 2026-09-16 |
| **3. Problem Analysis** | **Written** | Seven documented constraints with dates; six defects found by measurement |
| **4. Design and Development** | **Written** | Thirteen design decisions, each with its alternative and its evidence. Three added 2026-09-17 for the email half |
| **5. Evaluation and Testing** | **Written** | Controlled experiments, a negative result, three defects found by testing. §5.9 added 2026-09-17: the email half, reported as counts with **no accuracy claimed** |
| **6. Discussion** | **Written** | Eight lessons, each attached to the evidence that produced it. Two added 2026-09-17: a metric that hid what it was built to find, and tests that shared their author's blind spots |
| **7. Recommendations** | **Written** | `database-spec.md` §8, three measured-but-unshipped changes, and three gaps in the email half |
| **8. Conclusion** | **Written** | Reflection against the four objectives, answered one by one, plus a fifth strand deliberately not claimed as one |

Section 5 was written first because it holds the most evidence and requires the least
invention. Section 2 was written last for the opposite reason, and is the only section whose
claims rest on sources rather than on this project's own measurements.

Sections 3 to 8 live in their own files and are concatenated at build time. `build-docx.sh`
does this in the correct order; running `pandoc` by hand with a glob will place sections 6 to 8
before section 3, because this file contains the front matter.

---

*Sections 1 to 8 follow in full, each from its own file, then the reference list.*
