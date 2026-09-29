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
| 1 | Introduction and PoC Scope | **Have.** Pivot narrative merged in 2026-09-29 as §1.5 and §1.6. Still needs a citation for §1.1 | §1.1–1.6 | Nobody, except §1.1 which needs outside literature |
| 2 | Literature and Environmental Review | **Have** | §2, six subsections, 19 sources | Nobody |
| 3 | Requirements | **New**, drafted 2026-09-28 | `requirements-spec.md`, 41 functional and 6 non-functional requirements with status | Nobody |
| 4 | System Architecture and Data Flow | **Have** | §4.1 Architecture, §4.2 Data model; `database-spec.md` | Nobody |
| 5 | Security and Privacy | **Written 2026-09-29**, 1,192 words | Read off the code: two outbound calls, the eight fields Jira receives, `auth.py`, and what is not protected | Nobody. Luke to confirm the Jira field list |
| 6 | Implementation | **Have** | §6.1, thirteen numbered design decisions; §6.2 development process | Nobody |
| 7 | RAG Method | **Written 2026-09-29**, 1,150 words | JJ's PR #12 read at `543fe12`: `rag-poc.md`, `results_rag_summary.json`. Reports the null result as a ceiling effect per §8.12.3 | Nobody. Numbers must be re-read if #12 changes before it merges |
| 8 | Evaluation | **Have** | Twelve subsections, 6,287 words. §8.11 answers the supervisor's question about where the score comes from; §8.12 carries the prompt-label finding, which is the largest single effect the project has measured. The RAG comparison lives in §7, not here | Nobody |
| 9 | Deployment and System Requirements | **Written 2026-09-29**, 1,103 words | `deployment-spec.md`, `Dockerfile`, `docker-compose.yml`, plus hardware and footprint measured on this machine | Nobody. Route C has never reproduced the report's numbers, and §9.5 says so |
| 10 | Limitations and Future Work | **Written 2026-09-28**, 1,752 words | Absorbed the old §7.1–7.3 and §5.10, and adds §10.6 on the decision §8.12 created | Nobody |
| 11 | Discussion and Lessons | **Have** | Seven lessons each attached to its evidence, plus §11.6 on what transfers past invoices | Nobody |
| 12 | Conclusion | **Have** | 998 words | Nobody |
| n/a | References | **Have** | 841 words, APA | Nobody |

### What that totals

**All twelve sections and the references are written.** The built document is 24,029 words
across 14 sources, with no gap in its numbering. Sections 3, 5, 7, 9 and 10 were written
between 28 and 29 September 2026 and account for 7,447 of those words.

## File naming

Every section file uses a zero-padded number matching this outline:
`section-03-requirements.md`. References is `section-99-references.md` so that a plain
lexical sort puts it last.

| New | File | State |
|---|---|---|
| 1 | `section-01-introduction.md` | Written. Gained §1.5 and §1.6, the pivot narrative, from the dissolved problem-analysis file |
| 2 | `section-02-literature.md` | Written |
| 3 | `section-03-requirements.md` | Written. §3.3 gained the three workflow roles |
| 4 | `section-04-architecture.md` | Written. The §4.1–4.2 half of the old design file |
| 5 | `section-05-security-privacy.md` | Written |
| 6 | `section-06-implementation.md` | Written. The §4.3–4.4 half, renumbered to §6.1 and §6.2 |
| 7 | `section-07-rag-method.md` | Written |
| 8 | `section-08-evaluation.md` | Written. Gained §8.3.1, the first-version defects. §8.10 is now a pointer into §10.3 |
| 9 | `section-09-deployment.md` | Written |
| 10 | `section-10-limitations.md` | Written |
| 11 | `section-11-discussion.md` | Written. Gained §11.6 from the dissolved recommendations file |
| 12 | `section-12-conclusion.md` | Written |
| References | `section-99-references.md` | Written. Numbered 99 so it sorts last |

**Applied 2026-09-29.** The renumbering pass ran: eight renames, the §4 split, 91 headings
renumbered, 57 cross-references repointed and one stale reference corrected. Sections 5, 7 and
9 were then written, so the numbering is contiguous and `report/build-docx.sh` lists all
fourteen sources with no commented gaps. `scripts/check_report_refs.py` reports zero dangling
references and zero pending.

Two files no longer exist. `section-3-problem-analysis.md` went to §1.5, §1.6, §8.3.1 and §3.3;
`section-7-recommendations.md` went to §10.4, §10.5, §10.7 and §11.6. Nothing in either was
dropped. See `report-renumbering-spec.md`.

**Section 4 split an existing file in two.** `section-4-design.md` carried architecture, the
data model, thirteen design decisions and the development process. Sections 4 and 6 divide it
along a seam that already existed in the file, at the old §4.3 heading, so the split was a cut
rather than a rewrite.

## Writing order for the week ending 2026-10-02

Ordered by dependency, so that nothing waits on a person who has not delivered yet.

| When | Work | Depends on |
|---|---|---|
| Mon 28 Sep | §3 Requirements | Nobody. **Drafted, 2,099 words** |
| Mon 28 Sep | §8 gains §8.11, where the validation score comes from and what it decides | Nobody. **Written, ~1,480 words**, with `evaluation/score_breakdown.py` and 17 tests behind it |
| Mon 28 Sep | §10 Limitations, consolidated from six places | Nobody. **Written, 1,752 words** |
| Tue 29 Sep | The renumbering pass and the §4/§6 split | **Done.** 57 references repointed, checker at zero dangling |
| Mon 28 Sep | §1 gains an explicit in-scope and out-of-scope statement | Nobody. **Written as §1.4, 660 words** |
| Mon 29 Sep | §5 Security and Privacy | **Written from the code**, not from Luke. Two outbound calls found by searching every source file |
| Mon 29 Sep | §9 Deployment | **Written**, with hardware and footprint measured rather than requested |
| Mon 29 Sep | §7 RAG Method | **Written** from PR #12 at `543fe12`, reported as a ceiling effect |
| Outstanding | Luke to confirm §5.2's Jira field list; JJ to confirm §7 if #12 changes | Both are confirmations of written text, not blockers |

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

---

## Figures held because the state they show is about to disappear

`report/screenshots/approval-screen-before-prompt-fix-2026-09-28.png` is the approval screen as
it stood on 28 September, and it is the only record of a state that the prompt fix removes.

It shows the two pending documents side by side with their score meters:

```
Harbour Review Supplies   AUD   990.00   0.87  Needs review   (amber rail)
Apex Cloud Solutions Pty  USD 1,500.00   0.85  Validated      (green rail)
```

That pair is the worked example in §8.11.4, and the argument turns on it: the higher score is
refused and the lower one is approved, because three of the gate's four conditions have nothing
to do with the score. **After the prompt fix, Apex scores 1.00 and the inversion is gone.**

Kept deliberately, and it does not settle the open question of what §8.11.4 should say. A
screenshot is evidence that a state existed on a date; it is not a licence to describe that state
in the present tense once `main` no longer produces it. Whatever the group decides, this file is
what the figure caption must be dated against.
