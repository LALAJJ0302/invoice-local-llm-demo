# Spec: the queue tab becomes cards

**Covers FE-4 to FE-8 from `fe-backlog.md`, which is all of group B.**
Status: proposed, awaiting Neo's approval. Nothing in `app.py` has been changed.

Target: the queue half of `approval-screen-design-v2.html`, built from the components in
`approval-screen-components.html`. Group A landed the tokens; this is the first change that
makes the screen look different.

---

## 1. How CSS attaches to a Streamlit widget

Group A left this open and it decides the shape of everything below.

Streamlit's own class names are build hashes. Measured on the running app:

```
stVerticalBlock st-key-docCard st-emotion-cache-gjtiw6 e4tidtd3
```

`st-emotion-cache-gjtiw6` changes whenever Streamlit is rebuilt, and `data-testid` is internal.
But `st-key-docCard` is ours: passing `key=` to a container or widget puts `st-key-<key>` on the
element. That is a hook the app controls, and it is the only selector this change will rely on.

```python
with st.container(border=True, key=f"doc-{invoice_id}"):
    ...
```

```css
[class*="st-key-doc-"] { ... }
```

The attribute-substring selector matches our own prefix rather than anything Streamlit owns.

Three native capabilities also remove CSS that would otherwise be needed. `st.button` takes
`type="primary" | "secondary" | "tertiary"` and emits `stBaseButton-primary`, verified in the
DOM. `st.container` takes `horizontal`, `gap`, `vertical_alignment` and `width`. `st.columns`
takes `vertical_alignment` and `border`. Layout that can come from an argument will not come
from a stylesheet.

---

## 2. FE-4, the document card

Replaces `document_rows()` at [app.py:399](app.py#L399), which today lays out six `st.columns`
and a header row of captions.

One card per pending document:

```python
with st.container(border=True, key=f"doc-{row['id']}"):
    st.markdown(header_html, unsafe_allow_html=True)      # icon, vendor, type tag, meta, amount
    st.markdown(signal_html, unsafe_allow_html=True)      # FE-6, omitted when there is no signal
    chips, action = st.columns([3, 1], vertical_alignment="center")
    chips.markdown(provenance_html, unsafe_allow_html=True)
    if action.button("Review document", type="primary", key=f"rev-{row['id']}"):
        review_dialog(row)
```

The button stays a real `st.button`. It cannot live inside the markdown, because a string cannot
carry a widget, and a card that is one HTML blob would need a custom component to be clickable.
That is the one place the design and the framework do not meet, and the answer is to let the
framework win: the card is markdown, the action is a widget, and CSS makes the seam invisible.

**The weight rail is not built.** It was removed from the design for a reason recorded in
`approval-screen-components.html`: with one document waiting it is always full, and the quantity
it originally encoded summed USD and AUD.

---

## 3. FE-5, and the contradiction that has to be settled first

**This item cannot be built as designed, and the conflict is in our own documents.**

`fe-screen-spec.md` §2 says, about the three tab labels carrying their counts:

> The counts live in the tab labels, so every number is visible without clicking and there is
> no separate metric strip to duplicate them.

`approval-screen-components.html` C3 is a metric strip of three tiles, and two of the three
repeat a tab label exactly. `Waiting for you 1 · USD 1,500.00` is the same pair of numbers as
`Awaiting approval 1 · 1,500`. The design brief asked for at most three tiles and never
mentioned that the spec had already ruled the strip out, which is my error, not the designer's.

Only one of the three tiles carries something no tab shows: **Oldest wait 12d, since 7 Sep**.

Three ways out:

| Option | Cost |
|---|---|
| **A. Drop the tile row. Counts stay on tabs.** Oldest wait already appears on the card as `waiting 12d` | Loses nothing. The screen gets quieter, which is the stated goal |
| B. Keep the tiles, strip the counts from the tab labels | Contradicts the spec's reasoning, and a number then needs a click |
| C. Keep both | The duplication the spec rejected by name |

**Recommended: A.** `waiting 12d` is on the card already, and with one document waiting the
oldest wait and that document's wait are the same number by definition. Where the queue holds
many, the sort control in FE-8 puts the oldest first, so the answer is the top card.

If A is taken, FE-5 is closed as "not built, superseded by the spec" rather than done, and
`fe-backlog.md` records why.

---

## 4. FE-6, the risk strip

The `.signal` rule already exists from group A. This gives it the C5 composition: an 8px
`--caution` dot, the sentence in `--caution-text`, and the explanation in `--text-muted` on a
`--caution-wash` band with a `--border-subtle` rule above it.

**The sentence still comes from `risk_signal()` and this change must not put any copy in
`app.py`.** `review_signals.py` is the source, it is tested, and its docstring records why it
reads the invoice's current columns rather than `tasks.reason`. A card with no signal gets no
band, per C4's second variant.

The explanation sentence beside the signal, "The model read a total but no rows...", exists in
the design but not in `review_signals.py`. It is **not built in this change**. Adding a second
sentence per signal means six more strings, and they belong beside the first six in that module
with a test, not inlined into a card template.

---

## 5. FE-7, the empty state

Replaces `st.caption("Nothing is waiting for you.")` at [app.py:406](app.py#L406) with C8: the
`--positive-wash` circle, the heading, one sentence, and a secondary button to the other tab.

The sentence must be true. `reviewed_at` is null on every row in this database, so no person has
decided anything and the line reports what the system did:

> Everything the pipeline read today has been decided. The last two it cleared on its own at 04:13.

Both numbers come from a query, not from a constant.

---

## 6. FE-8, filter and sort

The design draws `All vendors`, `Oldest first` and a disabled `Approve selected`.

**`Approve selected` is not built.** It is bulk approval, which `fe-screen-spec.md` §8 rules out
of V1, and drawing a disabled control for a feature that does not exist advertises a promise.

`All vendors` and `Oldest first` are built and both do something:

- Vendor: `st.selectbox` over the distinct vendors in the pending set, `All vendors` first
- Sort: `st.selectbox` of `Oldest first` and `Largest amount first`

Sorting by amount across currencies orders USD against AUD, which is the arithmetic this project
watches the model for. **Sort by amount therefore sorts within currency and groups by it**, or
the option does not ship. With one pending document it cannot be told apart either way, so it is
the kind of thing that is only correct in code, not on screen.

Default sort is oldest first, which is also what makes option A in section 3 safe.

---

## 7. What this change does not do

No sidebar navigation, that is group E. No dialog, that is group C. No change to any query
result, any table, or any decision path. `record_decision()` is not touched.

---

## 8. Verification

| id | Verified by |
|---|---|
| FE-4 | Screenshot at 1440 next to `approval-screen-design-v2.html`; `AppTest` reports 0 exceptions and one button per pending document |
| FE-6 | The strings on screen are byte-identical to `SIGNALS`; a test asserts the screen renders no signal copy that the module does not define |
| FE-7 | Screenshot with the pending set empty, taken by pointing the app at a database copy with no pending rows |
| FE-8 | Changing either control changes the order or the membership of the cards, asserted in `AppTest` rather than looked at |
| all | Rendered text still contains every value the before screenshot showed, except where this spec says it changes |

---

## 9. Risks

- **`st.container(border=True)` draws its own border and radius from the theme.** Group A set
  `baseRadius` to 8px and the design's panels are 12px. Either the card overrides radius in CSS
  or the design accepts 8px. Cheap either way, but it is a decision and the screenshot settles it.
- **Cards are taller than rows.** Three documents fit on a screen; three hundred do not. C4
  documents a two-line variant for that case and it is not built here, so the queue at scale is
  a known gap rather than a solved problem.
- **`st.columns` inside a bordered container may not align the button to the chips.**
  `vertical_alignment="center"` is the intended fix and it is an argument, not CSS.
