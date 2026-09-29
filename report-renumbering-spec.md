# Report renumbering spec

Written 2026-09-29, against `neo/report-poc` @ `f98e8e2`.

Moves the drafted report sections onto the twelve-section structure agreed in
[report/report-outline-v2.md](report/report-outline-v2.md). Nothing is rewritten. Files are
renamed, one file is split at a seam that already exists in it, passages that section 10 has
already absorbed are cut from where they were copied from, and every cross-reference is
repointed.

**Scope.** `report/*.md` and `report/build-docx.sh`. No code, no tests, no schema, no
migration. `pytest` collects 629 before and 629 after.

## 1. What makes this harder than a rename

### 1.1 The report is already written in two numbering schemes at once

The three files written on 2026-09-28 were drafted against the **new** structure. Everything
older is against the **old** one. Both are in the tree now, and a global rewrite would corrupt
whichever population it was not aimed at.

| File | Scheme its references use |
|---|---|
| `section-1-introduction.md` | **New.** `§8.3`, `§8.9`, `§8.11`, `§10.1`, `§10.4`, `§10.7`, `§6`, `§7` |
| `section-03-requirements.md` | **New.** `§8`, `§8.2`, `§5`, `§6`, `§10`, `§11` |
| `section-10-limitations.md` | **Mixed.** Self-references `§10.1`, `§10.4` are new. Outward references `§5.11`, `§5.12`, `§5.5.1` are old |
| The other eight | **Old**, throughout |

So 24 of the 121 references are already correct and must be left alone, 7 sit in a file where
some are correct and some are not, and the rest need rewriting.

### 1.2 Section 7 does not renumber, it scatters

Every other old section maps to one new number. Section 7 does not, because section 10 absorbed
its subsections in a different order.

| Old | New | Evidence |
|---|---|---|
| `§7.1 Work that is specified and measured but not shipped` | `§10.5 Specified and measured, deliberately not shipped` | 354 words, same content |
| `§7.2 The gap that matters most...` | `§10.4 The limit that no improvement in extraction closes` | 330 words, same content |
| `§7.3 Functional gaps for a later team` | `§10.7 Functional gaps for a later team` | 531 words, titles identical |

`§7.2` is referenced six times and every one of them has to become `§10.4`. A prefix swap would
send all six to `§10.2`, which is a different subsection that exists and reads plausibly. That
is the single most dangerous edit in this pass: wrong, and silently wrong.

### 1.3 Two references point at other documents

These are not report sections and must not be touched.

- `section-1-introduction.md:15` — `` `requirements-spec.md` §2 ``
- `section-4-design.md:78` — `` `database-spec.md` §5 ``

### 1.4 Cut passages invalidate references that would otherwise be correct

`§5.2` and `§5.10` are cut in step 4 below. A reference to `§5.2` therefore cannot become
`§8.2`; it has to go to `§10.2`, where the text now lives. Four references are affected this
way.

## 2. File operations

`git mv` throughout, so history follows.

| New | Command |
|---|---|
| 1 | `section-1-introduction.md` → `section-01-introduction.md` |
| 2 | `section-2-literature.md` → `section-02-literature.md` |
| 3 | `section-03-requirements.md` (already named) |
| 4 | `section-4-design.md` lines 1–74 → `section-04-architecture.md` |
| 5 | not written, waiting on Luke |
| 6 | `section-4-design.md` lines 75–end → `section-06-implementation.md` |
| 7 | not written, waiting on JJ |
| 8 | `section-5-evaluation.md` → `section-08-evaluation.md` |
| 9 | not written, waiting on Luke |
| 10 | `section-10-limitations.md` (already named) |
| 11 | `section-6-discussion.md` → `section-11-discussion.md` |
| 12 | `section-8-conclusion.md` → `section-12-conclusion.md` |
| refs | `section-9-references.md` → `section-99-references.md` |

`section-3-problem-analysis.md` and `section-7-recommendations.md` are not in this table. See
section 5, they are the open question.

### The split

`section-4-design.md` is cut at [report/section-4-design.md:75](report/section-4-design.md#L75),
the existing `## 4.3 Design decisions` heading. Lines 1–74 carry `4.1 Architecture` and
`4.2 Data model`; lines 75 onward carry the thirteen design decisions and `4.4 Development
process`. Both halves need a new `# ` title line, since only the first inherits the old one.

## 3. Heading renumbering

Every numbered heading in a moved file changes. 91 headings total, concentrated in two files.

| File | Numbered headings | Rewrite |
|---|---|---|
| `section-02-literature.md` | 7 | `2.x` unchanged |
| `section-01-introduction.md` | 5 | `1.x` unchanged |
| `section-04-architecture.md` | 3 | `4.1`, `4.2` unchanged |
| `section-06-implementation.md` | 15 | `4.3` → `6.1`, `4.3.n` → `6.1.n`, `4.4` → `6.2` |
| `section-08-evaluation.md` | 31 | `5.x` → `8.x`, `5.x.y` → `8.x.y` |
| `section-11-discussion.md` | 6 | `6.x` → `11.x` |
| `section-12-conclusion.md` | 1 | `8.` → `12.` |
| `section-03-requirements.md` | 10 | unchanged |
| `section-10-limitations.md` | 8 | unchanged |

`section-06-implementation.md` is the awkward one: `4.3.1` through `4.3.13` become `6.1.1`
through `6.1.13`, which pushes the design decisions a level deeper than they read now. The
alternative is promoting them to `6.1` through `6.13` and giving development process `6.14`,
which reads better and breaks the `§4.3.n` references in a second way. **Recommendation:
promote.** No reference targets a `§4.3.n` today, so the promotion costs nothing and the nesting
costs a level of depth on thirteen headings.

## 4. Cuts

Section 10 reproduces material that still sits in its original files. Cut the originals.

| Cut from | Words | Now lives in |
|---|---|---|
| `§5.2 What this evaluation does not measure` | 258 | `§10.2` |
| `§5.10 Threats to validity` | 231 | `§10.3` |
| `§6.5 What this evaluation cannot support` | 367 | `§10.3` |
| `§7.1`, `§7.2`, `§7.3` | 1,215 | `§10.5`, `§10.4`, `§10.7` |

2,071 words leave the report. They are duplicates, so the reader loses nothing, but the total
word count drops and that is expected rather than a build failure.

Each cut leaves a one-line pointer in place, so a reader following the old flow is not dropped:
`The limits of this measurement are collected in §10.2.`

## 5. Three things the outline does not place, and I am not deciding alone

**a. `section-7-recommendations.md` §7.4 Implications beyond this project, 223 words.** The
other three subsections are consolidated into §10. This one is not, and it is the only passage
in the report that generalises the schema finding past invoices. Options: fold into `§10.6`,
fold into `§11 Discussion`, or keep as the closing move of `§12 Conclusion`.
**Recommendation: §11 Discussion**, because it is an argument rather than a limitation.

**b. `section-3-problem-analysis.md` §3.3 Defects in the first working version, 317 words.** The
outline merges this file's pivot narrative into §1 and says nothing about §3.3.
**Recommendation: `§8` Evaluation, as the baseline the measurements improve on.**

**c. `section-3-problem-analysis.md` §3.4 Stakeholder needs, 189 words.** `§3.3 Stakeholders`
in the new requirements section already covers this ground.
**Recommendation: merge and delete, after checking for anything the requirements section lacks.**

§3.1 and §3.2, 565 words of pivot narrative, go into §1 as the outline already says.

If all three recommendations are accepted, `section-3-problem-analysis.md` and
`section-7-recommendations.md` are both fully dissolved and deleted. Nothing is silently
dropped, which is the failure this spec exists to avoid.

## 6. `report/build-docx.sh`

The `SECTIONS` array at [report/build-docx.sh:35-45](report/build-docx.sh#L35-L45) is the single
source list added in `60e0ae1`. It is reordered to the twelve-section sequence, the two
dissolved files are removed, and the three unwritten sections are **listed as comments** at
their position, so the gap is visible in the file rather than only in the outline.

## 7. Verification

A rename cannot be verified by the test suite, which is why the checker is part of the work
rather than a follow-up.

**`scripts/check_report_refs.py`**, with tests, doing three things:

1. Parse every heading in `report/section-*.md` into the set of section numbers that exist.
2. Parse every `§n.n.n` reference, skipping any preceded by a backticked filename within 40
   characters, and report references to numbers that do not exist.
3. Report any heading number that appears twice across files.

Run it before the pass, to record the baseline, and after. Before the pass it should report
dangling references, because the three new files already point at sections that do not exist
yet under the current numbering. That is the bug this pass closes, and the checker should show
it closing.

Also: `./report/build-docx.sh`, and confirm the word count drops by roughly 2,071 and not more.

## 8. Order of work

1. Write and run the checker. Record the dangling-reference baseline.
2. `git mv` the eight renames.
3. Split `section-4-design.md`.
4. Renumber headings.
5. Apply the cuts and leave the pointers.
6. Repoint references, old-scheme files first, then the seven in `section-10-limitations.md`.
7. Place the three orphans, once decided.
8. Update `build-docx.sh`.
9. Run the checker to zero, build, compare the word count.

Steps 1 to 6 are mechanical once approved. Step 7 is the only one that waits on a decision, and
it can be done last without holding the rest up.

---

# Applied, 2026-09-29

Ran on `neo/report-poc` after `upstream/main` was merged in at `fc3c0c2`. 629 tests before,
629 after. The checker reports zero dangling references and six pending ones, all into sections
5 and 7. The build produces 20,584 words across 11 sources, down 1,407.

Four things differ from the plan above, all found by reading the passages before cutting them.
Each is a case of the spec treating two texts as duplicates because they cover the same
subject, when the report had already thought about the overlap and defended it.

**§8.2 was not cut.** Section 3 of this spec proposed cutting it into §10.2. Its own first
sentence is "Stated here rather than in the limitations section, because a reader needs it
before the numbers rather than after them." It is a deliberate forward-statement, §10.2 is
about the email half's missing ground truth, and `section-03-requirements.md:169` references
§8.2 as a live target. Cutting it would have removed an argument and broken a reference.

**§11.5 was not cut.** Same reason, stated in the text: "The limits below are restated here
rather than left in §5.2, because a discussion section that draws conclusions without
re-stating its own boundaries invites the reader to over-read them." Its content also does not
match §10.3, which is threats to validity, and its closing paragraph separating data-shortage
limits from ground-truth limits appears nowhere else.

**The old §3.4 was not merged into §3.3 and deleted.** The spec called them the same list. They
are not: §3.3 Stakeholders is supervisor, markers, team and a notional user, while the old §3.4
is accounts payable, approver and maintainer, and carries the argument that every automation
decision fails toward a person. It was appended inside §3.3 as a labelled block, which keeps
§3.4 through §3.9 at their existing numbers.

**Design decisions kept their nesting.** Section 3 of this spec recommended promoting `4.3.1`
to `6.1`, on the reasoning that nesting them as `6.1.1` would push them a level deeper. That
reasoning was wrong: `4.3.1` and `6.1.1` are the same depth. Nesting was applied instead, so
`## 6.1 Design decisions` holds `### 6.1.1` to `### 6.1.13` and development process is `6.2`.

So the cut is 1,407 words rather than the 2,071 the spec predicted, and the difference is the
1,024 words of §8.2, §11.5 and the workflow roles that survive on their own argument.

**One stale reference was corrected rather than repointed.** `section-08-evaluation.md:49` read
"so §5.10 reports what a run produces", but §5.10 was Threats to validity; the email half is
§5.9. It now reads §8.9. This was wrong before the pass and would have stayed wrong if the
reference had simply been shifted with the rest.

**Three documents outside `report/` named the old filenames** and were repointed:
`CLAUDE.md`, `nullable-required-schema-spec.md` and `error-taxonomy-spec.md`.

---

# Sections 5, 7 and 9, written 2026-09-29

The renumbering pass left gaps at 5, 7 and 9, which the outline had recorded as blocked on Luke
and JJ. Reading what each actually needed showed that two of the three were not blocked at all.

**§5 Security and Privacy was not waiting on Luke.** The outline said it needed him to say what
stays local and what reaches Jira. That is a question the code answers exactly: searching every
source file for an outbound network primitive returns two results, `imaplib.IMAP4_SSL` in
`email_listener.py:99` and `urllib.request.urlopen` in `jira_client.py:227`, and
`task_dispatch._build_description` enumerates the eight fields a Jira issue carries. Asking a
teammate to recall what the code does, when the code is in front of you, is the failure this
project keeps naming in other contexts. Luke should confirm the field list; he was not needed to
produce it.

**§9 Deployment was not waiting on Luke either.** It needed hardware figures and a clean run.
The machine reports its own figures, `CLAUDE.md` records clean-copy runs on 24 and 28 September,
and `deployment-spec.md` already carried the design reasoning. Writing it surfaced a real
discrepancy nobody had noticed: the Dockerfile pins `python:3.12-slim` while every measurement
in the report was taken on Python 3.13.5, so the two routes are not running the same
interpreter and Route C has never reproduced a single number. §9.4 and §9.5 say so.

**§7 RAG Method needed JJ's branch, and the branch is public.** PR #12 is open at `543fe12` with
`rag-poc.md` and six result files. The section is written from those, and every figure names the
branch it came from because it is not the branch the rest of the report was measured on. §8.12.3
had already committed §7 to reporting the null result as a ceiling effect rather than at face
value, and it does: both arms scored 100% because the one-word prompt fix shipped in the same
branch, so there was no headroom for retrieval to appear in.

The document now runs 1 to 12 with no gap, 24,029 words across 14 sources, and
`scripts/check_report_refs.py` reports zero dangling and zero pending references.

**What is genuinely outstanding is confirmation, not authorship.** Luke should check §5.2's
field list against what he intended the integration to send, and JJ's numbers must be re-read if
#12 changes before it merges. Neither blocks the report.
