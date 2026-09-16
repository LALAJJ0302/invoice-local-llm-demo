---
title: "Local-First Invoice Processing: A Measured Investigation"
subtitle: "32040 Industry Project, Assignment 1 (Option A), Final Report Draft v3"
author: "Neo Pitayasiri"
date: "16 September 2026"
---

# Draft status

**This is version 3. Every section now has a draft.** What remains is verification, not
composition, and it is marked in place rather than left implicit.

**Section 2 carries six `Verify before submission` markers.** It was written from a literature
search rather than from full readings of the papers, so every claim that depends on a paper's
contents rather than its existence is flagged where it sits. Those markers must be resolved or
the claims removed before this is submitted.

**The reference list is deliberately incomplete.** Only one entry has a verified author list.
Author names are not filled in from memory, because a fabricated citation in a report about
fabricated field values would be the worst available irony.

**Section 1.1 still has no source** for its industry-context figure.

| Section | Status | Evidence available |
|---|---|---|
| 1. Introduction | Drafted | Objectives from `requirements-spec.md`; industry context **still needs a citation** |
| **2. Literature and Environmental Review** | **Drafted, 6 markers** | Anchor paper plus 4 benchmarks and 7 further works; **claims need checking against the papers** |
| **3. Problem Analysis** | **Written** | Seven documented constraints with dates; six defects found by measurement |
| **4. Design and Development** | **Written** | Ten design decisions, each with its alternative and its evidence |
| **5. Evaluation and Testing** | **Written** | Controlled experiments, a negative result, three defects found by testing |
| **6. Discussion** | **Written** | Six lessons, each attached to the evidence that produced it |
| **7. Recommendations** | **Written** | `database-spec.md` §8, plus three measured-but-unshipped changes |
| **8. Conclusion** | **Written** | Reflection against the four objectives, answered one by one |

Section 5 was written first because it holds the most evidence and requires the least
invention. Section 2 was written last for the opposite reason, and is the only section whose
claims rest on sources rather than on this project's own measurements.

Sections 3 to 8 live in their own files and are concatenated at build time. `build-docx.sh`
does this in the correct order; running `pandoc` by hand with a glob will place sections 6 to 8
before section 3, because this file contains the front matter.

---

*Sections 1 to 8 follow in full, each from its own file, then the reference list.*
