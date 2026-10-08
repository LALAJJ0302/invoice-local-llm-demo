"""Artboard v4: craft.do's model applied to our screen, for comparison only.

v3 puts colour on the frame and leaves the content white. craft.do does the opposite: its
sidebar is #F9FBFA with no rules, its selected row is a soft fill with no outline, and every
document card is a saturated pastel. This builds our screen the other way round so the two can
be looked at side by side.

Three changes, and nothing else moves. Same data, same copy, same order, same sizes.

1. The frame goes quiet. Section headers lose the navy-wash band and the identity rule.
2. The content carries the colour. A document that needs attention is a tinted card; a document
   already decided is white. This keeps the rule from fe-theme-v2-spec.md §0.1 rather than
   breaking it: the whole card becomes the risk signal instead of a strip inside it.
3. Separation is tone and shadow, not hairlines. Measured on the two renders, v3 recovers from
   card to ground in one pixel and craft.do takes more than ten.

`app.py` is not touched by this script. It writes one file and nothing else.

Run: ./.venv/bin/python scripts/build_quiet_frame_artboard.py
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import build_design_mockups as m  # noqa: E402

V3 = ROOT / "approval-screen-design-v3.html"
V4 = ROOT / "approval-screen-design-v4.html"

# One shadow, used everywhere, with a near and a far term. The near term seats the card, the far
# term gives it the falloff that a 1px border cannot.
LIFT = "box-shadow:0 1px 2px rgba(22,26,35,0.06), 0 8px 20px rgba(22,26,35,0.07);"
LIFT_SOFT = "box-shadow:0 1px 2px rgba(22,26,35,0.05), 0 4px 10px rgba(22,26,35,0.05);"

SURFACE = "background:#FFFFFF; border-radius:14px; " + LIFT
ATTENTION = "background:#FDF3E6; border-radius:14px; " + LIFT


def tab(label, count, *, active=False):
    """Active is a white pill with a shadow, the way craft.do marks its open document.

    No underline and no filled count chip: both are frame decoration, which is the thing this
    variant is testing the removal of.
    """
    if active:
        box = f"background:#FFFFFF; border-radius:9px; padding:7px 14px; {LIFT_SOFT}"
        tone = "font-weight:600; color:#161A23;"
    else:
        box = "padding:7px 14px;"
        tone = "color:#5F667A;"
    chip = (f"<span style=\"{m.MONO} font-size:12px; color:#8A90A0; margin-left:8px;\">{count}</span>"
            if count is not None else "")
    return (f"<div style=\"display:flex; align-items:center; {box} cursor:pointer;\">"
            f"<span style=\"font-size:13.5px; {tone}\">{label}</span>{chip}</div>")


def tile(label, value, note):
    return (f"<div style=\"{SURFACE} padding:16px 18px; display:flex; flex-direction:column; "
            f"gap:6px;\"><span style=\"font-size:12px; color:#5F667A;\">{label}</span>"
            f"<div style=\"display:flex; align-items:baseline; gap:9px; flex-wrap:wrap;\">"
            f"<span style=\"{m.MONO} font-size:26px; font-weight:500; letter-spacing:-0.01em; "
            f"color:#161A23;\">{value}</span>"
            f"<span style=\"{m.MONO} font-size:13px; color:#5F667A; "
            f"font-variant-numeric:tabular-nums;\">{note}</span></div></div>")


def section_head(title, note):
    """Bold text and a note. No band, no rule, no icon chip.

    The space above is doing the work the band used to do, which is the whole claim being
    tested here.
    """
    return (f"<div style=\"display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; "
            f"margin:34px 0 12px;\">"
            f"<span style=\"font-size:15px; font-weight:600; color:#161A23; "
            f"letter-spacing:-0.005em;\">{title}</span>"
            f"<span style=\"font-size:12.5px; color:#5F667A;\">{note}</span></div>")


def chip(text, dot):
    """A chip without a border. The tint carries it."""
    mark = (f"<span style=\"width:6px; height:6px; border-radius:50%; background:{dot}; "
            f"flex:none;\"></span>") if dot else ""
    return (f"<span style=\"display:inline-flex; align-items:center; gap:7px; font-size:12px; "
            f"color:#43495A; background:rgba(22,26,35,0.045); border-radius:7px; "
            f"padding:4px 10px; margin-right:8px;\">{mark}{text}</span>")


def button(label, kind):
    styles = {
        "primary": f"background:#5B5BD6; color:#FFFFFF; {LIFT_SOFT}",
        "secondary": "background:rgba(22,26,35,0.05); color:#161A23;",
        "disabled": "background:rgba(22,26,35,0.035); color:#A4AAB8;",
    }[kind]
    return (f"<span style=\"display:inline-flex; align-items:center; height:34px; padding:0 15px; "
            f"border-radius:9px; font-size:13px; white-space:nowrap; {styles}\">{label}</span>")


def document_card(p):
    """The flagged document. The card itself is the signal.

    v3 draws a white card with an amber strip inside it. Here the card is amber, so the thing
    the eye lands on is the document rather than a band within it. There is no risk strip and
    no border; the verdict sits inline with the finding.
    """
    return (
        f"<article style=\"{ATTENTION} padding:22px 24px 18px;\">"
        f"<div style=\"display:grid; grid-template-columns:1fr auto; gap:16px; "
        f"align-items:start;\">"
        f"<div style=\"display:flex; flex-direction:column; gap:7px; min-width:0;\">"
        f"<div style=\"display:flex; align-items:center; gap:10px; flex-wrap:wrap;\">"
        f"<span style=\"font-size:18px; font-weight:600; letter-spacing:-0.01em; "
        f"color:#161A23;\">{p['vendor']}</span>"
        f"<span style=\"font-size:11.5px; color:#6B6152; background:rgba(138,74,24,0.09); "
        f"border-radius:20px; padding:2px 9px;\">{p['type']}</span></div>"
        f"<div style=\"{m.MONO} font-size:12.5px; color:#7A7160;\">{p['meta']}</div></div>"
        f"<div style=\"{m.MONO} font-size:24px; font-weight:500; text-align:right; "
        f"white-space:nowrap; color:#161A23; font-variant-numeric:tabular-nums;\">{p['amount']}</div>"
        f"</div>"

        f"<div style=\"margin:16px 0 0; padding:0; display:flex; align-items:baseline; gap:10px; "
        f"flex-wrap:wrap;\">"
        f"<span style=\"font-size:14px; font-weight:600; color:#8A4A18;\">Worth a careful look</span>"
        f"<span style=\"font-size:14px; color:#8A4A18;\">{p['signal']}</span></div>"
        f"<div style=\"font-size:13px; color:#7A7160; margin-top:4px; max-width:820px;\">"
        f"{p['detail']}</div>"

        f"<div style=\"display:flex; align-items:center; gap:12px; margin-top:18px; "
        f"flex-wrap:wrap; row-gap:8px;\">"
        f"<div style=\"display:flex; align-items:center; flex-wrap:wrap; row-gap:6px;\">"
        f"{chip('Vendor read from the document', '#3D9A50')}"
        f"{chip('Total read from the document', '#3D9A50')}"
        f"{chip('Covering email &middot; ' + p['sender'], None)}</div>"
        f"<div style=\"margin-left:auto; display:flex; align-items:center; gap:10px;\">"
        f"{button('Download the original', 'secondary')}{button('Review document', 'primary')}"
        f"</div></div></article>")


def row(dot, left, doc, value, right, action, last=False):
    """No hairline. The gap is the separator."""
    edge = "" if last else "margin-bottom:2px;"
    return (f"<div style=\"display:grid; grid-template-columns:auto 1fr auto auto auto auto; "
            f"align-items:center; gap:16px; padding:13px 18px; border-radius:10px; {edge}\">"
            f"<span style=\"width:6px; height:6px; border-radius:50%; background:{dot}; flex:none;\"></span>"
            f"<span style=\"font-size:13.5px; color:#161A23;\">{left}</span>"
            f"<span style=\"{m.MONO} font-size:12.5px; color:#5F667A;\">{doc}</span>"
            f"<span style=\"{m.MONO} font-size:13px; font-variant-numeric:tabular-nums; "
            f"min-width:112px; text-align:right; color:#161A23;\">{value}</span>"
            f"<span style=\"{m.MONO} font-size:12px; color:#5F667A; text-align:right;\">{right}</span>"
            f"{action}</div>")


def build_body(d):
    tabs = "".join([
        tab("Overview", None, active=True),
        tab("Awaiting approval", d["pending_n"]),
        tab("Approved by the system", d["auto_n"]),
        tab("Outbox", d["outbox_n"]),
        tab("History", d["history_n"]),
    ])
    tiles = "".join([
        tile("Waiting for you", d["pending_n"], d["waiting_total"]),
        tile("Approved by the system", d["auto_n"], "no person involved"),
        tile("Oldest wait", d["oldest"], d["oldest_since"]),
    ])
    auto = "".join(row("#3D9A50", r["vendor"], r["doc"], r["amount"], r["right"],
                       button("Open", "secondary"), last=(i == len(d["auto"]) - 1))
                   for i, r in enumerate(d["auto"]))
    out = "".join(row("#A4AAB8", r["vendor"], r["doc"], r["state"], r["when"],
                      button("Push to Jira", "disabled"), last=(i == len(d["outbox"]) - 1))
                  for i, r in enumerate(d["outbox"]))

    return f"""
    <div style="flex:1; padding:22px 30px 40px; display:flex; flex-direction:column; min-width:0;">

      <nav style="display:flex; gap:4px; margin:0 -8px 24px;">
{tabs}
      </nav>

      <div style="display:grid; grid-template-columns:repeat(3,1fr); gap:14px;">
{tiles}
      </div>

{section_head("Awaiting approval", "a person has to decide on each of these")}
{document_card(d["pending"])}

      <div style="display:flex; align-items:center; gap:10px; margin:16px 0 0; font-size:13px;
                  color:#43495A;">
        <span style="width:6px; height:6px; border-radius:50%; background:#3D9A50;"></span>
        That is everything waiting. {d["auto_n"]} were cleared by the system on its own.
      </div>

{section_head("Approved by the system", "at a validation score of 1.00, with nobody asked")}
      <div style="{SURFACE} padding:6px;">{auto}</div>

{section_head("Outbox", d["outbox_note"])}
      <div style="{SURFACE} padding:6px;">{out}</div>

{section_head("History", "decisions a person made, including rejections")}
      <p style="font-size:13px; color:#5F667A; margin:0;">Nobody has decided anything yet.</p>

      <p style="font-size:12.5px; color:#5F667A; margin:26px 0 0; padding:14px 18px;
                border-radius:12px; background:rgba(22,26,35,0.035);">
        Approving opens a Jira task for someone else, so the decision is made inside the
        document, not from this page.</p>
    </div>
"""


def quiet_chrome(html):
    """The aside and the header lose their dividing rules too, or the test is only half run.

    Structure, copy and spacing are untouched. Only the two 1px rules that separate the sidebar
    and the header from the canvas are replaced by the tone step that is already there.
    """
    return (html
            .replace("border-right:1px solid #D8DCE8;", "")
            .replace("border-bottom:1px solid #D8DCE8;", ""))


def main():
    data = m.read_live()
    html = V3.read_text()
    head_end = html.index("</header>") + len("</header>")
    main_end = html.index("</main>")
    out = quiet_chrome(html[:head_end]) + "\n" + build_body(data) + "\n  " + html[main_end:]
    out = out.replace("<title>Approval queue, tinted surfaces</title>",
                      "<title>Approval queue, quiet frame</title>")
    V4.write_text(out)
    print(f"wrote {V4.name}")


if __name__ == "__main__":
    main()
