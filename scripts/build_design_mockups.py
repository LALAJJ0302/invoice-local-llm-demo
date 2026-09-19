"""Rebuild the two artboard files from the live database.

`approval-screen-design-v2.html` is the Claude Design output. Its chrome, the aside and the
header, is the standing design of what has not been built yet and is never touched here: this
script splices a new body between `</header>` and `</main>` and leaves every other byte alone.

`approval-screen-design-v3.html` is v2 with the token map of fe-theme-v2-spec.md §4 applied to
the whole artboard, chrome included. A themed body beside an unthemed sidebar would be useless
as a reference, so v3 recolours the chrome while preserving it exactly as drawn.

Run: ./.venv/bin/python scripts/build_design_mockups.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

ROOT = pathlib.Path(__file__).resolve().parent.parent
V2 = ROOT / "approval-screen-design-v2.html"
V3 = ROOT / "approval-screen-design-v3.html"

# Old value -> new value. Anything absent from this map is identical in both artboards, which
# is every chromatic token except the two washes. fe-theme-v2-spec.md §4 is the source.
TOKEN_MAP = {
    "#F6F6F7": "#E8EDF7",   # ground
    "#FBFBFC": "#F7F8FC",   # surface sunk
    "#FAFAFB": "#F7F8FC",   # row hover
    "#EDEDEF": "#E6E9F2",   # border subtle
    "#E9E9EC": "#E6E9F2",   # border subtle, a spelling that predates the token
    "#E1E1E4": "#D8DCE8",   # border
    "#C9C9CF": "#B9C0D1",   # border hover
    "#D4D4D8": "#C4CAD8",   # the breadcrumb slash
    "#ECECEE": "#DDE3EF",   # the page behind the artboard
    "#1C1C1F": "#161A23",   # text
    "#3F3F46": "#3A4152",   # text strong
    "#6E6E76": "#5F667A",   # text muted
    "#A1A1A8": "#98A0B3",   # text faint
    "#C2C2C8": "#BBC2D0",   # text disabled
    "#F1F1F3": "#EBEEF5",   # control fill
    "#EEEEFB": "#ECECFA",   # accent wash
    "#FDFCFB": "#FAF0E2",   # caution wash. The one value that had to move, see §4
    "#EAF5EC": "#E9F2EC",   # positive wash
    "#2F7A3F": "#2B7038",   # positive text
}

DOC_ICON = ("<svg width='20' height='20' viewBox='0 0 16 16' fill='none'>"
            "<path d='M4 2.2h5l3 3v8.6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V3.2a1 1 0 0 1 1-1Z' "
            "stroke='#3F3F46' stroke-width='1.3' stroke-linejoin='round'/>"
            "<path d='M8.7 2.2v3.2H12' stroke='#3F3F46' stroke-width='1.3' stroke-linejoin='round'/>"
            "<path d='M5.4 8.6h5.2M5.4 10.8h3.4' stroke='#6E6E76' stroke-width='1.3' "
            "stroke-linecap='round'/></svg>")

SECTION_ICONS = {
    "awaiting": "<path d='M2.5 9.5h3l1 1.75h3l1-1.75h3' stroke='currentColor' stroke-width='1.4' "
                "stroke-linecap='round' stroke-linejoin='round'/><path d='M3.6 3.2h8.8l1.1 6.3v2.8"
                "a1 1 0 0 1-1 1H3.5a1 1 0 0 1-1-1V9.5l1.1-6.3Z' stroke='currentColor' "
                "stroke-width='1.4' stroke-linejoin='round'/>",
    "auto": "<path d='m3.6 8.3 2.9 2.9 5.9-6.1' stroke='currentColor' stroke-width='1.6' "
            "stroke-linecap='round' stroke-linejoin='round'/>",
    "outbox": "<path d='M2.6 8h3.2l1 1.8h2.4l1-1.8h3.2' stroke='currentColor' stroke-width='1.4' "
              "stroke-linecap='round' stroke-linejoin='round'/><path d='M8 2.6v6.2M5.6 6.4 8 8.8l"
              "2.4-2.4' stroke='currentColor' stroke-width='1.4' stroke-linecap='round' "
              "stroke-linejoin='round'/>",
    "history": "<circle cx='8' cy='8' r='5.4' stroke='currentColor' stroke-width='1.4'/>"
               "<path d='M8 4.8V8l2.2 1.4' stroke='currentColor' stroke-width='1.4' "
               "stroke-linecap='round' stroke-linejoin='round'/>",
}

ACCENT, POSITIVE, CAUTION, NEUTRAL = "#5B5BD6", "#3D9A50", "#A65A1E", "#C9C9CF"
MONO = "font-family:'IBM Plex Mono',monospace;"
PANEL = ("background:#FFFFFF; border:1px solid #E1E1E4; border-radius:12px; "
         "box-shadow:0 1px 2px rgba(24,24,28,0.04); overflow:hidden;")


def tab(label, count, *, active=False, count_tone="muted"):
    chip = ""
    if count is not None:
        colour, bg = ("#3E3EA8", "#EEEEFB") if count_tone == "accent" else ("#6E6E76", "#F1F1F3")
        chip = (f"<span style=\"{MONO} font-size:11.5px; color:{colour}; background:{bg}; "
                f"border-radius:20px; padding:1px 7px;\">{count}</span>")
    edge = "border-bottom:2px solid #5B5BD6; margin-bottom:-1px;" if active else ""
    weight = "font-weight:500; color:#1C1C1F;" if active else "color:#6E6E76;"
    return (f"<div style=\"display:flex; align-items:center; gap:8px; padding:0 2px 11px; "
            f"{edge} cursor:pointer;\"><span style=\"font-size:13.5px; {weight}\">{label}</span>"
            f"{chip}</div>")


def tile(label, value, note, rule):
    # The tiles carried left rules before this change too, so they are the one piece of
    # section identity that is not new and is drawn the same way in both artboards.
    surface = "#FFFFFF" if rule == "#5B5BD6" else "#FBFBFC"
    return (f"<div style=\"background:{surface}; border:1px solid #E1E1E4; "
            f"border-left:3px solid {rule}; border-radius:10px; "
            f"box-shadow:0 1px 2px rgba(24,24,28,0.04); padding:14px 16px; display:flex; "
            f"flex-direction:column; gap:6px;\">"
            f"<span style=\"font-size:12px; color:#6E6E76;\">{label}</span>"
            f"<div style=\"display:flex; align-items:baseline; gap:9px; flex-wrap:wrap;\">"
            f"<span style=\"{MONO} font-size:26px; font-weight:500; letter-spacing:-0.01em;\">{value}</span>"
            f"<span style=\"{MONO} font-size:13px; color:#6E6E76; font-variant-numeric:tabular-nums;\">{note}</span>"
            f"</div></div>")


def section_head(key, title, note, rule, chip_bg="#F1F1F3", chip_fg="#6E6E76", *, themed=True):
    """The header band, which is the visible half of section identity.

    Before this change every section header was the same: a bare bottom rule and a grey icon
    chip. `themed=False` draws that, so the two artboards differ by the theme and nothing else.
    """
    icon = SECTION_ICONS[key]
    if not themed:
        chip_bg, chip_fg = "#F1F1F3", "#6E6E76"
        frame = "margin:18px 0 8px; padding-bottom:5px; border-bottom:1px solid #E1E1E4;"
    else:
        # These four numbers are pinned to app.py by tests/test_artboard_matches_app.py.
        # They drifted once, within an hour of the artboard being called a true picture.
        frame = ("margin:20px 0 10px; padding:8px 14px; background:#E8ECF5; "
                 f"border:1px solid #EDEDEF; border-left:3px solid {rule}; border-radius:6px;")
    return (f"<div style=\"display:flex; align-items:center; gap:10px; flex-wrap:wrap; {frame}\">"
            f"<span style=\"width:24px; height:24px; border-radius:7px; background:{chip_bg}; "
            f"color:{chip_fg}; display:inline-flex; align-items:center; justify-content:center; "
            f"flex:none;\"><svg width='15' height='15' viewBox='0 0 16 16' fill='none'>{icon}</svg></span>"
            f"<span style=\"font-size:14px; font-weight:600; color:#1C1C1F;\">{title}</span>"
            f"<span style=\"font-size:12.5px; color:#6E6E76;\">{note}</span></div>")


def chip(text, dot):
    mark = (f"<span style=\"width:6px; height:6px; border-radius:50%; background:{dot}; "
            f"flex:none;\"></span>") if dot else ""
    return (f"<span style=\"display:inline-flex; align-items:center; gap:7px; font-size:12px; "
            f"color:#3F3F46; background:#FFFFFF; border:1px solid #E1E1E4; border-radius:7px; "
            f"padding:3px 9px; margin-right:8px;\">{mark}{text}</span>")


def button(label, kind):
    styles = {
        "primary": "background:#5B5BD6; border:1px solid #4F4FC9; color:#FFFFFF; cursor:pointer;",
        "secondary": "background:#FFFFFF; border:1px solid #C9C9CF; color:#1C1C1F; cursor:pointer;",
        "disabled": "background:#F6F6F7; border:1px solid #E9E9EC; color:#C2C2C8; cursor:not-allowed;",
    }[kind]
    return (f"<span style=\"display:inline-flex; align-items:center; height:32px; padding:0 14px; "
            f"border-radius:8px; font-size:13px; white-space:nowrap; {styles}\">{label}</span>")


def dense_row(dot, left, doc, value, right, action, last=False):
    border = "" if last else "border-bottom:1px solid #EDEDEF;"
    return (f"<div style=\"display:grid; grid-template-columns:auto 1fr auto auto auto auto; "
            f"align-items:center; gap:16px; padding:10px 14px; {border}\">"
            f"<span style=\"width:6px; height:6px; border-radius:50%; background:{dot}; flex:none;\"></span>"
            f"<span style=\"font-size:13.5px; color:#1C1C1F;\">{left}</span>"
            f"<span style=\"{MONO} font-size:12.5px; color:#6E6E76;\">{doc}</span>"
            f"<span style=\"{MONO} font-size:13px; font-variant-numeric:tabular-nums; "
            f"min-width:112px; text-align:right; color:#1C1C1F;\">{value}</span>"
            f"<span style=\"{MONO} font-size:12px; color:#6E6E76; text-align:right;\">{right}</span>"
            f"{action}</div>")


def build_body(d, *, themed=True):
    """The Overview page, which is what app.py opens on. Every value comes from `d`.

    `themed` selects between the artboard before fe-theme-v2-spec.md and the one after. Only
    the identity rules and the header bands are gated on it; the token values are swapped
    afterwards by TOKEN_MAP, so nothing in here has to know two palettes.
    """
    def rule(colour):
        return f"border-left:3px solid {colour};" if themed else ""

    def head(*args, **kw):
        return section_head(*args, themed=themed, **kw)
    tabs = "".join([
        tab("Overview", None, active=True),
        tab(d["awaiting_label"], d["pending_n"], count_tone="accent"),
        tab("Approved by the system", d["auto_n"]),
        tab("Outbox", d["outbox_n"]),
        tab("History", d["history_n"]),
    ])

    tiles = "".join([
        tile("Waiting for you", d["pending_n"], d["waiting_total"], "#5B5BD6"),
        tile("Approved by the system", d["auto_n"], "no person involved", "#3D9A50"),
        tile("Oldest wait", d["oldest"], d["oldest_since"], "#C9C9CF"),
    ])

    p = d["pending"]
    card = (
        f"<article style=\"{PANEL} {rule(ACCENT)}\">"
        f"<div style=\"display:grid; grid-template-columns:44px 1fr auto; gap:16px; "
        f"align-items:start; padding:22px 24px 20px;\">"
        f"<div style=\"width:44px; height:44px; border-radius:10px; background:#F1F1F3; "
        f"border:1px solid #EDEDEF; display:flex; align-items:center; justify-content:center;\">{DOC_ICON}</div>"
        f"<div style=\"display:flex; flex-direction:column; gap:7px; min-width:0;\">"
        f"<div style=\"display:flex; align-items:center; gap:10px; flex-wrap:wrap;\">"
        f"<span style=\"font-size:17px; font-weight:600; letter-spacing:-0.01em; color:#1C1C1F;\">{p['vendor']}</span>"
        f"{chip(p['type'], '#A1A1A8')}</div>"
        f"<div style=\"{MONO} font-size:12.5px; color:#6E6E76;\">{p['meta']}</div></div>"
        f"<div style=\"{MONO} font-size:22px; font-weight:500; text-align:right; "
        f"white-space:nowrap; color:#1C1C1F; font-variant-numeric:tabular-nums;\">{p['amount']}</div></div>"

        # The risk strip is the only filled band on the page. fe-theme-v2-spec.md §0.1.
        f"<div style=\"display:flex; align-items:center; gap:12px; padding:12px 24px; "
        f"flex-wrap:wrap; border-top:1px solid #EDEDEF; background:#FDFCFB;\">"
        f"<span style=\"font-size:12px; font-weight:500; border-radius:20px; padding:3px 10px; "
        f"flex:none; color:#8A4A18; background:#F6E8DC;\">Worth a careful look</span>"
        f"<span style=\"color:#8A4A18; font-size:14px;\">{p['signal']}</span>"
        f"<span style=\"font-size:13px; color:#6E6E76;\">{p['detail']}</span></div>"

        f"<div style=\"display:flex; align-items:center; gap:12px; padding:13px 24px; "
        f"border-top:1px solid #EDEDEF; flex-wrap:wrap; row-gap:8px;\">"
        f"<div style=\"display:flex; align-items:center; flex-wrap:wrap; row-gap:6px;\">"
        f"{chip('Vendor read from the document', '#3D9A50')}"
        f"{chip('Total read from the document', '#3D9A50')}"
        f"{chip('Covering email &middot; ' + p['sender'], None)}</div>"
        f"<div style=\"margin-left:auto; display:flex; align-items:center; gap:10px;\">"
        f"{button('Download the original', 'secondary')}{button('Review document', 'primary')}"
        f"</div></div></article>")

    everything = (
        f"<div style=\"display:flex; align-items:center; gap:10px; margin:14px 0 0; "
        f"font-size:13px; color:#3F3F46;\">"
        f"<span style=\"width:6px; height:6px; border-radius:50%; background:#3D9A50;\"></span>"
        f"That is everything waiting. {d['auto_n']} were cleared by the system on its own.</div>")

    auto_rows = "".join(
        dense_row("#3D9A50", r["vendor"], r["doc"], r["amount"], r["right"],
                  button("Open", "secondary"), last=(i == len(d["auto"]) - 1))
        for i, r in enumerate(d["auto"]))
    auto_panel = f"<div style=\"{PANEL} {rule(POSITIVE)}\">{auto_rows}</div>"

    out_rows = "".join(
        dense_row("#6E6E76", r["vendor"], r["doc"], r["state"], r["when"],
                  button("Push to Jira", "disabled"), last=(i == len(d["outbox"]) - 1))
        for i, r in enumerate(d["outbox"]))
    out_panel = f"<div style=\"{PANEL} {rule(NEUTRAL)}\">{out_rows}</div>"

    history = (f"<p style=\"font-size:13px; color:#6E6E76; margin:10px 0 0;\">"
               f"Nobody has decided anything yet.</p>")

    foot = (f"<p style=\"font-size:12.5px; color:#6E6E76; margin:18px 0 0; padding:12px 16px; "
            f"border-radius:10px; background:#FBFBFC; border:1px solid #EDEDEF;\">"
            f"Approving opens a Jira task for someone else, so the decision is made inside the "
            f"document, not from this page.</p>")

    return f"""
    <div style="flex:1; padding:26px 30px 34px; display:flex; flex-direction:column; min-width:0;">

      <nav style="display:flex; gap:22px; border-bottom:1px solid #E1E1E4;">
{tabs}
      </nav>

      <div style="display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-top:22px;">
{tiles}
      </div>

{head("awaiting", "Awaiting approval", "a person has to decide on each of these",
              "#5B5BD6", "#EEEEFB", "#3E3EA8")}
{card}
{everything}

{head("auto", "Approved by the system", "at a validation score of 1.00, with nobody asked",
              "#3D9A50", "#EAF5EC", "#2F7A3F")}
{auto_panel}

{head("outbox", "Outbox", d["outbox_note"], "#C9C9CF")}
{out_panel}

{head("history", "History", "decisions a person made, including rejections", "#C9C9CF")}
{history}
{foot}
    </div>
"""


def read_live():
    import app as dashboard
    from review_signals import risk_detail, risk_signal
    import pandas as pd

    df = dashboard.load_data()
    pending = df[df["approval_status"] == "Pending"]
    auto = df[(df["approval_status"] == "Approved") & (df["reviewed_at"].isna())]
    outbox = dashboard.load_outbox()
    history = dashboard.load_history()

    row = pending.iloc[0]
    meta = [row["invoice_number"] or "-"]
    if row.get("invoice_date") and not pd.isna(row["invoice_date"]):
        meta.append(f"issued {row['invoice_date']}")
    waited = dashboard.waiting_days(row)
    if waited is not None:
        meta.append(f"waiting {waited}d")

    arrived = row.get("email_received_at")
    since = (f"since {pd.Timestamp(arrived).strftime('%d %b').lstrip('0')}"
             if arrived and not pd.isna(arrived) else "the queue is empty")

    jira = outbox[outbox["channel"] == "Jira"]
    pushable = jira[jira["state"].isin(["Pending", "Failed"])]
    frozen = len(outbox[outbox["channel"] != "Jira"])
    sent = int((outbox["state"] == "Sent").sum())

    return {
        "awaiting_label": "Awaiting approval",
        "pending_n": len(pending), "auto_n": len(auto),
        "outbox_n": len(outbox), "history_n": len(history),
        "waiting_total": dashboard.single_currency_total(pending) or "more than one currency",
        "oldest": f"{waited}d" if waited is not None else "none",
        "oldest_since": since,
        "pending": {
            "vendor": row["vendor_name"], "type": row["document_type"],
            "meta": " &middot; ".join(meta),
            "amount": dashboard.money(row["total_amount"], row["currency"]),
            "signal": risk_signal(row), "detail": risk_detail(row),
            "sender": row.get("email_sender"),
        },
        "auto": [{"vendor": r["vendor_name"], "doc": r["invoice_number"],
                  "amount": dashboard.money(r["total_amount"], r["currency"]),
                  "right": f"score {r['validation_score']:.2f} &middot; "
                           f"{str(r['system_processed_at'])[11:16]}"}
                 for _, r in auto.iterrows()],
        "outbox": [{"vendor": r["vendor_name"], "doc": r["invoice_number"],
                    "state": r["state"], "when": str(r["created_at"])[:16]}
                   for _, r in pushable.iterrows()],
        "outbox_note": f"{len(pushable)} ready to push, {frozen} with no transport, {sent} sent",
    }


def main():
    data = read_live()
    html = V2.read_text()

    head_end = html.index("</header>") + len("</header>")
    main_end = html.index("</main>")
    V2.write_text(html[:head_end] + "\n" + build_body(data, themed=False) + "\n  " + html[main_end:])
    print(f"wrote {V2.name}")

    themed = html[:head_end] + "\n" + build_body(data, themed=True) + "\n  " + html[main_end:]
    for old, new in TOKEN_MAP.items():
        themed = themed.replace(old, new)
    themed = themed.replace("<title>Approval queue</title>",
                            "<title>Approval queue, tinted surfaces</title>")
    V3.write_text(themed)
    print(f"wrote {V3.name}")


if __name__ == "__main__":
    main()
