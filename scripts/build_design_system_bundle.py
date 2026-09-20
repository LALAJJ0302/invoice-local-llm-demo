"""Emit the design-system bundle that `DesignSync` uploads to claude.ai/design.

Every file is standalone HTML whose first line is a `<!-- @dsCard ... -->` marker. The Design
System pane builds its own index from those markers, so nothing here needs registering by hand.

The values come from the same place the build does: `app.py`'s `:root` block is the source for
the token card, and `build_design_mockups.py` supplies the component markup so a component
cannot drift between the artboard and the card that documents it.

Run: ./.venv/bin/python scripts/build_design_system_bundle.py
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import build_design_mockups as m  # noqa: E402

OUT = ROOT / "design-system"

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500'
         '&family=IBM+Plex+Sans:wght@400;450;500;600&display=swap" rel="stylesheet">')


def luminance(hex_colour):
    hex_colour = hex_colour.lstrip("#")
    channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    channels = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(a, b):
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def tokens_from_app():
    """Read the live `:root` block rather than restating it. A card that documents a value the
    build no longer holds is worse than no card."""
    css = (ROOT / "app.py").read_text()
    block = css[css.index(":root {"):css.index("}", css.index(":root {"))]
    return re.findall(r"--([a-z-]+):\s*(#[0-9A-Fa-f]{6})", block)


def page(card, title, body, *, width=1100, height=700, ground="#E8EDF7"):
    return (f'<!-- @dsCard group="{card}" -->\n'
            f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{title}</title>{FONTS}'
            f'<style>body{{margin:0;background:{ground};'
            f"font-family:'IBM Plex Sans',system-ui,sans-serif;color:#161A23;"
            f'-webkit-font-smoothing:antialiased;}}'
            f'.wrap{{width:{width}px;padding:28px 30px;box-sizing:border-box;}}'
            f'h2{{font-size:15px;font-weight:600;margin:0 0 4px;}}'
            f'p.note{{font-size:12.5px;color:#5F667A;margin:0 0 20px;max-width:760px;'
            f'line-height:1.6;}}</style></head><body><div class="wrap">{body}'
            f'</div></body></html>')


def swatch(name, value):
    on_white = contrast(value, "#FFFFFF")
    on_ground = contrast(value, "#E8EDF7")
    return (f'<div style="display:flex; align-items:center; gap:12px; padding:9px 12px; '
            f'background:#FFFFFF; border:1px solid #D8DCE8; border-radius:8px;">'
            f'<span style="width:30px; height:30px; border-radius:7px; background:{value}; '
            f'border:1px solid #D8DCE8; flex:none;"></span>'
            f'<span style="display:flex; flex-direction:column; gap:2px; min-width:0;">'
            f'<span style="font-size:12.5px; font-weight:500;">--{name}</span>'
            f"<span style=\"{m.MONO} font-size:11.5px; color:#5F667A;\">{value}</span></span>"
            f'<span style="{m.MONO} margin-left:auto; font-size:11px; color:#5F667A; '
            f'text-align:right; white-space:nowrap;">{on_white:.2f} / {on_ground:.2f}</span></div>')


def tokens_page():
    rows = "".join(swatch(n, v) for n, v in tokens_from_app())
    body = ('<h2>Colour tokens</h2>'
            '<p class="note">Read from the <code>:root</code> block in <code>app.py</code>, so '
            'this card cannot describe a value the build no longer holds. The pair on the right '
            'is the contrast ratio against white and against the ground. Every token that '
            'carries information clears 4.5:1; <code>--text-faint</code> and '
            '<code>--text-disabled</code> do not, and are used on disabled controls only, which '
            'WCAG 2.1 1.4.3 exempts by name.</p>'
            f'<div style="display:grid; grid-template-columns:repeat(3,1fr); gap:8px;">{rows}</div>')
    return page("Colors", "Colour tokens", body, width=1100)


def type_page():
    ramp = [("h1 / page title", "24px", 600), ("h2 / section", "20px", 600),
            ("h3 / card title", "17px", 600), ("body", "14px", 400),
            ("small", "13px", 400), ("caption", "12px", 400)]
    rows = "".join(
        f'<div style="display:flex; align-items:baseline; gap:18px; padding:12px 14px; '
        f'background:#FFFFFF; border:1px solid #D8DCE8; border-radius:8px; margin-bottom:8px;">'
        f'<span style="{m.MONO} font-size:11.5px; color:#5F667A; min-width:118px;">{label}</span>'
        f'<span style="font-size:{size}; font-weight:{weight};">Apex Cloud Solutions Pty Ltd</span>'
        f'<span style="{m.MONO} margin-left:auto; font-size:11.5px; color:#5F667A;">{size} / {weight}</span>'
        f'</div>' for label, size, weight in ramp)
    mono = ('<div style="padding:12px 14px; background:#FFFFFF; border:1px solid #D8DCE8; '
            'border-radius:8px;"><span style="font-size:12.5px; color:#5F667A;">'
            'IBM Plex Mono, tabular figures. Every amount, identifier and timestamp.</span><br>'
            f'<span style="{m.MONO} font-size:22px; font-variant-numeric:tabular-nums;">'
            'USD 1,500.00 &middot; AUD 2,650.00 &middot; INV-2026-001</span></div>')
    body = ('<h2>Type</h2><p class="note">IBM Plex Sans for words, IBM Plex Mono for anything '
            'a person compares down a column. The base is 14px, not Streamlit\'s 16, because '
            'every measured line length in this design was drawn against 14.</p>'
            f'{rows}<div style="height:10px;"></div>{mono}')
    return page("Type", "Type", body, width=980)


def tabs_page():
    bar = "".join([
        m.tab("Overview", None, active=True),
        m.tab("Awaiting approval", 1, count_tone="accent"),
        m.tab("Approved by the system", 2),
        m.tab("Outbox", 17),
        m.tab("History", 0),
    ])
    body = ('<h2>Tab bar</h2><p class="note">The count lives on the label, so every number on '
            'this screen is visible without a click. The active tab takes a 2px accent underline '
            'and a wash-filled chip; the rest take the control fill. Streamlit paints its own red '
            'here by default and <code>primaryColor</code> in <code>config.toml</code> replaces '
            'it.</p>'
            f'<div style="background:#FFFFFF; border:1px solid #D8DCE8; border-radius:10px; '
            f'padding:18px 20px 0;"><nav style="display:flex; gap:22px; '
            f'border-bottom:1px solid #D8DCE8;">{bar}</nav></div>')
    return page("Components", "Tab bar", body, width=980)


def card_page():
    data = m.read_live()
    flagged = m.build_body(data, themed=True)
    card = flagged[flagged.index("<article"):flagged.index("</article>") + 10]
    clear = (f'<article style="{m.PANEL} border-left:3px solid {m.ACCENT};">'
             f'<div style="display:grid; grid-template-columns:44px 1fr auto; gap:16px; '
             f'align-items:start; padding:22px 24px 20px;">'
             f'<div style="width:44px; height:44px; border-radius:10px; background:#F1F1F3; '
             f'border:1px solid #EDEDEF; display:flex; align-items:center; '
             f'justify-content:center;">{m.DOC_ICON}</div>'
             f'<div style="display:flex; flex-direction:column; gap:7px;">'
             f'<div style="display:flex; align-items:center; gap:10px;">'
             f'<span style="font-size:17px; font-weight:600; letter-spacing:-0.01em;">'
             f'Synthetix AI Consulting</span>{m.chip("Invoice", "#A1A1A8")}</div>'
             f'<div style="{m.MONO} font-size:12.5px; color:#6E6E76;">INV-2026-003 &middot; '
             f'issued 2026-08-21</div></div>'
             f'<div style="{m.MONO} font-size:22px; font-weight:500; text-align:right; '
             f'font-variant-numeric:tabular-nums;">USD 2,350.00</div></div>'
             f'<div style="display:flex; align-items:center; gap:12px; padding:12px 24px; '
             f'border-top:1px solid #EDEDEF; background:#FBFBFC;">'
             f'<span style="font-size:12px; font-weight:500; border-radius:20px; '
             f'padding:3px 10px; color:#2F7A3F; background:#EAF5EC;">Nothing flagged</span>'
             f'<span style="font-size:13px; color:#6E6E76;">Our checks read the document itself, '
             f'so this says the numbers hang together, not that the document is genuine.</span>'
             f'</div></article>')
    body = ('<h2>Document card</h2><p class="note">A card rather than a table row, because the '
            'real queue holds one document and one row in a wide table reads as a loading error. '
            'The risk strip is the only filled band on the screen: the verdict leads, then what '
            'was found, then why. Its sentence comes from <code>review_signals.py</code> and is '
            'never written into a card template. The two provenance chips say whether the model '
            'read a value or our own code recovered it after the model failed to.</p>'
            f'<div style="display:flex; flex-direction:column; gap:16px;">{card}{clear}</div>')
    return page("Components", "Document card", body, width=1130)


def rows_page():
    rows = (m.dense_row("#3D9A50", "NextGen Hardware Supplies", "INV-2026-002", "AUD 2,650.00",
                        "score 1.00 &middot; 04:13", m.button("Open", "secondary")) +
            m.dense_row("#3D9A50", "Synthetix AI Consulting", "INV-2026-003", "USD 2,350.00",
                        "score 1.00 &middot; 04:13", m.button("Open", "secondary"), last=True))
    out = (m.dense_row("#A65A1E", "NextGen Hardware Supplies", "INV-2026-002", "Failed",
                       "2026-09-19 04:13", m.button("Push to Jira", "secondary"), last=True))
    body = ('<h2>Dense rows</h2><p class="note">One line per record, for the sections that '
            'summarise rather than ask for a decision. Each row carries its own currency symbol '
            'and no total is drawn beneath them, because the documents in these sections do not '
            'share one. The panel takes its section\'s identity rule; the row takes a state dot.'
            '</p>'
            f'<div style="{m.PANEL} border-left:3px solid #3D9A50; margin-bottom:16px;">{rows}</div>'
            f'<div style="{m.PANEL} border-left:3px solid #A65A1E;">{out}</div>')
    return page("Components", "Dense rows", body, width=1100)


def empty_page():
    empty = ('<div style="display:flex; flex-direction:column; align-items:center; gap:9px; '
             'padding:28px 24px; background:#FFFFFF; border:1px solid #D8DCE8; '
             'border-radius:12px;">'
             '<span style="width:42px; height:42px; border-radius:50%; background:#EAF5EC; '
             'display:flex; align-items:center; justify-content:center;">'
             "<svg width='20' height='20' viewBox='0 0 16 16' fill='none'>"
             "<path d='m3.6 8.3 2.9 2.9 5.9-6.1' stroke='#3D9A50' stroke-width='1.6' "
             "stroke-linecap='round' stroke-linejoin='round'/></svg></span>"
             '<span style="font-size:15px; font-weight:600;">Nothing is waiting for you</span>'
             '<span style="font-size:13px; color:#5F667A;">Every document the pipeline has read '
             'has been decided.</span></div>')
    body = ('<h2>Empty state</h2><p class="note">The real queue holds one document, so it is '
            'empty most of the time. An approval tool that reads as broken when there is nothing '
            'to approve is a failure. This has to read as finished.</p>' + empty)
    return page("Components", "Empty state", body, width=860)


def buttons_page():
    row = "".join(m.button(label, kind) + "&nbsp;&nbsp;" for label, kind in [
        ("Review document", "primary"), ("Download the original", "secondary"),
        ("Push to Jira", "disabled")])
    focus = ('<span style="display:inline-flex; align-items:center; height:32px; padding:0 14px; '
             'border-radius:8px; font-size:13px; background:#5B5BD6; border:1px solid #4F4FC9; '
             'color:#FFFFFF; box-shadow:0 0 0 3px rgba(91,91,214,0.16);">Review document</span>')
    body = ('<h2>Buttons</h2><p class="note">Primary is the decision, secondary is everything '
            'reversible, disabled carries its reason in a tooltip rather than being hidden. The '
            'focus ring is <code>:focus-visible</code>, not <code>:focus</code>, so a mouse click '
            'does not leave one behind.</p>'
            f'<div style="background:#FFFFFF; border:1px solid #D8DCE8; border-radius:10px; '
            f'padding:22px 24px; margin-bottom:14px;">{row}</div>'
            f'<div style="background:#FFFFFF; border:1px solid #D8DCE8; border-radius:10px; '
            f'padding:22px 24px;"><span style="font-size:12.5px; color:#5F667A; '
            f'margin-right:16px;">focus-visible</span>{focus}</div>')
    return page("Components", "Buttons", body, width=860)


def screen_page():
    html = (ROOT / "approval-screen-design-v3.html").read_text()
    return '<!-- @dsCard group="Screens" -->\n' + html


def dialog_page():
    html = (ROOT / "review-dialog-design.html").read_text()
    return '<!-- @dsCard group="Screens" -->\n' + html


FILES = {
    "foundations/tokens.html": tokens_page,
    "foundations/type.html": type_page,
    "components/tabs.html": tabs_page,
    "components/document-card.html": card_page,
    "components/dense-rows.html": rows_page,
    "components/empty-state.html": empty_page,
    "components/buttons.html": buttons_page,
    "screens/approval-queue.html": screen_page,
    "screens/review-dialog.html": dialog_page,
}


def index_page():
    """A local index of every card.

    The Design System pane on claude.ai builds its own index from the `@dsCard` markers, and an
    API upload does not appear to trigger whatever compiles it. That is outside this repo's
    control; this is not. Nine cards on disk, openable with a static file server, so the work
    can be looked at whatever the hosted pane is doing.
    """
    cards = "".join(
        f'<a href="{path}" style="display:block; background:#FFFFFF; border-radius:12px; '
        f'box-shadow:0 1px 2px rgba(22,26,35,0.06), 0 8px 20px rgba(22,26,35,0.07); '
        f'padding:16px 18px; text-decoration:none; color:#161A23;">'
        f'<div style="font-size:14px; font-weight:600;">{path.split("/")[-1][:-5]}</div>'
        f"<div style=\"font-family:'IBM Plex Mono',monospace; font-size:11.5px; "
        f'color:#5F667A; margin-top:4px;">{path}</div></a>'
        for path in FILES)
    return (f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Design system</title>'
            f'{FONTS}<style>body{{margin:0;background:#E8EDF7;'
            f"font-family:'IBM Plex Sans',system-ui,sans-serif;color:#161A23;padding:34px 36px;}}"
            f'</style></head><body><h1 style="font-size:20px; margin:0 0 4px;">'
            f'Invoice approvals design system</h1>'
            f'<p style="font-size:13px; color:#5F667A; margin:0 0 22px;">'
            f'Generated by scripts/build_design_system_bundle.py. The same files are uploaded to '
            f'the Claude Design project.</p>'
            f'<div style="display:grid; grid-template-columns:repeat(3,1fr); gap:12px;">'
            f'{cards}</div></body></html>')


def main():
    FILES["index.html"] = index_page
    for path, builder in FILES.items():
        target = OUT / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(builder())
        print(f"wrote design-system/{path}")


if __name__ == "__main__":
    main()
