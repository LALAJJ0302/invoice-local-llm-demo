# FE spec: put the validation score on every document

**Written 2026-09-24. Status: approved by Neo and built the same day. 17 tests in
`tests/test_score_block.py`, suite green at 587.**

Neo approved all three open questions as recommended: the rail, the full six-surface scope, and
amending `fe-screen-spec.md` §3 and §4 with a dated note rather than rewriting them. Both of
those amendments are in place.

**Two things changed during the build, both found by looking at the running app rather than at
the artboard.** They are recorded in §9.

Requested by Neo after looking at the queue card and the review dialog and finding no score on
either. He is right, and the audit below says exactly how right.

This spec does two things. It says where the score goes, and it reverses a recorded decision
that said the score should not be there. The reversal is the part worth reading, because that
decision was argued rather than assumed, and the argument still holds. What changes is what
sits beside the number, not whether the number is allowed.

---

## 1. Where the score is today

Audited against the running app, 2026-09-24. `validation_score` and `validation_status` are both
already in `load_data()`, so nothing here needs a query change or a migration.

| Surface | Renderer | Score shown |
|---|---|---|
| Overview, three tiles | `tile()` | no |
| Overview, Awaiting approval, 3 cards | `document_card()` | **no** |
| Overview, Approved by the system | `record_row()` | **yes**, as `score 1.00 · 04:13` |
| Overview, Outbox | `record_row()` | not applicable |
| Overview, History | `record_row()` | **no** |
| Awaiting approval destination | `document_card()` | **no** |
| Approved by the system destination | `document_card()` | **no** |
| History destination | `history_body()` | **no** |
| Documents | `dense_rows()` | **no** |
| Review dialog | `review_dialog()` | **no** |

**One surface out of nine.** The number appears in a single section of a single page, and it is
absent from the card and from the dialog, which are the two places a person actually decides.

The one place it does appear was itself an amendment. [app.py:1594](app.py#L1594) carries the
reason in a comment: *"The score belongs on the row. This section exists to show what was
approved without a person, and the number behind that decision was only in the caption."* So the
principle this spec extends is already in the build. It was applied to one section and never
carried to the rest.

---

## 2. The decision this reverses, stated fairly

`fe-screen-spec.md` §3 lists `validation_score` under **Deliberately excluded**, beside file
name, ingestion time and `run_id`, with the reason *"All are available and none of them changes
a decision."* §4 makes the argument properly:

> Instead of the raw `0.85`, the row states in words what the gate was unhappy about. A person
> reading `0.85` cannot act on it; a person reading "no line items to check the total against"
> knows to open the document.

`review_signals.py` repeats it in its own module docstring, and adds a second reason:

> the weights behind it changed twice during the project, so it is not even comparable across
> dates.

**Both reasons are true and neither is a reason to hide the number.** They are reasons not to
show the number *alone*, and not to let it be read as something it is not. The original decision
treated the choice as either the sentence or the score. It is not.

What each one answers is different:

- The sentence answers **what to do**: open this document, look at the line items.
- The score answers **how far off it is**: 0.85 against a gate at 0.80 is a near miss, 0.40
  would not be. Two documents both reading "no line items to check the total against" can be a
  long way apart, and today the screen cannot tell them apart.

The third reason to show it is the one Neo is actually asking about, and it is about the
project rather than the user. The gate is one of the two pieces of work this group built itself
(the other is the storage layer). The report argues about it, `validation-gate-spec.md` is 478
lines about it, and the screen that is supposed to demonstrate it renders its output once.

**What §3 and §4 keep.** The sentence stays exactly where it is, in the same position, at the
same weight. The score is added beside it and never replaces it. If this spec is built, §3 and
§4 are amended in place with a dated note rather than rewritten, which is the precedent set by
`fe-screen-spec.md` §2 and `design-handoff/02-DESIGN-SYSTEM.md`.

---

## 3. The misread this has to defend against

`CLAUDE.md` names it as a confirmed defect: **`confidence_score` is not a confidence score.** It
measures field completeness and substring agreement with the document. It was renamed in the
database for exactly this reason.

A bare `0.85` on a card invites a reader to hear "the model is 85% sure", which is the single
most damaging misreading available on this screen and is false. Anyone at the presentation who
takes that away has learned the opposite of what the project found.

So the number never ships alone. Three rules:

1. **The label is `Validation score`.** Never `Confidence`, never `Accuracy`, never a bare
   percentage with no noun.
2. **It is shown against its threshold**, not as a free-floating value. `0.85` means nothing;
   `0.85, gate at 0.80` means a near miss, and the position of the mark says so before the
   number is read.
3. **One sentence says what it measures**, and that sentence lives in `review_signals.py` with
   the other copy, not in `app.py`. `tests/test_app_signal_copy.py` already exists to stop copy
   being written into the layout and will cover this too.

---

## 4. What gets built

### 4.1 The component

A score block, in the card head under the amount and in the dialog head in the same place.

```
                                    Validation score
                                    0.85  ·  Validated
                                    ├──────────────┤▮──┤
                                                gate 0.80
```

Three parts:

- **The number**, at the size of the card meta, not the size of the amount. The amount is the
  most important thing on the card and this must not compete with it.
- **The verdict word**, `Validated` or `Needs review`, straight from `validation_status`. This
  is the gate's own verdict and it is not derivable from the score: the gate has hard rules
  beyond the threshold, so a document can score above 0.80 and still fail.
- **A rail** with the fill to the score and a tick at the threshold.

### 4.2 The rail, and the one design rule it has to clear

`fe-theme-v2-spec.md` §0.1 keeps one constraint from the original design system:

> **risk is the only thing that gets a filled band.**

A rail is not a band. It is 4px of track with a fill, at the weight of a rule rather than a
block, and the risk strip above it keeps its tinted background and its filled verdict pill. My
reading is that this clears the rule. **It is close enough to the line that it is worth Neo's
explicit yes rather than my judgement**, because the rule exists precisely to stop the screen
growing a second thing that shouts.

If the answer is no, option B in §7 drops the rail and keeps the number and the threshold as
text. It is less legible at a glance and it breaks no rules.

Colour: the fill takes `--positive` when `validation_status` is `Validated` and `--caution` when
`NeedsReview`. Both already clear 4.5:1 in the palette, measured in FE-3b. The threshold tick
takes `--text-muted` at 5.05.

### 4.3 Where it goes

| id | Surface | What lands |
|---|---|---|
| SC-1 | `document_card()` | The block in the card head, under the amount. Reaches Awaiting approval, Approved by the system, and the Overview section, since all three call the same renderer |
| SC-2 | `review_dialog()` | The same block in `dlg-head`, under the amount, above the risk strip |
| SC-3 | `review_dialog()` stored panel | A `field_row` reading `Validation score` with the number and the verdict, so the panel that lists what the model stored also lists what our check made of it |
| SC-4 | `history_body()` | The score on the row. A decision looked at after the fact should show what the machine scored when the person decided |
| SC-5 | `record_row()` on Overview auto rows | Wording aligned to `validation score 1.00`. The number is already there; today it reads `score 1.00` and nothing says which score |
| SC-6 | `documents_panel()` | A score column. This is the everything table and it is the one place the whole spread is visible at once |

SC-1 and SC-2 are the request. SC-3 to SC-6 are what stops the screen contradicting itself by
showing the number in some places and not others.

### 4.4 The threshold is not a literal in `app.py`

`GATE_THRESHOLD = 0.80` goes in `review_signals.py`, which `app.py` already imports and which
pulls in no heavy dependency.

**`app.py` must not import `main.py`.** `main.py` imports `ollama` at module level, and the
dashboard running without Ollama is a property worth keeping: it is what lets the screen be
demonstrated on a machine that never pulled a model.

A test in `tests/test_review_signals.py` asserts
`review_signals.GATE_THRESHOLD == main.ConfidenceValidator().threshold`. The test may import
`main`; the app may not. If someone retunes the gate, the test fails rather than the screen
quietly drawing the tick in the wrong place.

---

## 5. The thing this will expose, and it should

Invoice 1 will read:

```
Validation score 0.85 · Validated
Worth a careful look · No line items to check the total against
```

A reader will ask why a document that passed is worth a careful look. **That is the correct
question and the screen currently hides it.** The answer is that the gate's hard rules all
passed (the total was found beside a grand-total label, the vendor is not a caption, the line
items do not exceed the total) while `reconciliation` is `unknown`, because there are no line
items to reconcile against. The gate cleared it and our own risk rule still flags it.

This is the project's argument about auto-approval made visible on one card. `requirements-spec.md`
§7.2 calls auto-approval at 1.00 the most important open risk, and this is the near-miss version
of it. Adding the score turns that from a paragraph in a report into something a person can see.

**No copy change is proposed to explain it.** The two lines are each true and they sit together.
If the group decides the pair needs a joining sentence, that sentence belongs in
`review_signals.py` and is a separate item.

---

## 6. Tests

| Test | Asserts |
|---|---|
| `test_the_card_carries_the_validation_score` | Every card rendered in Awaiting approval carries the number and the verdict word |
| `test_the_dialog_carries_the_validation_score` | Same block in the dialog, asserted against the rendered dialog |
| `test_the_threshold_matches_the_gate` | `review_signals.GATE_THRESHOLD == main.ConfidenceValidator().threshold` |
| `test_app_holds_no_score_copy` | Extends `test_app_signal_copy.py`. The sentence saying what the score measures is not written in `app.py` |
| `test_a_null_score_renders` | `validation_score` is nullable. A row with NULL draws `-` and no rail, and raises nothing |
| `test_the_page_still_loads` | `AppTest`, 0 exceptions, which is the non-functional requirement already in `fe-backlog.md` |

The null case is not hypothetical: `database-spec.md` marks the column nullable, and
`app.py:1596` already guards it with `pd.isna`. Any new call site needs the same guard.

---

## 7. The alternative, kept so it is not re-proposed as an oversight

**Option B: text only.** `Validation score 0.85 of 1.00 · gate at 0.80 · Validated`, no rail, no
colour. Clears `fe-theme-v2-spec.md` §0.1 with nothing to argue about, and is the fallback if
§4.2 is refused. The cost is that it is read rather than seen, and "ดูง่ายๆ" is the requirement
Neo actually stated.

**Option C: a score column on every dense row and nothing on the card.** Rejected. It puts the
number furthest from the decision, which is the mistake the current build already makes.

---

## 8. Out of scope

- **Showing the per-check breakdown.** `ConfidenceValidator.explain()` returns the full `checks`
  dict, and none of it is stored: the database keeps the score, the verdict, `total_source`,
  `vendor_source` and `reconciliation`. Showing the breakdown means a migration and a pipeline
  re-run. Worth doing, not worth doing before a presentation.
- **Comparing scores across dates.** The weights moved on 2026-09-03 and again on 2026-09-08.
  Nothing on this screen may imply a score is comparable to an older one, which is why no trend,
  sparkline or delta is proposed.
- **Changing the gate, the threshold or the weights.** This spec renders what the gate already
  decided and touches no scoring logic.

---

## 9. What changed during the build

Both were invisible in the markup and obvious in a screenshot, which is the same lesson FE-25
recorded when the artboard drifted from the app within an hour.

**The gate mark was drawn in `--text-muted` and disappeared exactly where it mattered.** On the
unfilled track it was legible. On invoice 1, which scores 0.85 against a gate at 0.80, the mark
sits *inside* the green fill, and a muted grey line on `--positive` is close to invisible. The
near miss is the case the mark exists for, so it failed at its only job. It is now a 2px notch
in the surface colour: it reads against the fill and the track alike, because it is the absence
of both rather than a third colour competing with them.

**The full sentence was too long for the review dialog.** `SCORE_NOTE` is four lines, and in the
stored-field panel those four lines landed between `Vendor` and `Line items` and pushed the
contradiction panel down the screen. The contradiction is the most important thing in that
dialog, by `fe-screen-spec.md`'s own argument and by FE-11's, and a footnote about scoring was
outranking it. `SCORE_NOTE_SHORT` now carries it in one line there. The full sentence survives
as the hover text on every score block, so nothing is lost, and the dialog got shorter, which
helps FE-31 rather than hurting it.

## 10. What this did not fix

**FE-31 is still open.** The dialog is two lines shorter than it was, which moves the Approve
button up by roughly that much, and the measurement in FE-31 was taken before this change. The
row stays open until someone re-measures at a 1000px viewport rather than assuming a shorter
dialog crossed the line.

**Nothing here changes what the gate decides.** Every number on screen is a column that was
already in the database. No scoring logic, no threshold and no weight was touched, so the
evaluation figures in `evaluation/` and the report are unaffected by this change.
