# Design the review dialog

Paste this. The files it names are the reference.

---

I need the review dialog designed for an invoice approval tool. There is a working build and a
designed screen; the dialog is the one part that has never been designed, and it is where the
decision actually happens.

**Who uses it.** A senior finance approver at a mid-sized company. They open it to decide,
document by document, whether to accept what an AI pipeline read out of an incoming email.
Pressing approve immediately creates a Jira task for someone else, so the decision is
consequential and the screen has to make a person look before they click.

## What to produce

Three artboards, light theme, desktop:

1. **The dialog at the top**, open over the screen, on the one flagged document
2. **The dialog at the decision**, scrolled down, showing where approve and reject live
3. **The Overview screen re-set in the new type and colour discipline** (section 4 below), so
   the direction can be judged against something already built rather than only on a new surface

## The container, measured, not guessed

It renders in Streamlit's `st.dialog(width="large")`. In the running build at a 1440 viewport:

```
width        1120px, fixed and centred, 160px clear each side
background   #E8EDF7, the page ground. Not white. White panels sit on it
height       unbounded, it scrolls inside a fixed frame
```

At a 1000px-tall viewport the dialog already runs past the bottom, and **approve and reject are
below the fold today.** The decision buttons on a screen built to slow a person down are the
one thing they cannot see. That is the first problem to solve, and it is a layout problem, not
a styling one.

## The six steps, in order

This order is settled and is the order a person asks the questions in. Do not re-sequence it.

```
1. What am I approving        vendor, amount, document number, type
2. Is there a concern         the risk signal
3. Let me look                the document text, beside the extracted fields
4. Where did it come from     sender, subject, body, AI summary and category
5. What happens if I approve   NEW. Nothing like it exists today
6. Decide                     approve / reject
```

## The three things the design has to decide

**1. The contradiction, and it is the most useful thing on the screen.**

The document panel shows this, which is what the model was given, character for character:

```
Dedicated Cloud Compute - EC2 Instance      2    USD 450.00    USD 900.00
High Performance SSD Storage (1TB)          3    USD 120.00    USD 360.00
Managed Database Service (PostgreSQL)       1    USD 240.00    USD 240.00
```

The panel beside it reads `Line items   none`.

The model missed three priced rows a person can see in one glance. Today those two facts sit
side by side and nothing connects them, so the contradiction is available and not visible. A
person should not be able to miss it. This is the single highest-value thing in the round.

**2. Approving creates work in another system, silently.**

It fires a Jira task of type `Payment` with no warning of any kind. One line of text naming the
task, on screen before the button is pressed. A confirmation dialog on top of a dialog is not
the answer.

**3. The decision is below the fold.** Sticky footer, shorter document panel, two columns,
something else. Your call, but the person must be able to see that a decision is being asked of
them without scrolling to find out.

## Type direction, new, and it applies to the whole product

Today it is IBM Plex Sans at every size with headings at weight 600. That weight is the loudest
machine-generated tell in the build.

Move to a **serif display paired with a sans for interface**, headings at **weight 400 with
negative letter-spacing**, in the manner of craft.do. Two constraints on that:

- **The sizes do not transfer.** craft.do is a marketing page with a 66px h1. This is a
  14px-base dense internal tool whose largest heading is 17px. Take the pairing, the weight and
  the tracking. Leave the scale.
- **Name a serif that Google Fonts serves.** The app loads its fonts by URL from
  `config.toml`, so a licensed foundry face cannot be used.

## Colour discipline, measured

**The neutrals are 13 hand-picked hex values.** craft.do carries one ink at four alphas
(`#030302` at 100%, 50%, 12%, 9% and 4%) and no grey ramp at all. Propose the ramp as alpha on
a single ink if it survives on a tinted rather than white ground, or say why it does not.

**The status set does not read as a set, and the numbers say which one is wrong:**

| | hue | lightness | saturation |
|---|---|---|---|
| positive `#3D9A50` | 132 | 42 | 43 |
| caution `#A65A1E` | 26 | 38 | 69 |
| accent `#5B5BD6` | 240 | **60** | 60 |

craft.do's five status colours sit inside a 14-point lightness band. Ours span 21, and the
green and the ochre are already almost exactly where craft.do puts its green and its orange.
**The indigo is the outlier.** Pull it into the band or give a reason not to.

**One rule survives from the previous round and is not up for discussion: risk is the only
thing on screen that gets a filled background.** Identity colour appears as rules and small
chips. It is the reason the eye goes to risk, and it is checkable in a screenshot.

## Forbidden

Indigo-to-purple gradients. A row of three rounded cards with thin-line icons. Glassmorphism.
Inter everywhere. Emoji in headings. The screen this replaced had nine emoji and they were its
loudest tell.

## Use the real content

It is not placeholder text and it is the reason several of these decisions are hard.

```
vendor      Apex Cloud Solutions Pty Ltd        long enough to break a layout built for short names
amount      USD 1,500.00
document    INV-2026-001 · Invoice · issued 2026-08-10 · waiting 13d
risk        Worth a careful look
            No line items to check the total against
            The model read a total but no rows, so nothing adds up to it. Every other check passed.
fields      Date 2026-08-10 · Currency USD · Total source model · Vendor source model
            Line items   none          ← contradicted by the document panel above
email       billing@apexcloud.io
            Reminder: Tax Invoice INV-2026-001 from Apex Cloud Solutions Pty Ltd
            Received 2026-09-07
            "Hello, Please find attached tax invoice INV-2026-001. Amounts are in USD.
             Payment terms are Net 15 days. Regards, billing team"
jira        Approving creates a Payment task
```

**The AI action items, which need their own treatment.** Each is shown above the sentence it
was read from, and the quote is the point: it is what separates a real action from an invented
one. Read these three and the problem is obvious:

```
review the invoice              ← "Thank you for your business!"
verify the vendor information   ← "Vendor: Apex Cloud Solutions Pty Ltd"
check the payment terms         ← "Payment Terms: Net 15 days."
```

The model produced generic actions and attached whatever sentence it happened to be near. The
design should not dress these up as insight. It should make the weakness of the pairing legible,
the same way the line-item contradiction is.

## Attached

```
approval-screen-design-v3.html   the built screen, current, generated from the live database
design-system/foundations/       the colour tokens with every contrast ratio, and the type ramp
design-system/components/        tabs, document card, dense rows, empty state, buttons
fe-screen-spec.md  §5            why the dialog exists and why approve is not on the row
```

`approval-screen-design-v3.html` is a true picture of what is built, including the sidebar,
search box, header and user account, which are designed and not yet implemented. Keep them.
