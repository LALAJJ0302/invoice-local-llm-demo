# Spec: tinted surfaces, and the mockup catches up with the build

Status: **approved by Neo 2026-09-20, with three amendments recorded in section 0.**

Approved as written for parts 1 and 2. Part 3 changed direction at approval and the version
below is the amended one, not the one that was reviewed.

Extends `fe-theme-spec.md`, which put the token layer into `config.toml` and `app.py`. That spec
was right about where tokens live. This one changes what they are, and fixes the fact that the
design mockup no longer describes the thing that got built.

Three parts, in this order:

1. The body of `approval-screen-design-v2.html` catches up with what `app.py` actually renders
2. The design system is uploaded to a Claude Design project
3. The neutrals gain a navy tint, and every surface gets an identity

---

## 0. The three decisions taken at approval, 2026-09-20

**0.1 The one-colour rule is removed.** `design-handoff/02-DESIGN-SYSTEM.md` set it as a hard
constraint: one chromatic colour in the whole interface, on the risk signal, and nowhere else.
Neo removed it. It is struck in that file with a dated note rather than deleted, because it was
sent to Claude Design and shaped the artboards that came back, so a reader of those artboards
needs to know why they obey a rule the build no longer does.

`design-handoff/` is in `.gitignore`, so that amendment is on disk and not in any commit. This
section is the committed record of the decision.

What replaces it is weaker and still worth keeping: **risk is the only thing that gets a filled
band.** Identity colour appears as 3px rules and 24px icon chips. That is checkable in a
screenshot, which is the only reason it is written as a rule at all.

**0.2 The Claude Design project is new.** `Invoice approvals design system`, created rather than
filling the empty `Design System` project. Section 3 stands as written.

**0.3 The palette is cool, not warm.** This is the amendment that rewrites section 4. The spec
as reviewed proposed warm paper on the Brex evidence. Neo asked for a professional register
instead: indigo, blue, navy, grey, white.

The Inspo finding survives the change intact, because the finding was never "go warm". It was
**the flat read comes from the ground having no colour at all, not from too few coloured
elements.** `#F6F6F7` is a grey with almost no chroma in any direction. Giving it a navy tint
answers that just as well as giving it a warm one, and it agrees with the accent this project
already ships, `#5B5BD6`. Warm paper would have put a cool indigo accent on a warm ground, which
works for Brilliant and Blue Bottle but is the harder thing to land.

One exception is carried forward deliberately, and it is the only hue in the build outside the
indigo-to-navy family: **risk stays ochre.** A caution band in blue reads as information, not as
a warning. Amber for caution is standard in every professional system that has an opinion,
IBM Carbon and Atlassian included, so this is inside the register Neo asked for rather than
against it.

---

## 1. What Inspo was asked, and the one finding that matters

The `inspo` MCP server was installed this session, so its tools are not loaded into the running
client yet. It was called directly over HTTP instead, at `https://inspomcp.dev/api/mcp`. Four
calls: one `recommend`, two `search_screens`, two `get_design_system`.

| Reference | Ground | Accent | What it is |
|---|---|---|---|
| Brex | `#f5f5f0` warm off-white | `#fc5c04` orange, CTAs only | fintech, dense, technical |
| Brilliant | `#fbdfd7` cream | `#2ac852` green, one badge | cards on a tinted tray |
| Blue Bottle | warm off-white | `#18b0e1` cyan, five small marks | editorial commerce |
| Louis Poulsen | `#d5d6b8` warm off-white | none in the first viewport | furniture, calm |
| Peak Design | stone field | `#a16b3d` earthy | technical product |

**The finding: none of them add more coloured elements. They move the paper.** Every one of
these reads as warm and deliberate while spending its chroma on one thing. Brex is the closest
match to this project by content, and it ships a near-black on a warm off-white with a single
orange that appears on buttons and one link, and nowhere else.

Set against ours:

```
ground   #F6F6F7   a cool grey at almost zero chroma
surface  #FFFFFF   pure white
contrast between them: 1.08:1
```

Two nearly identical non-colours, one very slightly cooler than the other. That is the "too
white". It is not a missing colour count, it is a missing ground.

**Two honest caveats.**

Inspo's own instructions say not to call it when the project already has a design system and to
follow that system instead. This project has one. It was called anyway because the question was
a decoration question, not a structure question, and the answer is used as evidence for one
token decision rather than as a replacement for `approval-screen-components.html`.

And the archive is marketing landing pages, not dense internal tools. The ground-to-accent
relationship transfers. The layout advice it returned (hero heights, 80-160px section rhythm)
does not, and is ignored.

---

## 2. Part 1. The mockup body catches up with the build

`approval-screen-design-v2.html` is the Claude Design output. Its chrome is still ahead of the
build and still wanted. Its body describes a screen that no longer exists.

### Untouched, byte for byte

Everything Claude Design drew that we have not built:

- the whole `<aside>`: Payables mark, search box with the `⌘K` hint, the six nav rows,
  Saved views, and the user account block at the bottom
- the `<header>`: breadcrumb, the "Pipeline read 04:13" status, the avatar

None of these exist in `app.py`. The app runs with `initial_sidebar_state="collapsed"` and no
sidebar content at all. They stay in the mockup as the standing design of what comes next.

### Rewritten

Only the content div inside `<main>`, currently `style="flex:1; padding:26px 30px 0"`.

| The mockup says | The build renders |
|---|---|
| 3 tabs: Awaiting approval, Approved by the system, Notifications | 5 tabs: **Overview** (default), Awaiting approval, Approved by the system, **Outbox**, **History** |
| `Notifications 17` | `Outbox 17`. FE-16 renamed it: the table is a transactional outbox |
| 3 tiles as the queue header | 3 tiles on Overview only. FE-5 cut them from the queue for repeating the tab labels |
| the queue as the landing view | Overview is a page with four sections, each carrying its own rows |
| no risk strip verdict | the strip leads with a verdict pill, then the finding, then why |
| no provenance chips | two chips per card saying whether the model read the value or our code recovered it |

Added to the mockup because the build has them and the design has never seen them: the section
headers with their icon chips, the dense row panels, the `Push to Jira` action, the
"That is everything waiting" line, and the closing note about Jira tasks.

### Real content, from the database

The brief's rule was to use real data, and it still applies. Read from
`workflow_platform.db` on 2026-09-20:

```
Awaiting approval  Apex Cloud Solutions Pty Ltd   INV-2026-001  USD 1,500.00  score 0.85
Approved by system Synthetix AI Consulting        INV-2026-003  USD 2,350.00  score 1.00
                   NextGen Hardware Supplies      INV-2026-002  AUD 2,650.00  score 1.00
Outbox             Jira 2 pending, Teams 15 pending
History            empty
```

The two currencies matter: they are why the tile reads "more than one currency" instead of a
total, and the mockup has never shown that case.

### Two files, not one

- `approval-screen-design-v2.html` is updated in place. It becomes a true picture of today.
- `approval-screen-design-v3.html` is new, and is v2 with Part 3 applied.

That gives the report a before and after that differ by the theme alone, with the structure
held constant. Overwriting v2 with the themed version would lose that.

---

## 3. Part 2. The design system reaches Claude Design

`DesignSync list_projects` returns three projects, all owned by Neo:

```
เสียดายเล่นๆ Design System     5bc1061d…   updated 2026-06-23
Baseline — Tennis E-book       f12f9097…   updated 2026-07-11
Design System                  f2fe85ee…   updated 2026-06-29, list_files returns []
```

None holds this project. "Design System" is empty and predates all of this work, so a new
project is created: **`Invoice approvals design system`**. Decided 2026-09-20, see 0.2.

Uploaded as preview files, each carrying a first-line `@dsCard` marker so the Design System
pane builds its own index:

| Path | Group | Source |
|---|---|---|
| `foundations/tokens.html` | Colors | the token block, with the measured contrast on each pair |
| `foundations/type.html` | Type | IBM Plex Sans and Mono, the six heading sizes |
| `components/tabs.html` | Components | C2, the tab bar with count chips |
| `components/document-card.html` | Components | C4 plus C5, the card and its risk strip |
| `components/dense-rows.html` | Components | C7, the Overview row panels |
| `components/empty-state.html` | Components | C8 |
| `components/buttons.html` | Components | C6, primary, secondary, disabled, focus ring |
| `screens/approval-queue.html` | Screens | `approval-screen-design-v3.html` |

Ordering is fixed by the tool: `list_files`, then `finalize_plan` with those paths, then
`write_files`. `finalize_plan` prompts, and the path list Neo sees there is the real contract,
not this table.

**Nothing is deleted.** The plan carries writes only.

---

## 4. Part 3. The token change

The rule this follows:

> Tint the neutrals toward the accent. Keep the surface white so cards lift off the paper.
> Spend chroma on meaning, not on decoration.

### The neutral ramp

Every neutral keeps roughly its lightness and moves from a hueless grey to a navy-tinted one.
Nothing gets meaningfully lighter or darker, so no spacing, weight or size decision is
disturbed.

| Token | Now | New | Why |
|---|---|---|---|
| `--ground` | `#F6F6F7` | `#E8EDF7` | the single change that answers "too white". Navy-tinted paper |
| `--surface` | `#FFFFFF` | `#FFFFFF` | **unchanged.** White on tinted paper is what makes a card read as a card |
| `--surface-sunk` | `#FBFBFC` | `#F7F8FC` | |
| `--border-subtle` | `#EDEDEF` | `#E6E9F2` | |
| `--border` | `#E1E1E4` | `#D8DCE8` | |
| `--border-hover` | `#C9C9CF` | `#B9C0D1` | |
| `--text` | `#1C1C1F` | `#161A23` | navy-black rather than neutral black |
| `--text-strong` | `#3F3F46` | `#3A4152` | |
| `--text-muted` | `#6E6E76` | `#5F667A` | |
| `--text-faint` | `#A1A1A8` | `#98A0B3` | disabled controls only. Role unchanged, see FE-3b |
| `--text-disabled` | `#C2C2C8` | `#BBC2D0` | |
| `--control-fill` | `#F1F1F3` | `#EBEEF5` | |
| `--row-hover` | `#FAFAFB` | `#F7F8FC` | |

**Card lift, measured: `1.08:1` today, `1.17:1` new.** The first value tried was `#EFF1F7`,
which measured `1.13:1` and, rendered and sampled rather than judged by eye, still left 23% of
the page as pure white card and read barely different from today. `#E8EDF7` is one step further:
15 points of blue over red against today's 1, and `--text-muted` still clears 4.5:1 on it at
`4.88:1`. The pixel sampling is in section 7.

### The chromatic tokens

| Token | Now | New | Why |
|---|---|---|---|
| `--accent` | `#5B5BD6` | `#5B5BD6` | unchanged. The whole neutral ramp now points at it |
| `--accent-hover` | `#4F4FC9` | `#4F4FC9` | unchanged |
| `--accent-text` | `#3E3EA8` | `#3E3EA8` | unchanged |
| `--accent-wash` | `#EEEEFB` | `#ECECFA` | |
| `--navy` | none | `#243352` | **new.** Section header bands and the deep end of the ramp |
| `--navy-wash` | none | `#E8ECF5` | **new.** The tint behind a section header |
| `--caution` | `#A65A1E` | `#A65A1E` | unchanged. The one hue outside the family, see 0.3 |
| `--caution-text` | `#8A4A18` | `#8A4A18` | unchanged |
| `--caution-wash` | `#FDFCFB` | `#FAF0E2` | **a fix, not a preference.** See below |
| `--positive` | `#3D9A50` | `#3D9A50` | unchanged. Dot fills only |
| `--positive-text` | `#2F7A3F` inline | `#2B7038` | promoted to a token, and darkened to clear 4.5:1 everywhere |
| `--positive-wash` | `#EAF5EC` | `#E9F2EC` | cooled to sit on the new ground |

**Why `--caution-wash` had to move.** `#FDFCFB` is 99% white. It was already close to invisible
on a white page, and on tinted paper it would have read as a *lighter* patch than its
surroundings, which inverts the signal: the one band a person is meant to stop on would have
been the one band that receded. `#FAF0E2` is a visible amber tint that still sits under
`#8A4A18` text at 6.06:1.

### Surfaces get an identity

Each Overview section carries a colour on its icon chip and a 3px left rule on its panel, and
the matching tile above carries the same rule.

| Section | Colour | Condition |
|---|---|---|
| Awaiting approval | `--accent` | always |
| Approved by the system | `--positive` | always |
| Outbox | `--caution` if any row is `Failed`, otherwise `--border-hover` | **conditional** |
| History | `--border-hover` | always |

Outbox is conditional on purpose. A permanently amber Outbox header would claim a problem on
the days when nothing has failed, which is most days, and a warning that is always on is not a
warning.

Also in this part: section headers sit on a `--navy-wash` band rather than carrying a bare
bottom rule, and `.tab-note` / `.ov-foot` sit on `--surface-sunk` with a real border instead of
reading as loose text on the page.

### Where each change lands

`.streamlit/config.toml`: `backgroundColor`, `secondaryBackgroundColor`, `textColor`,
`borderColor`. Four values.

`app.py`: the `:root` block, the `--caution-wash` fix, the two new navy tokens, the section
rules, the tinted header band. No new selector reaches into a Streamlit `data-testid` that is
not already used.

---

## 5. Accessibility

Measured, not assumed. Every information-carrying pair, foreground against every background it
can sit on:

```
                 surface  ground    sunk  control  acc-wash navy-wash caut-wash  pos-wash
#161A23 text       17.41   14.83   16.41    14.99    14.88    14.71    15.44    15.24
#3A4152 strong     10.20    8.69    9.62     8.79     8.72     8.62     9.05     8.93
#5F667A muted       5.72    4.88    5.39     4.93     4.89     4.84     5.08     5.01
#3E3EA8 accent      8.54    7.27    8.05     7.35     7.30     7.22     7.57     7.47
#243352 navy       12.57   10.70   11.85    10.82    10.74    10.63    11.15    11.00
#8A4A18 caution     6.83    5.82    6.44     5.88     5.84     5.77     6.06     5.98
#2B7038 positive    6.04    5.15    5.69     5.20     5.16     5.10     5.36     5.28
```

Every pair passes 4.5:1. `--text-muted` is **better** than today's: `4.88:1` on the ground
against `4.68:1` now, because the token darkened by more than the ground did.

`#2F7A3F`, the green that `.verdict-clear` carries inline today, measures `4.46:1` on the new
navy wash, which is the one pair in the whole set that failed. It is darkened to `#2B7038` and
promoted out of the inline style into a token, so the next person who needs green on a tint
finds a value that already passes.

One thing checked and found **not** to be a defect: `--positive` `#3D9A50` measures `3.54:1`,
below 4.5:1. It is never used as text. `grep` confirms it appears only as a dot background and
one SVG stroke. WCAG 2.1 1.4.11 wants 3:1 for non-text UI components, which it passes. No
change.

---

## 6. What this does not do

No layout change. No copy change. No query change. No behaviour change. Nothing moves, nothing
resizes, no section is added or removed, and no number on the screen differs afterwards. If one
does, this change is wrong.

The sidebar theming reaches the mockup only. `app.py` has no sidebar to theme.

`.streamlit/config.toml` applies to every Streamlit app in this directory, so
`app_main_preview.py` changes appearance again, as it did when that file first landed. The
before screenshots for the report were already taken then, so no new capture is owed, but the
report's after screenshots are now stale and will need retaking.

---

## 7. How each part was verified

Run 2026-09-20, results as recorded.

| Part | Check | Result |
|---|---|---|
| Mockup sync | The `<aside>` and `<header>` of v2 diffed against `HEAD` | byte-identical, 7014 and 1252 bytes |
| Mockup sync | v3 diffed against v2 with every hex value stripped | structurally identical, so they differ by colour alone |
| Mockup sync | Every count and amount traced to `workflow_platform.db` | built from it by `scripts/build_design_mockups.py`, not typed |
| Upload | `DesignSync write_files` | 8 written to project `bb7b5e12` |
| Tokens | `AppTest.from_file("app.py").run()` | 0 exceptions |
| Tokens | `./.venv/bin/python -m pytest tests/ -q` | 530 passed, 1 skipped |
| Tokens | Contrast of every information-carrying pair | all at 4.5:1 or better, table in section 5 |
| Tokens | The running app at :8599, screenshotted over CDP and sampled | ground `#E8EDF7`, card `#FFFFFF`, risk strip the only filled chromatic band |
| Tokens | Pixels matching Streamlit's default red | 0, so FE-2 still holds |

**The screenshot is the check that earned its place.** Two things were wrong and neither was
visible in the artboard:

- The first ground, `#EFF1F7`, measured a 1.13:1 card lift and looked almost unchanged when
  rendered and counted: 23% of the page was still pure white. `#E8EDF7` replaced it.
- The section panels' identity rules did not paint at all.
  `[class*="st-key-ovpanel-auto"]` and the base `[class*="st-key-ovpanel-"]` rule have the same
  specificity, and the base rule's `border` shorthand came later in the stylesheet, so it
  repainted the edge. Moving the identity rules after it fixed it. The artboard had been correct
  throughout, which is the point: a mockup cannot catch a cascade bug.

Streamlit renders over a websocket, so `--screenshot` in headless Chrome captures the loading
skeleton. The shot was taken by driving Chrome over the DevTools Protocol instead. The helper is
in the scratch directory, not the repo: it is 30 lines and nothing else needs it yet.

---

## 8. Risks

- **A tinted ground can read as a rendering artefact rather than a choice** if the hue is too
  faint. `#E8EDF7` is a deliberate step past `#F6F6F7`, far enough that the tint is legible next
  to a white card and short of anything that reads as a blue page. The first attempt, `#EFF1F7`,
  was exactly this mistake and was caught by sampling the render rather than by looking at it. If it still reads grey on
  Neo's display, the fix is a larger step in the same direction, not a different idea.
- **Ochre risk on a navy-tinted page is the one hue that has to earn its place every time.** It
  is defensible while it appears on risk alone. The moment a second warm thing arrives, the page
  has two families and no rule, and that is the state this project was in before section 0.1.
- **Four section colours can become four meanings nobody asked for.** The mitigation is the
  filled-background rule in section 4. It is a rule that can be checked in a screenshot, which
  is why it is written that way.
- **The mockup can drift again the moment the next commit lands.** Nothing keeps
  `approval-screen-design-v2.html` honest except this having been done by hand. Worth a line in
  `fe-backlog.md` rather than pretending otherwise.
- **The upload is a first upload, not a sync.** There is nothing in the target project to
  compare against, so `get_file` diffing does not apply and the first `write_files` defines the
  baseline.
