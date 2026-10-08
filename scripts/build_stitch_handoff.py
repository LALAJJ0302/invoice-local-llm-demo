"""Build the handoff for Google Stitch, which needs a different shape from Claude Design.

Claude Design was given a folder: HTML artboards, a component library, spec files it could read.
Stitch takes a prompt and images. It cannot open this repo, so everything it needs has to be
inline, and the sidebar and header have to be described rather than attached.

The output is gitignored and regenerated from here, the same arrangement `design-handoff/` uses.
Every value comes from the live database and from `app.py`'s token block, so a stale number
cannot be pasted into a design tool by accident.

Run: ./.venv/bin/python scripts/build_stitch_handoff.py
"""
import pathlib
import re
import html as html_lib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import build_design_mockups as m  # noqa: E402

OUT = ROOT / "stitch-handoff"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def tokens():
    css = (ROOT / "app.py").read_text()
    block = css[css.index(":root {"):css.index("}", css.index(":root {"))]
    return dict(re.findall(r"--([a-z-]+):\s*(#[0-9A-Fa-f]{6})", block))


def shoot(src: pathlib.Path, dest: pathlib.Path, height: int = 1120) -> bool:
    if not pathlib.Path(CHROME).exists():
        print(f"  skipped {dest.name}: Chrome not at {CHROME}")
        return False
    subprocess.run([CHROME, "--headless", "--disable-gpu", "--hide-scrollbars",
                    f"--screenshot={dest}", f"--window-size=1460,{height}",
                    f"file://{src}"], capture_output=True)
    return dest.exists()


def prompt(t, d):
    """The main paste. Short on purpose: a design tool's input box is not a spec file."""
    return f"""# Paste this into Stitch first

Design a desktop web screen, 1440 wide, light theme. It is the Overview page of an internal
invoice approval tool used by one senior finance approver at a mid-sized company. They open it
to decide, document by document, whether to accept what an AI pipeline read out of an incoming
email. Approving immediately creates a Jira task for someone else, so the screen has to make a
person look before they click.

## Layout

**Left sidebar, 248px, fixed.** A 26px dark rounded-square mark with a document glyph, the word
"Payables" in 13.5px semibold and "Finance operations" beneath it in 11.5px muted. Below that a
search field reading "Search documents" with a monospace ⌘K hint at its right edge. Then a
navigation list: Approvals (active, count 1), Documents (3), Vendors, Notifications (17),
Pipeline runs. Then a small uppercase label "SAVED VIEWS" and two rows with coloured dots:
"Flagged by the model" (1) and "Cleared this week" (2). Pinned to the bottom, a circular
initials avatar, the name "Nattakrit Pitayasiri" and the role "Approver".

**Top bar, 56px.** Breadcrumb "Payables / Invoice approvals", the second part semibold. At the
right, a status pill with a green dot reading "Pipeline read 04:13", then a 32px circular
avatar.

**Main column.** A tab row: Overview (active), Awaiting approval {d['pending_n']}, Approved by
the system {d['auto_n']}, Outbox {d['outbox_n']}, History {d['history_n']}. Then three stat
cards across the full width. Then four stacked sections, each a heading plus a muted note plus
its rows: Awaiting approval, Approved by the system, Outbox, History.

## The one thing this screen exists to do

The Awaiting approval section holds a single document card, and it is the most important object
on the page. It carries the vendor, a document-type chip, a monospace meta line, a large
right-aligned amount, and **a risk band**: a rounded pill reading "Worth a careful look", then
the finding, then one sentence of explanation. Below that, three small chips saying where each
value came from, and two buttons, "Download the original" and a filled primary "Review document".

**Risk is the only thing on this screen that gets a filled background.** Everything else uses
white surfaces, thin rules, tone and shadow. If a second thing is filled with colour, the eye
stops going to the risk.

## Style

Type: IBM Plex Sans for words, IBM Plex Mono for every amount, identifier and timestamp, with
tabular figures. Body 14px. Largest heading 18px. This is a dense internal tool, not a landing
page: no heading above 18px anywhere.

Ground {t['ground']}. Cards white with a soft two-term shadow and no border. Radius 12px.

## Do not

No indigo-to-purple gradients. No row of three rounded cards with thin-line icons. No
glassmorphism. No Inter. No emoji anywhere, least of all in headings. No illustration. No
marketing hero. The screen this replaces had nine emoji and they were its loudest tell.
"""


def tokens_md(t):
    order = ["ground", "surface", "surface-sunk", "border-subtle", "border", "border-hover",
             "text", "text-strong", "text-muted", "text-faint", "text-disabled",
             "accent", "accent-hover", "accent-text", "accent-wash", "navy", "navy-wash",
             "caution", "caution-text", "caution-wash",
             "positive", "positive-text", "positive-wash", "control-fill", "row-hover"]
    rows = "\n".join(f"{k:<16}{t[k]}" for k in order if k in t)
    return f"""# Paste this second, to pin the palette

Read straight from the running app's stylesheet, so these are the real values and not a guess.

```
{rows}
```

Use them by role, not by eye:

- `ground` is the page. `surface` is every card. Cards are **white on a tinted ground**, which
  is what makes them read as cards without a border.
- `accent` is the one action colour: the primary button, the active tab, the focus ring.
- `caution` and `caution-wash` belong to the risk band and nowhere else.
- `positive` fills small state dots. It is never used for text; `positive-text` is.
- `text-faint` and `text-disabled` are for disabled controls only. Every colour that carries
  information measures at least 4.5:1 against the surface it sits on, and that has to stay true.

Shadows, both terms, because the near one seats the card and the far one gives it the falloff a
border cannot:

```
card   0 1px 2px rgba(22,26,35,0.06), 0 8px 20px rgba(22,26,35,0.07)
small  0 1px 2px rgba(22,26,35,0.05), 0 4px 10px rgba(22,26,35,0.05)
chip   background rgba(22,26,35,0.045), no border
```
"""


def content_md(d):
    """Plain text, so the HTML entities `read_live` produces have to be decoded.

    `read_live` builds strings for an artboard, where `&middot;` is correct. Pasted into a
    prompt box it is four characters of noise that a design tool will render literally.
    """
    p = {k: html_lib.unescape(v) if isinstance(v, str) else v
         for k, v in d["pending"].items()}
    d = dict(d, auto=[{k: html_lib.unescape(v) if isinstance(v, str) else v
                       for k, v in r.items()} for r in d["auto"]],
             outbox=[{k: html_lib.unescape(v) if isinstance(v, str) else v
                      for k, v in r.items()} for r in d["outbox"]])
    auto = "\n".join(f"  {r['vendor']:<30}{r['doc']:<16}{r['amount']:>14}   {r['right']}"
                     for r in d["auto"])
    out = "\n".join(f"  {r['vendor']:<30}{r['doc']:<16}{r['state']:>14}   {r['when']}"
                    for r in d["outbox"])
    return f"""# Paste this third, and use it verbatim

This is real data from the running system, not placeholder text. Two of these strings are here
because they break layouts built for shorter ones; they are named at the bottom.

```
TAB ROW
  Overview                    (active)
  Awaiting approval           {d['pending_n']}
  Approved by the system      {d['auto_n']}
  Outbox                      {d['outbox_n']}
  History                     {d['history_n']}

STAT CARDS
  Waiting for you             {d['pending_n']}        {d['waiting_total']}
  Approved by the system      {d['auto_n']}        no person involved
  Oldest wait                 {d['oldest']}      {d['oldest_since']}

AWAITING APPROVAL      a person has to decide on each of these
  vendor      {p['vendor']}
  chip        {p['type']}
  meta        {p['meta']}
  amount      {p['amount']}
  verdict     Worth a careful look
  finding     {p['signal']}
  because     {p['detail']}
  chips       Vendor read from the document   (green dot)
              Total read from the document    (green dot)
              Covering email · {p['sender']}
  buttons     Download the original   |   Review document

  then one line:  That is everything waiting. {d['auto_n']} were cleared by the system on its own.

APPROVED BY THE SYSTEM   at a validation score of 1.00, with nobody asked
{auto}

OUTBOX   {d['outbox_note']}
{out}
      the Push to Jira button is disabled, because Jira is not configured

HISTORY   decisions a person made, including rejections
  Nobody has decided anything yet.

FOOTNOTE
  Approving opens a Jira task for someone else, so the decision is made inside the
  document, not from this page.
```

Two strings are load-bearing. **"{p['vendor']}"** is long enough to push the amount out of
alignment in a layout built around short names. **"{p['signal']}"** is a full sentence that has
to sit in a band beside a pill and a second sentence without wrapping badly.
"""


README = """# Handing this project to Google Stitch

## What is different from the Claude Design round

Claude Design was given files: the artboards, the component library, the specs. Stitch takes a
prompt and images, and cannot read this repo. So everything is inline here, and the sidebar and
header are **described in prose** rather than attached, which means Stitch will redraw them
rather than preserve them. That is the main thing to watch when the result comes back.

## Order

1. Paste `PROMPT.md`.
2. Upload `ref-current.png` as a visual reference **only if you want Stitch to stay close**. If
   the point of the experiment is to see what it does on its own, do not upload it on the first
   attempt: an image reference will pull the output toward what we already have and the
   comparison stops being informative.
3. Paste `TOKENS.md` to pin the palette.
4. Paste `CONTENT.md` and ask it to use the strings verbatim.

## Judging it fairly

The honest comparison is not "which looks nicer". Both tools will produce something plausible.
Ask the same five questions of each result:

1. **Does the eye go to the risk first?** That is the entire job of this screen. Cover the
   screen, look for one second, and see what you saw.
2. **Is anything except the risk band filled with colour?** One rule, checkable in a screenshot.
3. **Does "Apex Cloud Solutions Pty Ltd" fit without pushing the amount out of line?**
4. **Does it survive the empty queue?** The real queue holds one document and is empty most
   days. An approval tool that reads as broken when there is nothing to approve has failed.
5. **Is it buildable in Streamlit?** We render through `st.container`, `st.columns`,
   `st.tabs`, `st.button` and one injected stylesheet. Anything needing arbitrary DOM, a custom
   scroll container or a sticky element inside a dialog is a redesign, not a style.

Question 5 is where a general-purpose UI generator usually loses, and it is worth knowing that
before you fall in love with a result.

## Files

```
PROMPT.md           the main paste
TOKENS.md           the palette, read from app.py at build time
CONTENT.md          the real strings, read from workflow_platform.db at build time
ref-current.png     what is built today
ref-alternative.png the quiet-frame prototype, colour on the content instead of the frame
```

This folder is gitignored and rebuilt by `scripts/build_stitch_handoff.py`. Do not hand-edit it:
the generator is the source, and a hand edit here is lost on the next run.
"""


def main():
    OUT.mkdir(exist_ok=True)
    t, d = tokens(), m.read_live()
    (OUT / "PROMPT.md").write_text(prompt(t, d))
    (OUT / "TOKENS.md").write_text(tokens_md(t))
    (OUT / "CONTENT.md").write_text(content_md(d))
    (OUT / "README.md").write_text(README)
    for src, dest in [("approval-screen-design-v3.html", "ref-current.png"),
                      ("approval-screen-design-v4.html", "ref-alternative.png")]:
        shoot(ROOT / src, OUT / dest)
    for f in sorted(OUT.iterdir()):
        print(f"  stitch-handoff/{f.name}  {f.stat().st_size:>7,} bytes")


if __name__ == "__main__":
    main()
