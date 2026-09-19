# Spec: the approval screen, V1

**From:** Neo. **Status:** 2026-09-19, groomed with the team's pipeline Phase 2.
**Scope:** screen only. **No backend change, no migration, no new column.**

Every element below reads a column that exists today and holds real data. Verified by query,
not by reading the schema.

---

## 1. Who this is for, and what they are doing

A senior approver. They open the screen to answer one question about each document the AI has
processed: **do I accept this, yes or no.** Approving is not a status change inside this
screen. It closes the open tasks, opens the follow-up task, and dispatches that task to Jira
immediately (`app.py` `record_decision`). The screen must say so before the button is pressed.

**What this screen is not.** Not a mailbox, not an analytics page, not a place to browse
history. Anything that does not help a person accept or refuse a document belongs elsewhere.

---

## 2. Structure

Three tabs. The counts live in the tab labels, so every number is visible without clicking and
there is no separate metric strip to duplicate them.

```
Awaiting approval  1 · 1,500   │   Approved by the system  2   │   Notifications  17
└── default tab
```

| Tab | Contains | Source |
|---|---|---|
| **Awaiting approval** | Documents a person must decide on | `invoices` where `approval_status = 'Pending'` |
| **Approved by the system** | Documents the system approved with no human involved | `approval_status = 'Approved' AND reviewed_at IS NULL` |
| **Notifications** | Notifications recorded and never sent | `outbound_messages` where `state = 'Pending'` |

**Why the second tab exists at all.** The system currently approves 2 of 3 documents by itself.
The report argues in §7.2 that auto-approval at a score of 1.00 is not yet safe, because every
check the gate performs is internal to the document: a duplicate, an unknown vendor and a
well-formatted fraud all score 1.00. A screen that hid what the system decided alone would hide
the risk this project's own report calls its most important open one.

**Why the third tab exists.** FR-6.2 requires every intended notification to be recorded rather
than printed. 17 rows are recorded and have never appeared anywhere in the interface. Showing
them also makes the integration boundary honest: the queue is real, the sending is not.

---

## 3. The queue row

Six columns. The current table has eleven and overflows the right edge.

| Column | Source |
|---|---|
| Vendor | `invoices.vendor_name` |
| Amount | `total_cents / 100` with `currency` |
| Document number | `invoice_number` |
| Type | `document_type` |
| **Risk signal** | derived, see §4 |
| Open | opens the dialog |

**No approve button in the row.** See §5.

Deliberately excluded: file name, ingestion time, `run_id`, raw `validation_score`, invoice
date. All are available and none of them changes a decision.

---

## 4. The risk signal, and why it is computed fresh

Instead of the raw `0.85`, the row states in words what the gate was unhappy about. A person
reading `0.85` cannot act on it; a person reading "no line items to check the total against"
knows to open the document.

**Computed from the invoice's current columns, in this order. The first match wins.**

| Condition | Text |
|---|---|
| `reconciliation = 'short'` | Total is less than the line items add up to |
| `validation_status = 'NeedsReview'` | Did not pass the validation gate |
| `reconciliation = 'unknown'` | No line items to check the total against |
| `total_source = 'fallback'` | Total was recovered by our code, not read from the document |
| `vendor_source = 'fallback'` | Vendor name was inferred, not read from the document |
| `reconciliation = 'plausible'` | Total exceeds the line items; tax or shipping would explain it |
| otherwise | no signal shown |

`short` leads because it is the only genuine anomaly: neither tax nor shipping can reduce a
total below the sum of its parts.

**It must not read `tasks.reason`, even though that column holds a sentence written for exactly
this purpose.** Measured on 2026-09-19: invoice 1's open Review task was created 2026-08-28 and
still says "No total amount was extracted", while the invoice was reprocessed on 2026-09-19 and
now carries a total of 1,500.00. The unique index `ux_tasks_one_open` prevents a second open
task of the same type, so a re-run never refreshes the reason. **Putting that column on screen
would show an approver a sentence that is three weeks out of date and false.**

---

## 5. The dialog

`st.dialog`, available in the pinned Streamlit 1.62.0. Opens from a queue row. **The only place
the approve and reject buttons exist.**

**Why not in the row.** Objective 4 of this project is to keep a person in the approval path.
If a person can approve from the row, they approve on exactly the information the machine had,
which is the same decision the machine already makes by itself. The extra click buys the chance
to look at the document, the evidence quotes and the covering email first. A rubber stamp with
a human hand on it is not human review.

Ordered by the questions a person asks:

```
1. What am I approving        vendor, amount, document number, type
2. Is there a concern         the risk signal from §4
3. Let me look                the PDF itself, beside the extracted fields
4. Where did it come from     sender, subject, body, AI summary and category (collapsed)
5. What happens if I approve  a line naming the Jira task that will be created
6. Decide                     Approve / Reject
```

| Element | Source | Verified |
|---|---|---|
| The document's text | `invoices.archive_path`, extracted with `pypdf` | all 3 files present on disk |
| Extracted fields | `invoices` | 3 rows |
| Line items | `line_items` | 4 rows |
| Covering email | `email_messages` joined on `invoices.email_id` | sender, subject and body all populated |
| AI category and summary | `invoices.category`, `invoices.summary` | 3 of 3 populated |
| Action items with quotes | `invoice_action_items` | 9 rows, each with `evidence_quote` |

**The panel shows the document's extracted text, not the rendered page, and three approaches
were tried first.** `st.pdf` exists in Streamlit 1.62.0 but raises unless the separate
`streamlit-pdf` component is installed, and version 2.0.1 of that component fails on import
against this Streamlit. Embedding the file as a `data:` URI renders an empty frame, because
Streamlit sandboxes the iframe `st.html` produces. Rasterising the first page works and costs a
binary dependency every teammate would have to install.

**The text turned out to be the better artefact anyway.** The question an approver is answering
is whether the model read the document correctly, and the text panel is character for character
what the model was given. It earned its place on the first document it was pointed at: invoice 1
shows "Dedicated Cloud Compute", "High Performance SSD Storage" and "Managed Database Service"
with quantities and prices, while the panel beside it reads `Line items: none`. The model missed
line items a person can see in one glance. A rendered page would have shown what the document
looks like; this shows what the pipeline saw, and the gap between them is the thing worth
catching. The original is one click away for anyone who needs the layout.

**Step 5 is new and the current screen has nothing like it.** Approving fires
`dispatch_task_to_jira` with no confirmation step. A senior approver should know the button
creates work for someone else in another system, not just a status change here. One line of
text is enough; a confirmation dialog on top of a dialog is not.

---

## 6. Emails

**Email content appears inside the document dialog and nowhere else.** An email that never
produced a document does not appear on this screen at all.

That is a deliberate narrowing, and it has a cost worth recording: 15 of the 18 analysed emails
never produced a document, and their category, summary and action items will not be visible
anywhere in the interface. The email analysis is stored, queryable, and covered by FR-3 in the
requirements. It is simply not this screen's job. **The report must not claim the interface
demonstrates that half of the system, because it does not.**

---

## 7. Visual constraints

Decoration is Claude Design's job. These are the constraints it works within.

**Remove, all of it.** Every emoji in a heading. The current screen carries nine and they are
the strongest single tell of machine-generated design on the page.

**Rename.** "Enterprise AI Workflow Automation Dashboard" says nothing this project does that
another project does not. Its subtitle, "End-to-end Document Ingestion, Ollama Local Extraction
& Real-time Analytics", is weightless marketing copy, and "Real-time Analytics" is not true.

**Delete.** The three explanatory paragraphs. Stephen Few: *"Don't clutter the dashboard with
text that explains something that must only be explained once and never again."* The Data
Quality versus Approval distinction is real and belongs in documentation, not permanently on
screen.

**Delete.** The donut chart. It shows one category at 100%, which is decoration rather than
information.

**Avoid.** The visual defaults that identify generated interfaces: indigo-to-purple gradients,
a row of three rounded cards each with a thin-line icon, glassmorphism, and Inter used
everywhere by default.

**Follow.** Few on salience: *"When everything is yelling, no voices stand out."* Only the risk
signal and the pending count should draw the eye. Everything else is neutral.

---

## 8. Open, and not decided here

- **Bulk approval.** With a real queue of 50 documents, opening each one is punishing. Not a V1
  problem: the real queue holds one document.
- **Approving an email.** Rejected for V1 in grooming. `email_analysis` has no approval column
  and the Jira hand-off is keyed on `invoice_id` throughout, so it needs backend work.
- **The task queue and assignee editing.** The three tabs leave no place for the Task Queue, so
  the editable Assignee column goes with it in V1. The argument for dropping it is that
  Payment and File tasks are dispatched to Jira the moment a document is approved, and Jira is
  where they are assigned and worked; a second place to assign the same task invites the two to
  disagree. The argument against is that it works today and syncs back to Jira
  (`task_dispatch.sync_jira_assignee`). **Recorded as a deliberate removal, not an oversight.**

- **A done state for email action items.** `email_action_items` deliberately has no such column,
  because a re-run deletes and re-inserts the children and would reset a person's finished work.
  Invoice action items have `is_done`; email ones cannot until that is solved.
