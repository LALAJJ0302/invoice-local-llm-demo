# Design brief: the approval screen

**For:** Claude Design. **From:** Neo, 2026-09-19.
**Requirements:** `fe-screen-spec.md` is the source of truth for what appears. This document
covers only how it should look, and what it must not look like.

---

## What this screen is

An internal approval screen for a senior finance approver. They open it to accept or refuse
documents a local AI pipeline has read out of incoming email. Pressing approve closes the open
tasks and creates a Jira task for someone else, immediately.

It is a working tool used by the same few people every day, not a landing page, not a product
marketing surface, and not a consumer app.

---

## The design system, and why each choice was made

Every default was chosen against something true about this product rather than taken from a
framework. That is the whole point: the defaults are what make generated interfaces look
generated.

### Colour: paper and ink, with one colour reserved for doubt

This product reads documents and asks a person whether to trust what was read. Its argument is
evidence. So the palette is paper and ink, and **exactly one chromatic colour exists in the
entire interface: the caution colour on the risk signal.**

| Token | Value | Use |
|---|---|---|
| `ground` | `#FAF9F6` | page background, warm paper rather than blue-white |
| `surface` | `#FFFFFF` | the dialog, and nothing else |
| `ink` | `#1A1917` | primary text, warm near-black, never `#000000` |
| `ink-muted` | `#6B6862` | labels, secondary text |
| `hairline` | `#E3DFD7` | every separator |
| `caution` | `#A65A1E` | the risk signal only |

**No green. No red. No blue. No status pills.** Approved and auto-approved states carry no
colour at all; they are distinguished by position and by weight.

This is Stephen Few's rule applied literally: *"differences in the visual salience of items on a
dashboard should never be arbitrary... when everything is yelling, no voices stand out."* If the
only coloured thing on the screen is a risk, the eye goes to risk. A green "Approved" pill would
compete with it for no reason, because nobody needs their attention drawn to a document that is
already fine.

Caution is ochre rather than red on purpose. Red means failure. A risk signal is not a failure,
it is a note in the margin saying look at this one.

### Type: two families, and figures that line up

| Role | Family | Why |
|---|---|---|
| Interface text | **IBM Plex Sans** | Built for technical and enterprise work, has genuine character, and is not Inter |
| Amounts, document numbers, dates, any value read out of a document | **IBM Plex Mono** | Monospace already signals "extracted value" throughout this project, and it makes a column of money align |
| Page title only, optional | IBM Plex Serif | One place, or leave it in the sans |

**Amounts must use tabular figures and must be right-aligned.** A column of money that does not
line up is a functional defect, not a stylistic preference.

The distinction between prose and extracted values is load-bearing here, not decorative. A
vendor name the model read out of a PDF is a different kind of thing from a label the interface
wrote, and the typeface should say so.

### Layout: a ledger, not a deck of cards

- **Rows separated by hairlines. No cards, no boxes, no shadows.** Few: *"apply visual
  separators such as borders or fill colors with a gentle hand, making them just visible enough
  to do the job and no more."*
- The pending queue occupies the top left. It is the reason the screen exists.
- Generous vertical rhythm; the screen holds three documents, not three hundred, so density is
  not the problem here. Legibility is.
- One accent element visible per row at most.

---

## Forbidden

These are the defaults that identify a machine-made interface in 2026. None of them may appear.

- Indigo-to-purple or purple-to-cyan gradients, anywhere, including behind headings
- A row of three rounded cards, each with a thin-line icon, a heading and two lines of text
- Glassmorphism, frosted panels, glowing borders, neon on dark
- Emoji in headings or labels. The screen being replaced has nine of them and they are the
  single loudest tell on it
- Inter as the interface font
- Drop shadows used to imply elevation on a flat internal tool
- Weightless headline copy. "Enterprise AI Workflow Automation Dashboard" and its subtitle
  "End-to-end Document Ingestion, Ollama Local Extraction & Real-time Analytics" are both being
  deleted for exactly this reason, and the second one also is not true
- Any decorative chart. A donut showing one category at 100% is being deleted

---

## Screens to produce

### 1. The queue, default state

Three tabs, counts on the labels, no separate metric strip:

```
Awaiting approval  1 · 1,500     Approved by the system  2     Notifications  17
```

The queue holds one row. Use the real data, not placeholders:

| Vendor | Amount | Document | Type | Signal |
|---|---|---|---|---|
| Apex Cloud Solutions Pty Ltd | USD 1,500.00 | INV-2026-001 | Invoice | No line items to check the total against |

**Show what an empty queue looks like too.** With one document in it, this screen is empty most
of the time, and an approval tool that looks broken when there is nothing to approve is a real
failure. It should read as finished, not as missing.

### 2. The second tab, approved by the system

| Vendor | Amount | Document | Type |
|---|---|---|---|
| Synthetix AI Consulting | USD 2,350.00 | INV-2026-003 | Invoice |
| NextGen Hardware Supplies | AUD 2,650.00 | INV-2026-002 | Invoice |

These were approved with no human involvement, at a score of 1.00. The tab needs one line of
framing that makes that fact plain without alarming anyone, because the project's own report
argues this is its largest open risk.

### 3. The third tab, notifications

17 rows, recorded and never sent. **Each row must show its age**, because the oldest is from
30 August and its text says "NeedsReview at score 0.25" while that document now reads Validated
at 1.00. Showing the age turns a stale message into the honest point: an unsent queue decays.

### 4. The dialog

Opens from a queue row. Contains the only approve and reject buttons on the screen.

```
Apex Cloud Solutions Pty Ltd                          USD 1,500.00
INV-2026-001 · Invoice

⟨caution⟩ No line items to check the total against

┌────────────────────────┬──────────────────────────────────┐
│                        │  Date            2026-08-10      │
│   the PDF itself       │  Currency        USD             │
│   rendered inline      │  Total source    model           │
│                        │  Line items      none            │
└────────────────────────┴──────────────────────────────────┘

▸ Covering email          billing@apexcloud.io
▸ What the AI found       3 actions, each with the sentence it came from

Approving creates a Jira task "Payment" immediately.

        [ Approve ]                    [ Reject ]
```

Real content for the collapsed sections:

- Email subject: "Reminder: Tax Invoice INV-2026-001 from Apex Cloud Solutions Pty Ltd"
- Email body opens: "Hello, Please find attached tax invoice INV-2026-001. Amounts are in USD.
  Payment terms are Net 15 days."
- Action items, each shown above the quote it was read from:
  - review the invoice — "Thank you for your business!"
  - verify the vendor information — "Vendor: Apex Cloud Solutions Pty Ltd"
  - check the payment terms — "Payment Terms: Net 15 days."

**The evidence quote is the most important thing in this section and should be designed as
such.** It is what lets a person tell a real action from an invented one. The first of the three
above was read from "Thank you for your business!", which supports nothing, and a person should
be able to see that at a glance.

---

## Constraints from the build

Rendered in Streamlit 1.62.0, so `st.tabs`, `st.dialog`, `st.pdf` and `st.popover` are
available. Styling reaches it through a single injected stylesheet, so the design should not
depend on markup this project cannot produce. Light theme only; nobody asked for dark and a
dark default is itself one of the tells.

Desktop first, 1440 wide. It is an internal tool opened on a work machine.
