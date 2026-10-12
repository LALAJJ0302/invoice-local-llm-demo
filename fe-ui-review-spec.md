# FE spec: fixes from the 2026-10-12 UI review

**Status: built on neo/overview-square 2026-10-12, NOT committed, NOT pushed. F5 dropped by Neo 2026-10-12: phone support is not a goal. Tests: 729 passed, 4 skipped.** Found by screenshotting the running app
(a copy of `neo/overview-square` at 57dcd91, a copy of the database, signed in as neo) at
1440px wide, plus one narrow capture. Screens seen: Overview, Awaiting approval, Approved by the
system, Documents, Vendors, Pipeline runs, History (empty), the login screen, and the review dialog
(opened by clicking Review document).

**Still not covered:** keyboard navigation, measured colour contrast (judged by eye only), the
history screen with real decisions in it (the copy database has none), error states (wrong
password, a failed save), and any real phone.

## 1. What was found, and what turned out not to be a defect

| # | Finding | Verdict after checking the code and the earlier specs |
|---|---|---|
| F1 | Apex reads `0.85 Validated` beside "Worth a careful look". Harbour reads `0.87 Needs review`. A higher score has the worse label. | **Already a recorded design choice**, not a bug. `fe-score-spec.md` says the gate has hard rules beyond the threshold, so a document can score above 0.80 and still fail. The residual gap is smaller: a score above the gate line sitting next to "Needs review" has no sentence connecting the two. |
| F2 | Documents page says the same sentence twice, and the second copy ends "which is not the same as them being worth hiding". | **Defect.** `app.py:2661` (subtitle) and `app.py:2730` (`tab-note`) both open with "Every document the pipeline has stored." |
| F3 | Documents rows show `0.85` with no label and no gate. | **Probable breach** of condition 1 and 2 in `fe-score-spec.md` (always labelled `Validation score`, always against 0.80). To confirm: whether `tests/test_score_block.py` covers this surface. |
| F4 | Documents rows have no column headers, and status is a coloured dot only. | **Defect** for colour-only meaning (WCAG 1.4.1). Headers are a usability gap. |
| F5 | At narrow width the sidebar opens over the content from first load. | **Dropped.** Phone support is not a goal. |
| F7 | Documents, Vendors and Pipeline runs each show a one-line subtitle and then a grey note that repeats it. The notes read as developer commentary: "the arithmetic this project watches the model for", "which is not the same as them being worth hiding". | **Defect, systemic.** One mechanism (`tab-note`) produces all three. Fix it once. |
| F8 | Cards and dialog on **Approved by the system** say "waiting 35d" for documents the system already approved. | **Defect.** `app.py:1527` and `app.py:1672` add "waiting Nd" whenever a date exists, whatever the state. |
| F9 | Dialog, panel "What the model stored": the Vendor row reads "read from the document" instead of the vendor name. | **Label mismatch.** `app.py:1421` shows the source of the value, not the value. Total does it properly (value plus a `model` chip). |
| F10 | Dialog contradiction panel is headed "The document shows 3". | **Copy gap.** The noun is missing: 3 line items the model did not store. |
| F11 | Dialog footer tells a reviewer "nothing is sent ... until `JIRA_ENABLED` is set". | **Config variable in end-user copy.** Honest and useful for the demo, wrong for a reviewer. |
| F12 | Approve and Reject sit at the very bottom of a dialog about 1500px tall. On a laptop screen they are below the fold. | **Known limit, not new.** The code records that `position:sticky` does not work inside `st.dialog`. Listed so it is a decision, not a surprise. |
| F6 | "more than one currency" under Waiting for you. | **Not a bug.** Intended: pending items cannot be totalled across currencies. The wording is vague. |

## 2. Proposed changes

1. **F2.** Delete the duplicated opening in the `tab-note`; keep one plain sentence about what
   the approval card leaves out and why. Copy only.
2. **F3.** Label the score `Validation score` on Documents rows, with the 0.80 gate beside it,
   following the same three conditions. If a row is too tight, use the short form the dialog
   already uses. The explanatory sentence stays in `review_signals.py`, not `app.py`.
3. **F4.** Add a header row to Documents. Put the words `Validated` / `Needs review` next to
   the dot so colour is never the only signal.
4. **F1.** When the score is at or above the gate but the verdict is `NeedsReview`, add one
   clause to the existing card note: a check other than the score failed. Text lives in
   `review_signals.py`.
5. **F6.** Reword to "2 currencies, no single total".
6. **F7.** Remove the repeated note on Documents, Vendors and Pipeline runs, and rewrite what
   is left in plain words a reviewer would use. Copy only.
7. **F8.** Show "waiting Nd" only for documents that are actually waiting. Approved documents
   show nothing, or the date they were approved.
8. **F9.** Show the vendor name with a source chip, as Total does.
9. **F10.** Head the panel "The document shows 3 line items the model did not store".
10. **F11 (changed by Neo, 2026-10-12).** Delete the Jira sentence from the dialog footer
    (`app.py:1512-1514`). The footer holds Reject and Approve and nothing else. The same fact
    stays visible elsewhere: the Overview footnotes (`app.py:2082`, `app.py:2644`) and the
    Outbox note (`app.py:2263`). The tests that pin the sentence in
    `tests/test_app_jira_preview.py` are changed to assert its absence from the footer.
11. **F12 (decided by Neo, 2026-10-12).** Leave Approve and Reject at the bottom of the dialog.
    No change.
12. **F5 (dropped by Neo, 2026-10-12).** Phone support is not a goal, so the sidebar stays
    expanded. No change.

## 3. Out of scope

No change to the gate, the score weights, the schema, or `ConfidenceValidator`. No trend,
delta or sparkline on the score (rejected in `fe-score-spec.md`). No new pages. Bulk approve,
inline PDF view and CSV export are product ideas, listed in section 5, not part of this spec.

## 4. Verification

- Tests pin each changed string and the new header row, in the style of `tests/test_score_block.py`.
- Full suite stays green (720 passed, 4 skipped before the change, 2026-10-12).
- Re-screenshot Overview, Awaiting approval and Documents at 1440px and compare against the
  captures in this review. A narrow capture only if item 6 is built.

## 5. Ideas for later, not decided

Filter and search on the Documents page. CSV export for accounts. (An earlier draft also
listed the document beside the extracted fields. The dialog already does that, with the text
rather than the PDF, so it is not an idea.) A visible reason on Overview when Push to Jira is disabled.

## 6. Questions for the group

- Build on PR #23, or a new branch off `neo/overview-square` so #23 does not grow?
- The unseen screens have now been reviewed. What remains is keyboard use and real contrast
  numbers, which need a tool rather than a screenshot.
