# Spec: finishing the Overview page

**Covers OV-1 to OV-8 from `fe-backlog.md`. OV-9, OV-10 and OV-11 are not built.**
Approved by Neo 2026-09-20 from the prioritised backlog. One page finished before another starts.

---

## 1. What is not being built, and why that is recorded here

**OV-9, `Approve selected`.** Raised, argued, and declined by Neo in the same exchange.
`fe-screen-spec.md` §5 puts approve and reject in the dialog and nowhere else, because a person
approving from a list decides on exactly the information the machine had. Objective 4 of this
project is keeping a person in the approval path. The screen keeps it.

**OV-10, `Delete all`.** Declined. Nothing in this codebase deletes an invoice, the database is
the audit trail the evaluation stands on, and a delete would need a policy before it needs code.

**OV-11, collapsing a section.** Researched and declined. None of the four reference products
collapses a content panel. Linear collapses sidebar groups, which is navigation; Plausible,
Attio and Ramp expand panels rather than hide them.

Neo's answer to all three was to build other actions instead, which is P3 and OV-8 below.

---

## 2. The finding that makes OV-4 possible

> **Wrong, corrected 2026-09-20.** This section claimed the measurement below proved a section
> header could open a tab. It proved only that the session value changes. The rendered tab does
> not follow it, with either `key` or `default`, measured in a browser where `aria-selected`
> stayed on Overview. The control was removed. `st.segmented_control` can be driven this way and
> is the fix, as a deliberate change to the page's navigation. See `fe-backlog.md`, OV-4.


`st.tabs` in Streamlit 1.62.0 takes `key`, `default` and `on_change`. Setting the session key to
a tab's label and calling `st.rerun()` switches tabs. Measured, not assumed:

```
initial   st.session_state["nav"] raises KeyError until a tab is chosen
after     the button sets it to "Outbox", rerun, selected=Outbox, 0 exceptions
```

So a section header can open its tab through the supported widget state rather than a workaround.
The key is read nowhere and written in one place, so a missing key cannot break a render.

---

## 3. The work

| id | Change | Note |
|---|---|---|
| OV-1 | `Download the original` on the document card | All three archive files exist on disk, checked. A missing file disables the button and says so, the same rule the Push button follows |
| OV-2 | `Covering email` chip on the card | `email_sender`, already selected by `load_data` |
| OV-3 | A second sentence per risk signal | Lives in `review_signals.py`, not in a card template |
| OV-4 | Every section header opens its tab | Per section 2 |
| OV-5 | Outbox rows on Overview carry `Push to Jira` | Same disabled rule as the tab |
| OV-6 | Approved-by-the-system rows open the document | The screen should let a person look at what was approved without them |
| OV-7 | History rows open the document | A decision can be inspected after the fact |
| OV-8 | Selection and `Push selected to Jira` | **On the Outbox tab, not on Overview.** Checkboxes on a summary page ask a person to do bulk work in the place designed for a glance. Overview keeps one-press-per-row |

### OV-3 in detail, because it has a trap in it

`SIGNALS` is a tuple of `(column, value, message)` and three modules unpack it that way. Adding
a fourth element would break every one of them, so the explanation goes in a parallel mapping
keyed by the message, with `risk_detail(row)` beside `risk_signal(row)`.

Two structures that must agree can drift, so a test asserts every message in `SIGNALS` has an
explanation and that no explanation is orphaned. `tests/test_app_signal_copy.py` already forbids
`app.py` from containing any of the six sentences; the same rule now covers the six explanations.

---

## 4. Verification

| id | Verified by |
|---|---|
| OV-1 | The button renders per card, and with the file moved away it is disabled with the reason |
| OV-2 | The sender appears on the card |
| OV-3 | Every signal has exactly one explanation, no orphans, and `app.py` contains neither set |
| OV-4 | Pressing a section header's action sets the tab key and the rerun lands on that tab, asserted in `AppTest` |
| OV-5, OV-6, OV-7 | One action per row in each section, and pressing one opens the dialog for that document |
| OV-8 | Selecting two rows and pressing Push dispatches exactly those two |
| all | `pytest tests/ -q` green, 0 exceptions on every tab, and the card matches `approval-screen-design-v2.html` |

---

## 5. Risk

The page gains a widget per row where it had none. Streamlit re-executes every tab body on every
rerun, so a queue of three hundred documents would build three hundred buttons on Overview even
while nobody is looking at it. Overview already caps each section at five rows and the queue
section at three, which is what keeps that bounded. That cap is load-bearing, not cosmetic.
