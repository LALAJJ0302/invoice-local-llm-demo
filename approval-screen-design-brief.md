# Design brief: the approval screen

**For:** Claude Design. **From:** Neo. **Version 2, 2026-09-19.**
**Requirements:** `fe-screen-spec.md` is the source of truth for what appears on the screen.
This document covers only how it should look.

---

## Why there is a version 2

Version 1 of this brief was written almost entirely as a list of prohibitions. No colour, no
cards, no shadows, no icons, no rounded corners, one chromatic colour in the whole interface.
The design that came back obeyed every rule and had nothing in it. Beige on beige, zero icons,
zero radius, one hairline weight used for every separator on the screen, and a single row of
data floating in an empty 1440px canvas.

That result was correct and lifeless, and the brief caused it. There is a difference between
restraint and absence. Linear is restrained: it carries a lot of craft and spends very little
colour. Version 1 produced absence: there was no craft to spend.

So this version inverts the method. It says what the screen must have, with named references
for each. The forbidden list still exists, but it is four items long instead of eight, and it
is the last thing in the document rather than the spine of it.

---

## What this screen is

An internal approval screen for a senior finance approver. They open it to accept or refuse
documents a local AI pipeline has read out of incoming email. Approving creates a Jira task for
someone else, immediately, so the decision is consequential.

It is a working tool used by the same few people every day. Not a landing page, not a marketing
surface, not a consumer app, and not a design case study.

---

## The target, in the client's words

> Lively, attractive, user friendly. Not so much information that the user feels overloaded.

Those four words are the brief. Everything below is an attempt to make them concrete enough to
design against.

**Lively** does not mean colourful. It means the screen looks like software that is running:
surfaces sit at different depths, rows respond to the pointer, the primary action is obviously
the primary action, and data has visible weight. Version 1 looked like a printout because none
of that was present.

**Not overloaded** is the harder half, and it is a real constraint rather than a preference.
The queue usually holds one document. One. A layout that needs twenty rows to look composed
will fail on this data every single day.

---

## References, and what to take from each

Four products, each named for one specific thing. Open them. They are the standard.

### Linear, `linear.app`

The homepage carries a screenshot of the real application. Take two things from it.

**Status is a dot, not a pill.** Colour appears on that screen only as coloured dots roughly 6
to 8 pixels across: amber for In Progress, green on a label, amber on another. Nothing is
filled with colour. Nothing has a coloured background. The text around them is grey.

**The grey scale has many steps.** Borders, dividers, disabled text, secondary text and primary
text are all different greys. Version 1 had exactly one non-text grey and used it for every
separator, which is why nothing on it had hierarchy.

### Plausible, `plausible.io/plausible.io`

A live dashboard, public, no login. Take the row treatment.

**Weight lives inside the row.** In the Sources and Top Pages lists, a pale tinted bar runs
inside each row, behind the text, sized to the value. The row is still a row. It gains weight
without gaining a column. Our queue table currently gives every row identical weight, which is
a large part of why it reads as flat.

Also take the summary strip: a handful of numbers across the top, each with a small delta.
Note how few of them there are and copy that restraint.

### Attio, `attio.com`

Take the light application shell. White working surface, a dense and useful left sidebar, a
near black primary button, one blue accent used on exactly one control. It is proof that a
light interface can look current without being colourful.

### Ramp, `ramp.com/bill-pay`

Our exact domain: accounts payable, invoice approval, an AI agent reading bills. Take the
accent discipline. The entire site is black, white and grey with one acid colour that appears
only on the primary call to action.

### The anti-reference

`stripe.com/billing`, the marketing page, is full-bleed purple and orange gradient. Stripe's
actual dashboard is not. Do not take visual direction from any product's marketing page,
including the ones above. Take it from screenshots of the working product.

---

## What the screen must have

This is the part version 1 was missing. Every item is something the returned design contained
zero of.

**Depth.** A tinted page ground with white working surfaces on it, so a panel reads as a panel.
Subtle shadow is permitted now, at the weight Attio and Plausible use it: a hairline border plus
a shadow you would not notice if it were removed, not a drop shadow announcing elevation.

**Rounded corners.** Everything: surfaces, buttons, inputs, tags, the tinted row bar. Pick a
radius and apply it consistently. Version 1 was square everywhere and read as a Word document.

**Icons, on every row and every nav item.** Functional ones that help a person tell rows apart
at a glance, at the size and weight Linear and Attio use. This is a reversal from version 1,
which banned them.

**An application shell.** A persistent left sidebar with the sections in it, a top bar carrying
the screen title and the account. Right now the design is a bare page with a heading on it and
nothing says this is software.

**Drawn interaction states.** Hover on a row, focus ring on every focusable control, a filled
primary button that is visibly primary, a quieter secondary beside it, and a disabled state.
Show them in the artboard rather than describing them.

**Weight that follows the data.** Per Plausible. The amount, the risk, or both.

**A designed empty state.** The queue is empty most of the time. It must read as finished, not
broken, and it must be a designed composition rather than a sentence in the middle of nothing.

---

## Not overloaded: the rules that keep it calm

**The screen must look composed holding one document.** This is the hard requirement and the
one version 1 failed outright. Design the one-item case first and let it scale up, not the
other way round. If the layout is a wide table, one row in it will always look like a loading
error. Consider giving each pending document real presence instead of one thin line.

**One primary action visible at a time.** Approving is consequential. There should never be two
things on screen competing to be the obvious next click.

**Progressive disclosure.** Everything that is evidence rather than decision belongs behind an
expansion. The approver needs the vendor, the amount, the document and the risk to decide
whether to open it. Everything else is detail for after they open it.

**At most three numbers in the summary strip.** Plausible shows six and it is a metrics product.
This is an approval queue. Three is the ceiling.

---

## Starting palette

**Superseded 2026-09-19 by `approval-screen-components.html`.** The eleven tokens below were
the starting point and the returned design kept all of them, but it also used eleven more that
nobody wrote down: a darker ochre for caution text, an accent hover, an accent text colour, a
border hover, three washes and four greys. The component sheet is now the source of truth for
the palette and documents all twenty-two. This table is kept because it is what was asked for.

A starting point, not a cage. If you can do better, do better, and say what you changed and why.

| Token | Value | Use |
|---|---|---|
| `bg` | `#F6F6F7` | page ground |
| `surface` | `#FFFFFF` | panels, cards, the dialog |
| `border-subtle` | `#EDEDEF` | dividers inside a surface |
| `border` | `#E1E1E4` | the edge of a surface, input outlines |
| `text-faint` | `#A1A1A8` | placeholder, disabled |
| `text-muted` | `#6E6E76` | labels, secondary text |
| `text` | `#1C1C1F` | primary text |
| `accent` | `#5B5BD6` | primary button, active tab, links, focus ring |
| `accent-wash` | `#EEEEFB` | selected row, the tinted weight bar |
| `caution` | `#A65A1E` | the risk signal, as a dot and as text |
| `positive` | `#3D9A50` | a 6px dot on settled states, never a filled pill |

Three chromatic colours exist: the accent for things a person can do, caution for doubt, and a
single green dot for settled. That is a deliberate loosening of version 1's single-colour rule,
which is what made it inert.

## Type

Unchanged from version 1, because this part worked.

| Role | Family |
|---|---|
| Interface text | IBM Plex Sans |
| Amounts, document numbers, dates, any value read out of a document | IBM Plex Mono |

Amounts use tabular figures and are right aligned. A column of money that does not line up is a
functional defect. The split between prose and extracted values is load bearing: a vendor name
the model read out of a PDF is a different kind of thing from a label the interface wrote, and
the typeface should say so.

---

## Forbidden

Four items. Everything not on this list is available.

- Gradients as backgrounds or behind headings, and purple to cyan in any form
- Emoji anywhere in the interface
- Glassmorphism, frosted panels, glowing borders, neon
- Decorative charts, including any chart showing one category at 100 percent

---

## Deliverable

**One screen: the queue, holding one document awaiting approval.** Done properly, at 1440 wide,
light theme, desktop.

Not four artboards. No numbered plates, no annotation paragraphs in the margin, no palette
swatch footer, no title treatment for the design itself. Version 1 returned a case study about
a screen and the screen was less than half the pixels on it. Send the screen.

If one note is needed to explain a decision that is not visible, one short note is fine.

---

## What it gets built in, for information

The screen renders in Streamlit 1.62.0 and styling reaches it as one injected stylesheet, so
some of what is designed will need approximating in the build. That is the build's problem this
round, not the design's. Design the screen that should exist.

Two facts worth knowing anyway: the document panel shows the extracted text rather than a
rendered PDF page, because no PDF embed survives this stack, so design for a monospace text
block. And Streamlit paints its own accent on the active tab and the primary button, and it is
red, so the design needs to say what replaces it.
