# Spec: the design system reaches Streamlit

**Covers FE-1, FE-2, FE-3 and FE-3b from `fe-backlog.md`, which is all of group A.**
Status: proposed, awaiting Neo's approval. Nothing in `app.py` has been changed.

Sources of truth this implements: `approval-screen-components.html` for the tokens,
`fe-screen-spec.md` §7 for the visual constraints.

---

## 1. The finding that changes the approach

`approval-screen-design-brief.md` and the design handoff both say styling reaches this app as
**one injected stylesheet**, and that sentence shaped the whole design brief. It is out of date.

Streamlit 1.62.0 exposes **277 theme options**, read from the installed package:

```
./.venv/bin/python -c "from streamlit import config; \
  print(len([k for k in config._config_options_template if k.startswith('theme.')]))"
```

Among them are `primaryColor`, `backgroundColor`, `secondaryBackgroundColor`, `textColor`,
`borderColor`, `linkColor`, `baseFontSize`, `baseRadius`, `buttonRadius`, `showWidgetBorder`,
`headingFontSizes`, and `font` / `codeFont`, which accept a custom family loaded from a URL.
There is also a `[theme.sidebar]` section that overrides any of them for the sidebar alone.

**So most of the token layer is configuration, not CSS.** That matters beyond tidiness: config
is a supported interface, while CSS that targets `data-testid` attributes is reaching into
Streamlit's internals and breaks on upgrade. The less of the design that depends on those
selectors, the longer it survives.

The rule this spec follows: **anything the theme can express goes in `config.toml`; CSS covers
only what it cannot.**

---

## 2. What goes where

| Token or behaviour | Carried by |
|---|---|
| ground, surface, sidebar surface, text, border, accent, link | `config.toml` |
| IBM Plex Sans and IBM Plex Mono | `config.toml`, `font` and `codeFont` by URL |
| radius 8px on elements and buttons | `config.toml` |
| base font size, heading sizes | `config.toml` |
| the red accent on tabs and primary buttons | `config.toml`, `primaryColor` |
| tab count chips, the wash-filled pill | CSS |
| focus ring to the exact C6 spec | CSS |
| hover elevation on cards | CSS, group B |
| `text-faint` audit | Python, it is a usage change not a colour change |

---

## 3. `.streamlit/config.toml`, new file

```toml
[theme]
base = "light"
primaryColor = "#5B5BD6"
backgroundColor = "#F6F6F7"
secondaryBackgroundColor = "#FFFFFF"
textColor = "#1C1C1F"
borderColor = "#E1E1E4"
linkColor = "#5B5BD6"
baseFontSize = 14
baseRadius = "8px"
buttonRadius = "8px"
showWidgetBorder = true
font = "IBM Plex Sans:https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&display=swap, sans-serif"
codeFont = "IBM Plex Mono:https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&display=swap, monospace"
headingFontSizes = ["1.5rem", "1.25rem", "1.0625rem", "1rem", "0.9375rem", "0.875rem"]

[theme.sidebar]
backgroundColor = "#FBFBFC"
```

Two of these need their reasoning stated because they are not obvious.

**`baseFontSize = 14`.** Streamlit's default is 16 and every size in the design was drawn
against a 14px body. Leaving it at 16 would scale the whole interface up by 14% and none of the
measured line lengths would hold.

**`headingFontSizes` is far smaller than the default.** Streamlit ships h1 at 2.75rem, which is
38.5px at this base size. The largest heading in the design is 17px. This is an internal tool,
not a landing page, and a 38px heading on a screen holding one document is the same mistake
round 1 made in the other direction.

**The heading sizes are a proposal and the screenshot decides.** They are the one part of this
file not derived from a measured value, because the design draws its headings inside cards
rather than as Streamlit headings.

---

## 4. The CSS layer

Stays where it is, at [app.py:232-239](app.py#L232-L239), replacing the two placeholder rules.
It carries only what section 2 assigns to it.

Selectors are confirmed to exist in the installed frontend bundle: `stTabs`, `stDialog`,
`stExpander`, `stMetric`, `stSidebar`, `stHeading`, and buttons as `stBaseButton-<kind>` where
kind is `primary`, `secondary` or `tertiary`.

The CSS declares the 22 tokens as custom properties on `:root` even though `config.toml` already
sets most of them. The duplication is deliberate: group B builds cards, strips and chips out of
`st.markdown`, and those need the tokens by name.

```css
:root {
  --ground:#F6F6F7;  --surface:#FFFFFF;  --surface-sunk:#FBFBFC;
  --border-subtle:#EDEDEF;  --border:#E1E1E4;  --border-hover:#C9C9CF;
  --text:#1C1C1F;  --text-strong:#3F3F46;  --text-muted:#6E6E76;
  --text-faint:#A1A1A8;  --text-disabled:#C2C2C8;
  --accent:#5B5BD6;  --accent-hover:#4F4FC9;  --accent-text:#3E3EA8;  --accent-wash:#EEEEFB;
  --caution:#A65A1E;  --caution-text:#8A4A18;  --caution-wash:#FDFCFB;
  --positive:#3D9A50;  --positive-wash:#EAF5EC;
  --control-fill:#F1F1F3;  --row-hover:#FAFAFB;
}
```

Plus three rules: the tab count chip, the focus ring, and tabular figures on anything monospace.

---

## 5. FE-3b, the accessibility fix

`text-faint` `#A1A1A8` measures 2.57:1 on white. WCAG 2.1 AA wants 4.5:1 for body text.

Darkening it until it passes lands on `#6E6E76`, which is already `text-muted`, so this is not a
new colour. **The token is fine; the usage is wrong.** Every use that carries information moves
to `text-muted`. `text-faint` survives only on disabled controls, which WCAG 2.1 1.4.3 exempts
by name.

In scope for this change: the 15 uses in `approval-screen-design-v2.html` and the 3 in
`approval-screen-components.html`, so that the design files stop teaching the wrong thing before
group B starts copying from them.

`text-disabled` `#C2C2C8` on `#F1F1F3` measures 1.57:1 and is left alone, under the same
exemption.

---

## 6. The consequence nobody asked about

`.streamlit/config.toml` applies to **every** Streamlit app run from this directory, which
includes `app_main_preview.py`, the pre-redesign dashboard currently running on :8501. The
moment this file lands, the old dashboard stops looking like the old dashboard.

The report needs a believable before and after. **So the before screenshots are captured and
committed before this file is created**, not after. That is a step in this change, not a
follow-up.

---

## 7. How each item is verified

| id | Verified by |
|---|---|
| FE-1 | `AppTest.from_file("app.py").run()` returns 0 exceptions, and a screenshot shows Plex rather than the system sans |
| FE-2 | No pixel matching Streamlit's red in a screenshot of the running app, checked by sampling the image rather than by eye |
| FE-3 | Focus ring visible on every focusable control |
| FE-3b | The contrast script reports every information-carrying pair at 4.5:1 or better, and `grep -c "color:#A1A1A8"` over the two design files returns only the disabled-control uses |

---

## 8. What this change does not do

No cards, no summary tiles, no risk strip, no sidebar navigation. Those are group B and E and
they need the token layer to exist first. If this change is done right the app will look
plainer than it does now in places, because Streamlit's defaults are louder than our tokens.
That is the expected outcome, not a regression.

No change to any query, any table, or any behaviour. If a single number on the screen changes,
this change is wrong.

---

## 9. Risks

- **`font` by URL may not load offline.** This project's argument is that it runs locally with
  no cloud dependency, and a Google Fonts URL is a network call. The fallback in the string is
  `sans-serif`, so the app degrades rather than breaks, but if offline operation matters the
  fonts should be vendored with `server.enableStaticServing`. Worth a decision, not a blocker.
- **`headingFontSizes` is a proposal.** See section 3.
- **Docker.** `.streamlit/config.toml` is inside the build context, so the container picks it up
  with no change to the `Dockerfile`. Confirmed by reading it; not yet run.
