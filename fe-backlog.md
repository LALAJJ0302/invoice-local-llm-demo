# FE backlog: the approval screen

**Phase 2 of the project pipeline, front end slice. Written 2026-09-19.**

`fe-screen-spec.md` is the source of truth for what the screen contains and why.
`approval-screen-components.html` is the source of truth for how it looks.
This file is the ordered list of work between those two and a running application.

---

## What this backlog is not

It is not a list of screens to build. **The screens are built.** `app.py` renders three tabs, a
review dialog, the action-item checkboxes, the notification table and the document text panel,
and Approve and Reject are wired end to end: [app.py:361-365](app.py#L361-L365) calls
`record_decision()`, which resolves the open tasks and dispatches to Jira.

What is missing is the design layer on top of working software, plus the gaps that fell out of
checking the design against the database. That distinction sets the order below: nothing in
group B onward can be judged until group A puts the tokens on screen.

---

## Where this stands, 2026-09-19

**Groups A and B are done.** Two commits: the theme layer, then the cards. FE-5 is closed
without being built, for a reason recorded in its row below. The queue tab now renders document
cards with the risk strip, the empty state and working filter and sort controls.

One defect surfaced while verifying group B and was fixed in the same change. The
`Awaiting approval` tab label read `{count} · {sum}` unconditionally. With one document pending
that was correct and invisible. With three it showed `3 · 6,500`, which is USD 1,500 plus USD
2,350 plus AUD 2,650 added as though they were one unit: the arithmetic this project exists to
catch a model doing, on our own screen. The amount now appears only while every pending document
shares a currency, and it carries that currency's code.

**Groups D and D+ are done too, as of 2026-09-20.** The tab bar reached its final shape:
Overview, Awaiting approval, Approved by the system, Outbox, History. `fe-screen-spec.md` §2 was
amended rather than contradicted, and it now records why the default moved off the queue, why
the outbox is not called Push to Jira, and why History exists.

Two more defects surfaced while verifying, both invisible until the data moved:

- The `Awaiting approval` label summed across currencies. Fixed during group B.
- History showed `task Cancelled` against an approved document. The join matched every resolved
  task for the invoice and grouped, so SQLite returned an arbitrary one: invoice 1 carries a
  Review task cancelled on 2026-08-28 beside the Approve task completed on 2026-09-19. It now
  joins the most recently resolved task.

**Next: group C, the review dialog, and group E, the sidebar.** C is the only group blocked on
outside work, because FE-9 needs a design round. E is not blocked.

---

## Order of work

Groups run in order. Inside a group, items can be done in any order.

**A before B was a decision, not a default.** The alternative was to design the review dialog
first, since that is where the decision actually happens. A wins because the design system has
never touched Streamlit: until one stylesheet reaches a real page and survives Streamlit's own
markup, every artboard after it is a guess. Group A is where that risk is bought down, on the
simplest screen we have.

---

## Group A. The design system reaches Streamlit

Nothing else can be judged until this works.

| id | Work | Done when |
|---|---|---|
| ~~FE-1~~ ✅ | Replace the two-line placeholder stylesheet in [app.py:232-239](app.py#L232-L239) with the real one | All 22 tokens exist as CSS custom properties, IBM Plex Sans and Mono load, and the page still renders with 0 exceptions under `AppTest` |
| ~~FE-2~~ ✅ | Remove Streamlit's red accent | No red anywhere in a screenshot of the running app; the active tab is a 2px `#5B5BD6` underline with a wash-filled count chip, per C2 |
| ~~FE-3~~ ✅ | Primary, secondary and disabled buttons per C6 | Tabbing through the page shows a `0 0 0 3px rgba(91,91,214,.16)` focus ring on every focusable control |
| ~~FE-3b~~ ✅ | Fix the one token that fails accessibility, see below | Every colour pair carrying information measures 4.5:1 or better, verified by the same script that found it |

### FE-3b, stated properly because it is a defect and not a preference

`text-faint` `#A1A1A8` measures **2.57:1** on white. WCAG 2.1 AA requires 4.5:1 for body text.
It is used 15 times in the queue design and 3 times in the component sheet, and most of those
uses carry information: the sidebar counts, `no person involved`, `since 7 Sep`, the decided-row
timestamps, the search placeholder and the consequence line at the foot of the page.

Darkening it to pass AA lands on `#6E6E76`, which is already `text-muted`, so the fix is not a
new colour. **The token is not wrong; its usage is.** Anything that carries information moves to
`text-muted`. `text-faint` survives only where WCAG exempts it, which is disabled controls.

Measured on the same run: `text-disabled` `#C2C2C8` on `#F1F1F3` is **1.57:1** and stays as it
is, because WCAG 2.1 1.4.3 exempts inactive controls by name. Everything else already passes:
`text` 17.0, `text-strong` 10.4, `text-muted` 5.05, `caution-text` 6.67, white on `accent` 5.37,
`accent-text` on `accent-wash` 7.42.

---

## Group B. The Awaiting approval tab

Designed. `approval-screen-design-v2.html` is the target.

| id | Work | Done when |
|---|---|---|
| ~~FE-4~~ ✅ | Queue rows become document cards, per C4 | The one pending document renders as a card, not a table row, and the vendor name does not push the amount out of alignment |
| ~~FE-5~~ ❌ | Three summary tiles, per C3 | **Not built, closed 2026-09-19.** `fe-screen-spec.md` §2 had already ruled out a metric strip by name: the counts live on the tab labels so every number is visible without a click. Two of the three tiles repeated a tab label exactly. The third, oldest wait, is on the card as `waiting 12d`, and with the queue sorted oldest first the answer is the top card. The brief asked for tiles without telling the designer the spec had refused them, which is an error in the brief, not in the design |
| ~~FE-6~~ ✅ | Risk strip, per C5, reading `review_signals.py` | The sentence on screen is byte-identical to the matching entry in `SIGNALS`; changing the copy in that file changes the screen |
| ~~FE-7~~ ✅ | Empty state, per C8 | With `approval_status = 'Pending'` returning nothing, the tab reads as finished rather than broken |
| ~~FE-8~~ ✅ | Filter and sort row | `Oldest first` reorders the queue for real. A control that does nothing is worse than no control |

**Not in this group: the weight rail.** It was designed, then removed. The quantity it encoded
was money summed across USD and AUD, which is the same class of error the validation gate exists
to catch, and with one document waiting it is always full. It is documented in C4 as a rule and
earns its place from two documents up.

---

## Group C. The review dialog

Built in code, never designed. This is where the decision happens, so it is the most important
group and the only one blocked on outside work.

| id | Work | Done when | Blocked on |
|---|---|---|---|
| FE-9 | Design the dialog in the new system | An artboard exists at 1440 covering the six steps in `fe-screen-spec.md` §5 in order | A Claude Design round |
| FE-10 | Apply it to `review_dialog()` | The six steps appear in order: what am I approving, is there a concern, let me look, where did it come from, what happens if I approve, decide | FE-9 |
| FE-11 | Draw the contradiction | A person can see the document text listing three priced line items and the field reading `Line items: none` at the same time, without scrolling between them | FE-9 |
| FE-12 | The consequence line before the decision | The name of the Jira task that approving will create is on screen before the button is pressed. `fe-screen-spec.md` §5 calls this new: today `dispatch_task_to_jira` fires with no warning of any kind | |

---

## Group D. The other two tabs

| id | Work | Done when |
|---|---|---|
| ~~FE-13~~ ✅ | Approved by the system, per C7 | Two dense rows, each carrying its own currency symbol, and no column total drawn beneath them |
| ~~FE-14~~ ✅ | Outbox, was Notifications | The repetition is legible as repetition: three messages fired four times and never sent |

---

## Group D+. Added 2026-09-20, after the tab bar was reviewed

Specified in `fe-tabs-spec.md`. All four are done.

| id | Work | Done when |
|---|---|---|
| ~~FE-16~~ ✅ | `Notifications` becomes `Outbox` | No tab is labelled Notifications. The table is a transactional outbox and the word says so |
| ~~FE-17~~ ✅ | History tab | `reviewed_at IS NOT NULL`. It is the only place a rejection can be seen |
| ~~FE-18~~ ✅ | Overview tab, first and default | A page, not a grid of counters. Three tiles, then one section per tab in the order a person asks about them, each carrying its own rows. Rebuilt 2026-09-20 after the first attempt shipped as four boxes with numbers in them, which answered nothing |
| ~~FE-19~~ ✅ | Push to Jira | On Jira rows only, disabled with the reason on screen while `JIRA_ENABLED` is unset |
| ~~FE-20~~ ➖ | Stop writing Teams rows | **No work needed.** `queue_outbound` is called from one place, `task_dispatch.py`, and it passes `Jira`. The newest Teams row is 2026-09-08 and the pipeline has run since without producing one |

---

## Overview page, prioritised 2026-09-20, finished the same day

**Done: OV-1 to OV-8. Declined: OV-9, OV-10, OV-11.** Spec: `fe-overview-spec.md`. Each declined
item was argued rather than dropped, and the reasoning stays below.

The page is built and the tab bar is final, so the next block of work is finishing one page
before starting another. Ordered by what it costs to leave undone, not by what is easiest.

### P1. The approved card is not fully on screen

These are omissions against a design that was already agreed, which makes them the first thing
to fix. The approved card in `approval-screen-design-v2.html` carries five things in its footer
and the build renders two.

| id | Work | Done when |
|---|---|---|
| ~~OV-1~~ ✅ | `Download the original` on the card | The secondary button sits beside `Review document`, and pressing it downloads the file at `invoices.archive_path` |
| ~~OV-2~~ ✅ | `Covering email` chip | The chip shows the sender, as the approved card does. It is the only thing on the card that says the document arrived by email rather than appearing from nowhere |
| ~~OV-3~~ ✅ | The sentence beside the risk signal | `review_signals.py` gains a second sentence per signal, six in total, beside the six it already owns, with tests. It must not be written into a card template: `tests/test_app_signal_copy.py` exists to stop exactly that |

OV-1 and OV-2 were dropped when the card was built, and neither was a decision. OV-3 was a
recorded decision in `fe-queue-spec.md` §4 and is now due.

### P2. Every section is a dead end

| id | Work | Done when |
|---|---|---|
| ~~OV-4~~ ✅ | Each section header can open its tab | Every section on Overview reaches the tab it summarises in one click |

**Researched rather than guessed.** Plausible puts an expand control at the top right of every
panel and the panel opens to its full view. That is the pattern this needs and the reference
already uses it. Linear, Attio and Ramp do the same job with a heading that is itself a link.

### P3. Three of the four sections have no actions at all

Only Awaiting approval can be acted on. The other three are read-only lists of things a person
might want to do something about.

| id | Work | Done when |
|---|---|---|
| ~~OV-5~~ ✅ | Outbox rows carry `Push to Jira` on Overview, as they do in the tab | Pressing it dispatches, and it is disabled with the reason while `JIRA_ENABLED` is unset |
| ~~OV-6~~ ✅ | Approved by the system rows open the document | A person can look at what the system approved without being asked, which is the risk §7.2 of the report calls the most important open one |
| ~~OV-7~~ ✅ | History rows open the document | A decision can be inspected after the fact |

### P4. Bulk selection, and two requests that need a decision first

| id | Work | Status |
|---|---|---|
| ~~OV-8~~ ✅ | Checkbox selection, and `Push selected to Jira` | **Built, on the Outbox tab and not on Overview.** A checkbox asks a person to do bulk work, and Overview is designed for a glance |
| OV-9 | `Approve selected` | **Declined by Neo, 2026-09-20.** Not built. The argument is below, kept so nobody re-proposes it as an oversight |
| OV-10 | `Delete all` | **Declined by Neo, 2026-09-20.** Not built. No delete operation exists anywhere in this system |

**OV-9.** `fe-screen-spec.md` §5 says the approve and reject buttons exist in the dialog and
nowhere else, and gives the reason: *"If a person can approve from the row, they approve on
exactly the information the machine had, which is the same decision the machine already makes by
itself. The extra click buys the chance to look at the document, the evidence quotes and the
covering email first. A rubber stamp with a human hand on it is not human review."* Objective 4
of the project is keeping a person in the approval path. Bulk approval removes it. The report
argues in §7.2 that auto-approval at 1.00 is already unsafe; a button that approves several
documents without opening any of them is the same thing with a person's name on it.

Buildable in an afternoon. The cost is not the code, it is that the report's argument about
human oversight stops being true of its own screen. **Needs Neo's explicit decision, and if it
is built the reasoning in §5 has to be rewritten rather than quietly contradicted.**

**OV-10.** Nothing in this codebase deletes an invoice. The database is the audit trail the
evaluation and the report are built on, `reprocess.py` re-reads rather than removes, and the
upsert on a content hash exists so that re-running never duplicates. A delete would need a
policy first: what it means, whether it is reversible, and what happens to the tasks and outbox
rows that point at the row being removed.

### P5. Researched and recommended against

| id | Work | Status |
|---|---|---|
| OV-11 | Collapse or hide a section | **Not recommended.** None of the four reference products collapses a content panel. Linear collapses groups in its sidebar, which is navigation. Plausible, Attio and Ramp expand panels instead of hiding them. A page long enough to need hiding should show fewer rows per section, which it already does at five |

---

## Group E. The application shell

**Decided 2026-09-19: trim the sidebar to what exists.**

The returned design drew five destinations, Approvals, Documents, Vendors, Notifications and
Pipeline runs, plus two saved views. Two of those exist. The other three are navigation to
screens nobody has specified, and `requirements-spec.md` is explicit about separating what is
implemented from what is not.

| id | Work | Done when |
|---|---|---|
| FE-15 | Sidebar carries only what resolves | Approvals and Notifications, plus the two saved views, which are both real queries: `Flagged by the model` is `has_signal()` over the pending set, `Cleared this week` is `approval_status = 'Approved' AND reviewed_at IS NULL` |

---

## Group F. Out of scope for V1, with the reason

Carried over from `fe-screen-spec.md` §8. Listed so that nobody re-proposes them as oversights.

- **Bulk approval.** The real queue holds one document.
- **Approving an email.** `email_analysis` has no approval column and the Jira hand-off is keyed
  on `invoice_id` throughout.
- **The task queue and assignee editing.** Deliberately removed. Tasks are dispatched to Jira on
  approval and assigned there; a second place to assign the same task invites disagreement.
- **A done state for email action items.** A re-run deletes and re-inserts the children, which
  would reset a person's finished work.

---

## Non-functional requirements

Numbers, and each one is checked by something that already exists in the repo.

| Requirement | Target | How it is checked |
|---|---|---|
| Accessibility | Every colour pair carrying information at 4.5:1 or better, WCAG 2.1 AA | The contrast script that produced the numbers in FE-3b |
| Rendering | The page raises 0 exceptions on load | `streamlit.testing.v1.AppTest`, already used to verify the old dashboard |
| Signal freshness | The risk sentence is computed from the invoice's current columns on every render, and `app.py` contains none of the six sentences | `tests/test_review_signals.py` and `tests/test_app_signal_copy.py`; run `./.venv/bin/python -m pytest tests/ -q` for the current count rather than quoting one here |
| Viewport | Composed at 1440 wide, and composed holding exactly one document | Screenshot at 1440x960 |
| Fidelity to data | No number on screen that cannot be traced to a column or a stated derivation | The four corrections in `design-handoff` `uploads/v2/03-CORRECTIONS.md` are the precedent |

---

## Edge cases the design must answer

1. **The queue is empty**, which is its state most days. FE-7.
2. **Two currencies are pending at once.** The summary tile must stop showing a summed amount.
   It is drawn today with three USD-only documents and would silently lie on the fourth.
3. **A document carries no signal.** The card renders without the caution strip, C4's second
   variant, and shows a green dot rather than a filled pill.
4. **The vendor name is long.** `Apex Cloud Solutions Pty Ltd` wraps rather than pushing the
   amount out of alignment.
5. **Approve is pressed.** The row leaves the queue, the Jira task is created, and the count in
   the tab label and the sidebar both move. Nothing on screen may still say 1.

---

## What is deliberately not in this file

A PRD rollup. The pipeline generates one by default, and here it would restate
`requirements-spec.md` with a different name. This repo has already collapsed two duplicate
specs into `database-spec.md` once, for the reason that two documents describing the same thing
drift and then argue. If a stakeholder-facing rollup is wanted, it should be a generated view of
`requirements-spec.md`, not a second copy of it.
