# Final Report: Outline v2, reconciled with the team's PoC structure

**Assignment 1, Option A, 32040 Industry Project.** Individual, 80 marks, 80% of subject.
Written 2026-09-28, superseding the section map in [report-outline.md](report-outline.md).
That document is kept, not deleted: its rubric coverage table and evidence map are still the
authority on where marks are earned, and only the section numbering changes here.

## Why this exists

The team's plan of 2026-09-28 proposed a ten-section structure for the Proof of Concept,
written in response to the professor's feedback. Nine report sections were already drafted
against a different structure, totalling about 14,400 words.

**The two are reconcilable and nothing has to be rewritten.** Seven of the team's ten sections
are already written under another name. Four are genuinely new, and only one of those four is
blocked on work that does not yet exist.

**The team's structure was adopted as the spine, with three additions.** Every one of its ten
sections survives, in order, with its name intact. What is added back is material that had no
home in it and that the marking rubric rewards directly:

| Added back | Words already written | Why |
|---|---|---|
| Literature and Environmental Review | 1,821 | Written against 19 verified sources. The rubric's "Analysis and Design" and "Report structure" criteria both depend on it |
| Discussion and Lessons | 1,771 | Feeds "Quality of outcomes", the joint-largest criterion at 20 marks |
| References | 841 | APA is required throughout, and every figure and table must carry a citation, including ours |

Dropping those three would remove 4,433 written words from an individually marked report in
order to match a structure designed for a group technical document. The two documents can share
evidence without sharing a table of contents.

---

## Section map

Status is stated per section. **Have** means drafted and in the repository. **New** means it has
to be written, and the third column says what it is assembled from rather than invented.

| # | Section | Status | Source material | Blocked on |
|---|---|---|---|---|
| 1 | Introduction and PoC Scope | **Have**, needs a scope statement | §1.1–1.3 plus §3.1–3.2 on the pivot; `requirements-spec.md` §4 | Nobody |
| 2 | Literature and Environmental Review | **Have** | §2, six subsections, 19 sources | Nobody |
| 3 | Requirements | **New**, drafted 2026-09-28 | `requirements-spec.md`, 41 functional and 6 non-functional requirements with status | Nobody |
| 4 | System Architecture and Data Flow | **Have** | §4.1 Architecture, §4.2 Data model; `database-spec.md` | Nobody |
| 5 | Security and Privacy | **New** | §2.5 local inference argument; `.env` handling; NFR-1 | **Luke**: what stays local, what reaches Jira |
| 6 | Implementation | **Have** | §4.3, thirteen numbered design decisions; §4.4 development process | Nobody |
| 7 | RAG Method | **New** | JJ's PR #12: `rag-poc.md`, `rag_retrieval.py`, six result files. §4.3.10 on why retrieval uses keyword and hybrid and no embeddings | **JJ**, and the null result must be reported as a ceiling effect, see §5.12.3 |
| 8 | Evaluation | **Have**, extend | §5, twelve subsections, 6,159 words. §5.11 answers the supervisor's question about where the score comes from; §5.12 carries the prompt-label finding, which is the largest single effect the project has measured | **JJ** for the RAG comparison only |
| 9 | Deployment and System Requirements | **New** | `deployment-spec.md`, `Dockerfile`, `docker-compose.yml`, README Option C | **Luke**: clean-environment run and hardware figures |
| 10 | Limitations and Future Work | **Written 2026-09-28**, `section-10-limitations.md`, 1,752 words | Consolidates §5.2, §5.10, §6.5, §7.1–7.3, and adds §10.6 on the decision §5.12 created | Nobody |
| 11 | Discussion and Lessons | **Have** | §6, including seven lessons each attached to its evidence | Nobody |
| 12 | Conclusion | **Have** | §8 | Nobody |
| n/a | References | **Have** | §9 | Nobody |

### What that totals

- Already written and reusable: approximately **10,000 words**
- Reinstated rather than discarded: **4,433 words**
- Genuinely new: approximately **3,600 words** across sections 3, 5, 7 and 9
- Of that, §3 Requirements depended on nobody and is **written: 2,099 words**, leaving about 2,700 across sections 5, 7 and 9, all of which wait on a teammate

## File naming

New sections use a zero-padded number matching this outline: `section-03-requirements.md`. The
existing files keep their old single-digit names until the renumbering pass, which is one
mechanical rename per file and is deliberately left until the structure is agreed rather than
done twice.

| New | Current file |
|---|---|
| 1 | `section-1-introduction.md` plus material from `section-3-problem-analysis.md` |
| 2 | `section-2-literature.md` |
| 3 | `section-03-requirements.md` **(new, written)** |
| 4 | `section-4-design.md` §4.1–4.2 |
| 5 | `section-05-security-privacy.md` *(to write)* |
| 6 | `section-4-design.md` §4.3–4.4 |
| 7 | `section-07-rag-method.md` *(to write)* |
| 8 | `section-5-evaluation.md` |
| 9 | `section-09-deployment.md` *(to write)* |
| 10 | `section-10-limitations.md` **(new, written)**. The passages it consolidates still exist in `section-5-evaluation.md` and `section-7-recommendations.md` and are cut at renumber time, not before |
| 11 | `section-6-discussion.md` |
| 12 | `section-8-conclusion.md` |
| References | `section-9-references.md` |

**Section 4 splits an existing file in two.** `section-4-design.md` currently carries
architecture, the data model, thirteen design decisions and the development process. Sections 4
and 6 of the new structure divide it along a seam that already exists in the file, at the §4.3
heading, so the split is a cut rather than a rewrite.

## Writing order for the week ending 2026-10-02

Ordered by dependency, so that nothing waits on a person who has not delivered yet.

| When | Work | Depends on |
|---|---|---|
| Mon 28 Sep | §3 Requirements | Nobody. **Drafted, 2,099 words** |
| Mon 28 Sep | §8 gains §5.11, where the validation score comes from and what it decides | Nobody. **Written, ~1,480 words**, with `evaluation/score_breakdown.py` and 17 tests behind it |
| Mon 28 Sep | §10 Limitations, consolidated from six places | Nobody. **Written, 1,752 words** |
| Tue 29 Sep | The renumbering pass and the §4/§6 split | The structure being agreed |
| Tue 29 Sep | §1 gains an explicit in-scope and out-of-scope statement | The scope freeze |
| Wed 30 to Thu 1 Oct | §5 Security and Privacy, §9 Deployment | Luke |
| Thu 1 Oct | §7 RAG Method, §8 extended with the comparison | JJ |

## What has not changed

The rubric coverage table in [report-outline.md](report-outline.md) still holds. The two
20-mark criteria, "Solution of project" and "Quality of outcomes", are served by sections 4, 6,
8 and 11 of the new numbering, all of which are already written. **The restructure moves marks
around the document; it does not put any at risk, provided the three reinstated sections stay
in.**

Three requirements from the original outline still apply and are easy to lose in a restructure:
the Industry Supervisor Report is a second deliverable where a supervisor is appointed, APA
applies throughout including to our own figures, and the Subject Outline's generative AI
requirements must be read before writing rather than after.
