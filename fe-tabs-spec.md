# Spec: the tab bar reaches its final shape

**Covers FE-13, FE-14 and four new items: FE-16 to FE-19.**
Status: proposed, awaiting Neo's approval. Nothing in `app.py` has been changed.

This is the change that settles what the screen contains. Groups A and B settled how it looks.

---

## 1. What the tab bar becomes

```
Overview │ Awaiting approval 0 │ Approved by the system 2 │ Outbox 18 │ History 1
   ▲
   default
```

Five tabs where `fe-screen-spec.md` §2 specifies three, and a different default. **That section
is amended by this change, not contradicted by it.** Its argument for three tabs was that the
counts belong on the labels so no number needs a click, and that argument survives: the two new
tabs carry counts on their labels like the others.

Its argument for `Awaiting approval` as the default does not survive, and the reason is in the
database. As of tonight all three documents are approved and the queue holds nothing. That is
the normal state of this queue, not an accident: `fe-screen-spec.md` §8 already says "the real
queue holds one document". Opening onto an empty queue tells an approver nothing. Opening onto
Overview tells them what happened while they were away, and the queue is one click from there
when it has something in it.

---

## 2. FE-16, Notifications becomes Outbox

The tab is named for what the table is. `outbound_messages` carries `state`, `sent_at`,
`external_ref` and `error`: it is a transactional outbox, and "Outbox" is a word people already
understand to mean written but not delivered.

**Not "Push to Jira".** Two reasons, and the second is the one that matters. Fifteen of the
eighteen rows are Teams messages that have nothing to do with Jira. And nothing has been pushed
anywhere: every row is `Pending` and `sent_at` is null on all of them. A tab named for an action
the system has never performed advertises a capability `requirements-spec.md` FR-6.3 records as
deliberately absent.

---

## 3. FE-19, the Push to Jira button

**Almost no new code.** `task_dispatch.dispatch_task_to_jira` already reuses an existing
`Pending` or `Failed` outbox row for the same task rather than creating a second one, which
means the retry path was designed in from the start. The button calls it.

The three outcomes are already implemented in `task_dispatch.py`:

| Outcome | What happens now |
|---|---|
| Jira not configured | Prints `Skipped`, leaves the row `Pending` |
| Jira rejects it | `mark_outbound_failed(outbox_id, error)`, row goes `Failed` and keeps the message |
| Jira accepts it | `mark_outbound_sent(outbox_id, issue_key)`, row goes `Sent` and keeps the key |

**The button appears only on Jira rows**, because Teams rows have no transport and never will.
It is **disabled when `JiraClient.is_configured()` is false, with the reason on screen**:
`Jira is not configured. Set JIRA_ENABLED in .env`. A button that looks live and silently does
nothing is worse than one that explains itself.

**This is the first control in the project that reaches outside the machine.** Pressing it on a
configured Jira creates a real issue in a real project. That is worth saying out loud before it
is built, and it is the reason the disabled state is part of the spec rather than a detail.

Three rows are pushable today.

---

## 4. The Teams rows, and a work item that disappeared

The plan was to stop writing new Teams rows. **Nothing writes them already.**

`queue_outbound` is called from exactly one place in the codebase, `task_dispatch.py`, and it
passes `Jira`. The evidence in the data agrees:

```
newest Teams row   2026-09-08 04:53:34
newest Jira row    2026-09-19 12:32:07
pipeline last ran  2026-09-19 04:13:29
```

The pipeline has run since and produced no Teams row. So the fifteen are a frozen historical
record, and they stay: they are the evidence that FR-6.2 was implemented while FR-6.3 was not,
which is a point the report makes. They are shown as sent-nowhere rows with no action, and the
Outbox says so in one line rather than leaving a reader to wonder why fifteen rows have no
button.

---

## 5. FE-17, History

Documents a person decided on. The column that separates them already exists and nothing new
is needed:

```sql
SELECT * FROM invoices WHERE reviewed_at IS NOT NULL
```

`record_decision()` writes `reviewed_at` on every human decision, and the auto-approval path
never writes it. That is why `Approved by the system` can be defined as
`approval_status = 'Approved' AND reviewed_at IS NULL` today.

**History is the only place a rejection is visible.** Rejecting a document today removes it from
every tab on the screen: it is not `Pending`, so it leaves the queue, and it is not `Approved`,
so it never reaches the auto tab. The decision is recorded and then invisible. That is the
strongest argument for this tab and it is worth more than the summary it also provides.

Each row shows what was decided, when, the document, and the resulting task state: `Done` for an
approval, `Cancelled` for a rejection, from `tasks.resolved_at` and `tasks.state`.

One row qualifies today.

---

## 6. FE-18, Overview

Four sections, one per tab, each summarising it and linking into it. Every number below is a
query result and none is computed by adding across currencies.

| Section | Shows | Source |
|---|---|---|
| Awaiting approval | count, and the oldest document's vendor, amount and wait. Its empty state when the queue is empty | `approval_status = 'Pending'` |
| Approved by the system | count, and when the last one was cleared | `approval_status = 'Approved' AND reviewed_at IS NULL` |
| Outbox | pushable count, frozen count, and sent count, which is currently zero and should be shown as zero rather than hidden | `outbound_messages` grouped by channel and state |
| History | count decided by a person, split into approved and rejected, and the last decision | `reviewed_at IS NOT NULL` |

**The amount rule from FE-5 applies here too.** A section shows a summed amount only while every
document in it shares a currency, and the amount carries that currency's code. Otherwise the
count stands alone. This is the rule that already governs the `Awaiting approval` tab label.

Built from the C3 summary tile in `approval-screen-components.html`, which was drawn for the
queue screen, rejected there as duplication of the tab labels, and belongs here instead. Nothing
new has to be designed.

---

## 7. What this change does not do

No dialog design, that is group C and still blocked on a design round. No sidebar, that is group
E. No change to `record_decision`, the validation gate, or any query the pipeline runs.

---

## 8. Verification

| id | Verified by |
|---|---|
| FE-16 | No occurrence of the word Notifications in the tab bar; `Outbox` carries the row count |
| FE-19 | With Jira unconfigured the button is disabled and states why, asserted in `AppTest` rather than looked at. With a stub client that raises, the row moves to `Failed` and keeps the error. With a stub that succeeds, the row moves to `Sent` and keeps the key |
| FE-17 | A fixture with one approved and one rejected document shows both, with the correct task state against each |
| FE-18 | Every number on Overview equals the count on the corresponding tab label, asserted rather than compared by eye |
| all | `pytest tests/ -q` stays green, and `AppTest` reports 0 exceptions on each tab |

---

## 9. Risks

- **The push button writes to a system outside this project.** It is the only thing in the
  codebase that does. It should not be wired to a production Jira for a demo.
- **Five tabs.** The stated goal was a screen that does not overload. Five labelled tabs with
  counts is still a tab bar, but a sixth would need an argument.
- **Overview duplicates by design.** Every number on it appears somewhere else. That is what an
  overview is, and it is the same duplication `fe-screen-spec.md` §2 rejected for the queue
  screen. The difference is that the duplication is the tab's whole purpose rather than a strip
  competing with the tab labels above it. If that argument does not hold, this tab should not
  exist.
