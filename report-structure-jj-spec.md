# Adopting JJ's ten-section structure

Written 2026-09-29, after JJ circulated the PoC report structure. Supersedes the twelve-section
map in `report/report-outline-v2.md`.

## The structure being adopted, exactly as JJ wrote it

```
1.  Introduction and PoC Scope
2.  Requirements
3.  System Architecture and Data Flow
4.  Security and Privacy
5.  Implementation
6.  RAG Method
7.  Evaluation
8.  Deployment and System Requirements
9.  Limitations and Future Work
10. Conclusion
```

Every section on that list is already written. Nothing has to be drafted, only moved.

## What JJ's structure has no place for

Three pieces of finished writing, 4,656 words, have no numbered home in a ten-section list.

| Piece | Words | Why it is not being deleted |
|---|---|---|
| Literature and Environmental Review | 1,821 | 19 verified sources. The rubric's Analysis and Design and Report structure criteria both draw on it |
| Discussion and Lessons | 1,994 | Feeds Quality of outcomes, a 20-mark criterion, and carries the seven lessons |
| References | 841 | APA is required throughout, including for our own figures |

**They are kept as back matter rather than dropped.** The numbered spine is JJ's ten sections
and nothing else, so the structure is followed exactly as circulated. Literature becomes
Appendix A and Discussion becomes Appendix B, both after the references, which is where APA puts
appendices.

This is a reversible decision recorded rather than a silent one. Deleting two files is a minute's
work if the group would rather match the team document exactly; recovering 3,815 words is not.

## Mapping

| Now | Becomes | File |
|---|---|---|
| 1. Introduction | 1. Introduction and PoC Scope | `section-01-introduction.md` |
| 2. Literature | **Appendix A** | `appendix-A-literature.md` |
| 3. Requirements | 2. Requirements | `section-02-requirements.md` |
| 4. System Architecture and Data Flow | 3. System Architecture and Data Flow | `section-03-architecture.md` |
| 5. Security and Privacy | 4. Security and Privacy | `section-04-security-privacy.md` |
| 6. Implementation | 5. Implementation | `section-05-implementation.md` |
| 7. RAG Method | 6. RAG Method | `section-06-rag-method.md` |
| 8. Evaluation and Testing | 7. Evaluation | `section-07-evaluation.md` |
| 9. Deployment and System Requirements | 8. Deployment and System Requirements | `section-08-deployment.md` |
| 10. Limitations and Future Work | 9. Limitations and Future Work | `section-09-limitations.md` |
| 11. Discussion | **Appendix B** | `appendix-B-discussion.md` |
| 12. Conclusion | 10. Conclusion | `section-10-conclusion.md` |
| References | References | `section-99-references.md` |

Ten of the thirteen files change number, so this touches more of the report than the pass on
28 September did. 111 headings and 126 cross-references move.

**Two references point at other documents and must not move:** `requirements-spec.md` §2 in the
introduction and `database-spec.md` §5 in the architecture section. The checker already skips
any reference preceded by a filename.

**Section 7 is renamed from "Evaluation and Testing" to "Evaluation"** to match JJ's list
exactly. Nothing in it changes.

## Verification

`scripts/check_report_refs.py` has to learn about lettered sections, since its heading pattern
is digits only. After that it must report zero dangling and zero duplicate, as it does now, and
the built document must show 1 to 10 contiguous followed by References, Appendix A and
Appendix B. The word count must not fall: nothing is being cut in this pass.


---

# Revised the same day: Literature, Discussion and References become sections 11, 12 and 13

The appendix arrangement above lasted a few hours. Putting three finished pieces behind the
references understates them for a marker, and two of the three carry marks directly: Discussion
feeds Quality of outcomes at 20 marks and Literature feeds Analysis and Design at 10.

So they are numbered sections now, after JJ's ten rather than inside them:

```
1-10  JJ's structure, unchanged and in his order
11.   Literature and Environmental Review
12.   Discussion and Lessons
13.   References
```

JJ's spine is still exactly as circulated. Nothing was inserted into it, renumbered within it,
or renamed. The three additions sit after it, which is the arrangement that keeps his structure
intact and keeps the rubric's material in the numbered body.

**Numbering the reference list as §13 is a small APA deviation** and is done deliberately, so
that the document has one numbering scheme rather than ten numbered sections followed by three
unnumbered ones. The list itself is APA throughout.

## Gaps are now marked in the document rather than tracked outside it

Seven markers, each a blockquote beginning **Gap** placed in the subsection the missing thing
belongs to, plus a citation-status block at the head of §13 and a consolidated list in the
front matter. A marker names what is missing, where the material for it already exists, and
what it still needs.

| Marker | Where | What is missing |
|---|---|---|
| Figures 1 and 3 | §3.1 | Architecture before and after the pivot; pipeline flow with phase ownership |
| Figure 2 | §3.2 | ER diagram, already renders from `database-spec.md` §3 |
| Figure 6 and two screenshots | §5.1 | Task and outbox lifecycle; the two dated dashboard screenshots in the repository |
| Figure 5 | §7.3 | Before-and-after totals from migration 001, as a captioned table |
| Figure 4 | §7.4 | The two-by-two, generated from `prompt_schema_2x2.py` rather than retyped |
| Screenshot and an open decision | §7.11.4 | The 28 September approval screen, and what its caption may say once the prompt fix ships |
| Unsourced figure | §1.1 | Already marked before this pass; carried forward |
| Citation status | §13 | Nothing missing. Records that all sixteen entries are cited |

**A citation count reported earlier in the day was wrong and is corrected here.** Counting only
parenthetical citations gave 5 of 17 entries cited and suggested a serious APA problem. Counting
narrative citations as well, and tolerating a citation wrapped across a line break, gives 16 of
16. The lesson is the project's own: a measurement taken with the wrong instrument reads as a
finding.
