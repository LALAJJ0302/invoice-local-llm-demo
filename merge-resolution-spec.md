# Merge resolution spec: `upstream/main` into `neo/approval-screen`

**Written 2026-09-28. Status: approved by Neo, in execution.**

Both open questions are decided and recorded in §"Open questions" below: the sixty rendered-app
tests seed their own session, and Luke's login wording is left exactly as he wrote it.

Assigned to Neo by the team plan of 2026-09-28: *"Resolve the PR #11 conflicts while preserving
authentication migrations 013 and 014"* and *"Renumber the review-note migration to 015."*

## What is actually being merged

`neo/approval-screen` is 29 commits ahead of `upstream/main` and 5 behind. Four files conflict.
Two are mechanical, one is documentation, and one is the real work.

| File | Hunks | Nature |
|---|---|---|
| `storage.py` | 2 | Mechanical. Both sides add columns; neither removes anything |
| `tests/test_migration.py` | 6 | Mechanical. Both sides bind `m013` to a different file |
| `CLAUDE.md` | 2 | Documentation |
| `app.py` | 2 | **The real work.** Luke's auth was wired into a screen this branch deletes** |

## Assumptions, stated rather than assumed

**A1. The team wants Luke's authentication feature, not only his tables.** The plan says
"preserving authentication migrations 013 and 014". Migrations create the `users` table and the
`reviewed_by` column. They do not create a login screen. Preserving only the migrations would
produce a database with a `users` table that nobody can log into. This spec assumes the
intention was the feature.

**A2. Luke's security model is not changed by this merge.** `auth.py` ships
`DEFAULT_PASSWORD = "changeme"` and a hardcoded `DEFAULT_USERS` tuple. Both are worth discussing,
and §5 of the report is the place for it. A merge is not the place to rewrite a teammate's
threat model.

**A3. `reviewer_name` belongs on the History destination.** That is the only surface in the new
screen that shows a decision a person made, so it is the only one with a natural slot for who
made it. One line to move if Neo disagrees.

## The five pieces of Luke's work that must survive

Read off the diff of his `app.py` change, not inferred:

| # | What | Where it goes in the new screen |
|---|---|---|
| 1 | `import auth` | Unchanged |
| 2 | `require_login()`, stops the page until a user signs in | Before the body renders |
| 3 | `require_password_change()`, first-login password reset | Called by `require_login` |
| 4 | `load_data()` gains `LEFT JOIN users` and `reviewer_name` | Our `load_data`, additive |
| 5 | `record_decision` records `reviewed_by` | See below, this one is not additive |

**Piece 5 is the only genuine divergence.** The two branches moved the same logic in opposite
directions:

- `upstream/main` moved it **into** `storage.py` as
  `StorageManager.record_decision(invoice_id, decision, reviewed_by)`, which validates the user
  and refuses an unknown id.
- `neo/approval-screen` kept it **in** `app.py` as a local function writing SQL directly, and
  added the post-approval work: resolving tasks, opening the follow-up task, and dispatching.

Neither is wrong and both are wanted. The resolution is for our `app.py` function to delegate the
invoice update to his storage method and keep our follow-up logic after it. That gains his user
validation without losing our hand-off.

## The risk that is not in any conflict marker

**60 of 588 tests render `app.py` through `AppTest`. `upstream/main` contains zero such tests.**

```
tests/test_app_tabs.py      tests/test_score_block.py
tests/test_line_item_check.py  tests/test_sidebar_views.py
```

Every one calls `AppTest.from_file("app.py").run()` and asserts on what rendered. Once
`require_login()` is in the module and calls `st.stop()`, all sixty see a login form instead of
the screen. Luke's auth has never met a rendered-app test, so this was invisible on his side and
will surface on ours the moment the merge compiles.

This is the decision the merge turns on, and it is in §"Open questions" below rather than
resolved silently.

## Plan

Each step names how it is verified. No step proceeds on a failing check.

**Step 0.** Merge on `neo/approval-screen` directly, commit nothing until the suite is green, and
do not push until it is. A local merge is revertible with `git merge --abort`; a pushed one is
visible to two other people.
→ verify: `git status` clean before starting; PR #11 untouched until step 9.

**Step 1.** `git merge upstream/main`, producing the four conflicts above.
→ verify: exactly four conflicted files, no more.

**Step 2. `storage.py`.** `SCHEMA_VERSION = 15`. Keep every column both sides add: Luke's `users`
table and `invoices.reviewed_by`, ours `invoices.review_note` and `review_note_at`.
→ verify: `storage.SCHEMA_VERSION == 15`, and a fresh `StorageManager` creates all four.

**Step 3. Renumber our migration.** `git mv migrations/013_review_note.py
migrations/015_review_note.py`, then `FROM_VERSION 12 -> 14` and `TARGET_VERSION 13 -> 15`.
Luke's 013 goes 12→13 and his 014 goes 13→14, so ours starting at 14 leaves no gap.
→ verify: a fresh database run through 001 to 015 in order ends at `schema_version = 15` with
every column present.

**Step 4. `tests/test_migration.py`.** Rebind `m013` to Luke's `013_reviewer_login.py`, add `m014`,
and bind `m015` to ours. Every chain that previously ended at our `m013` now runs `m013`, `m014`,
`m015` in that order.
→ verify: `pytest tests/test_migration.py -q` green.

**Step 5. `app.py`.** Take our version as the base and re-apply the five pieces from the table
above. Nothing of the old dashboard body comes back.
→ verify: `pytest tests/ -q` green, and the app renders under `AppTest` with 0 exceptions.

**Step 6. The login gate against the 60 tests.** See open question Q1.
→ verify: all 60 render tests green.

**Step 7. `CLAUDE.md`.** Merge both sides' edits. While the file is open, correct the stale
contributor count: it records Luke as having two commits, and the first-parent history shows
fifteen, of which two were direct pushes and thirteen came through pull requests.
→ verify: read it back; no count that a push can invalidate is stated as a fixed number.

**Step 8. Re-verify the fresh clone.** The "587 passed" figure in the PR description was measured
before Luke's auth existed and is not true of the merged result until re-run. The documented setup
sequence may now need a login step, since `auth.ensure_default_users` runs inside `require_login`.
→ verify: a clean copy with no database, following only the README, reaches a green suite; and
the app opens to a login screen that accepts the default credentials.

**Step 9.** Push, and update PR #11 with what changed and what Luke should review.
→ verify: PR reports `MERGEABLE`.

## Open questions, both needing Neo

**Q1. How do the 60 rendered-app tests get past the login gate?**

- **(a) Tests seed the session. CHOSEN.** `at.session_state["user_id"] = 1` before `.run()`. No production
  code changes at all; each test states its own precondition, which is what a test should do.
  Recommended.
- **(b) An environment variable bypass in `app.py`. Rejected.** Adds a code path to the shipped app whose
  only purpose is to skip authentication. That is a security smell in a file two other people
  read, and it is the kind of speculative configurability worth refusing.

**Q2. What does the login screen say? DECIDED: Luke's wording is left untouched.** It reads
`⚡ Enterprise AI Workflow Automation Dashboard`, which is the name of the screen this branch
removes, so the login page and the page behind it now carry two different product names. That is
accepted deliberately: it is his text, the inconsistency is cosmetic and reversible in one line,
and a merge that silently rewrites a teammate's visible copy is worse than a merge that leaves a
question on the PR. Raised for him in the PR rather than decided for him.

## Out of scope, deliberately

- **Changing `DEFAULT_PASSWORD` or `DEFAULT_USERS`.** Noted for report §5, not touched here.
- **Any change to `auth.py`.** It merges cleanly and needs nothing.
- **Renumbering anything already on `main`.** Luke's 013 and 014 stay exactly as they are.
- **Report files.** `report/` work is not part of this merge and should not enter PR #11.
