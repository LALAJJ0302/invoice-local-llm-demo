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
