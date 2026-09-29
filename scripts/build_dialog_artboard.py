"""The review dialog, designed. Unblocks FE-9.

Built here rather than by a Claude Design round, on Neo's call 2026-09-20. The trade is stated
so nobody mistakes it later: `DesignSync` writes files into a design-system project, it does not
invoke Claude Design's own generation, so the second designer's eye that the brief in
`review-dialog-design-brief.md` was written to buy is not what this produced.

Everything on it is real. The document text is read from the archived PDF, the contradiction is
computed by `line_item_check`, and the Jira title is built by the dispatcher's own function.

The three problems the brief set, and what this answers:

1. **The decision was below the fold.** A sticky footer carries the consequence line and the
   two buttons, so a person can always see that a decision is being asked of them.
2. **The contradiction was available but not visible.** The three priced rows are marked inside
   the document panel, and the panel opposite carries the arithmetic: they sum to exactly the
   total the model reported, and the model stored none of them.
3. **Approving created work silently.** The consequence line names the issue that would be
   created and says plainly when nothing will be sent.

Run: ./.venv/bin/python scripts/build_dialog_artboard.py
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import app as dashboard          # noqa: E402
import task_dispatch             # noqa: E402
from line_item_check import contradiction, rows_in   # noqa: E402
from review_signals import risk_detail, risk_signal   # noqa: E402

OUT = ROOT / "review-dialog-design.html"

MONO = "font-family:'IBM Plex Mono',monospace;"
LIFT = "box-shadow:0 1px 2px rgba(22,26,35,0.06), 0 8px 20px rgba(22,26,35,0.07);"
FILL = "rgba(22,26,35,0.045)"

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500'
         '&family=IBM+Plex+Sans:wght@400;450;500;600&display=swap" rel="stylesheet">')


def read_pending():
    import pandas as pd
    from pypdf import PdfReader

    df = dashboard.load_data()
    row = df[df["approval_status"] == "Pending"].iloc[0]
    text = "\n".join((p.extract_text() or "")
                     for p in PdfReader(row["archive_path"]).pages)
    items = dashboard.load_line_items(int(row["id"]))
    meta = [row["invoice_number"] or "-"]
    if row.get("invoice_date") and not pd.isna(row["invoice_date"]):
        meta.append(f"issued {row['invoice_date']}")
    waited = dashboard.waiting_days(row)
    if waited is not None:
        meta.append(f"waiting {waited}d")
    return {
        "row": row, "text": text,
        "meta": " · ".join(meta),
        "amount": dashboard.money(row["total_amount"], row["currency"]),
        "signal": risk_signal(row), "detail": risk_detail(row),
        "stored_items": len(items),
        "clash": contradiction(text, len(items), row["total_amount"]),
        "email": dashboard.load_email_for(row),
        "actions": dashboard.load_action_items(int(row["id"])),
        "title": task_dispatch._build_summary(
            dashboard.follow_up_for(row["document_type"]),
            row["file_name"], row["vendor_name"]),
        "jira_on": dashboard.jira_ready(),
    }


def document_panel(text, marked):
    """The extracted text, with the rows the model missed marked where they appear.

    Marking them here is what lets the panel opposite be read without scrolling between the
    two: the eye has somewhere to land on both sides of the same fact.
    """
    wanted = {r.description for r in marked}
    out, i, lines = [], 0, [ln.strip() for ln in text.splitlines() if ln.strip()]
    while i < len(lines):
        if lines[i] in wanted and i + 3 < len(lines):
            desc, qty, unit, total = lines[i:i + 4]
            out.append(
                f"<div style=\"display:grid; grid-template-columns:1fr auto auto auto; gap:12px; "
                f"align-items:baseline; margin:3px -8px; padding:6px 8px; border-radius:7px; "
                f"background:#FAF0E2; box-shadow:inset 3px 0 0 #A65A1E;\">"
                f"<span>{desc}</span><span style='{MONO} color:#7A7160;'>{qty}</span>"
                f"<span style='{MONO} color:#7A7160;'>{unit}</span>"
                f"<span style='{MONO} font-weight:500;'>{total}</span></div>")
            i += 4
        else:
            out.append(f"<div style=\"padding:1px 0;\">{lines[i]}</div>")
            i += 1
    return "".join(out)


def clash_panel(clash, amount):
    rows = "".join(
        f"<div style=\"display:flex; justify-content:space-between; gap:12px; "
        f"{MONO} font-size:12px; color:#7A7160;\"><span>{r.description if len(r.description) <= 30 else r.description[:29] + chr(8230)}</span>"
        f"<span>{r.line_total.split()[1]}</span></div>" for r in clash["rows"])
    agreement = ("the exact total the model did read"
                 if clash["matches_stored_total"] else "which the model's total does not match")
    return (
        f"<div style=\"margin-top:12px; padding:14px 16px; border-radius:12px; "
        f"background:#FAF0E2; box-shadow:inset 3px 0 0 #A65A1E;\">"
        f"<div style=\"font-size:13.5px; font-weight:600; color:#8A4A18; margin-bottom:6px;\">"
        f"The document shows {clash['count']}</div>"
        f"<div style=\"font-size:13px; color:#6B6152; line-height:1.55; margin-bottom:10px;\">"
        f"They are marked in the panel on the left. They sum to "
        f"<strong>{clash['sum']:,.2f}</strong>, {agreement}. The model stored none of them."
        f"</div>{rows}"
        f"<div style=\"display:flex; justify-content:space-between; gap:12px; margin-top:6px; "
        f"padding-top:6px; border-top:1px solid rgba(138,74,24,0.22); {MONO} font-size:12.5px; "
        f"font-weight:500; color:#8A4A18;\"><span>sum of the rows</span>"
        f"<span>{clash['sum']:,.2f}</span></div></div>")


def field(label, value, note=""):
    tail = (f"<span style=\"{MONO} font-size:11.5px; color:#5F667A; "
            f"background:{FILL}; border-radius:5px; padding:1px 6px; margin-left:8px;\">"
            f"{note}</span>") if note else ""
    return (f"<div style=\"display:flex; justify-content:space-between; align-items:baseline; "
            f"gap:12px; padding:7px 0;\">"
            f"<span style=\"font-size:13px; color:#5F667A;\">{label}</span>"
            f"<span style=\"{MONO} font-size:13px; color:#161A23; text-align:right;\">"
            f"{value}{tail}</span></div>")


def collapsed(label):
    return (f"<div style=\"display:flex; align-items:center; gap:10px; padding:11px 16px; "
            f"border-radius:10px; background:{FILL}; font-size:13px; color:#43495A; "
            f"margin-bottom:8px; cursor:pointer;\">"
            f"<span style=\"{MONO} color:#5F667A;\">&rsaquo;</span>{label}</div>")


def build(d):
    row = d["row"]
    clash = d["clash"]
    consequence = (
        f"Approving creates a Jira issue immediately, titled "
        f"<code style=\"{MONO} font-size:12px;\">{d['title']}</code>."
        if d["jira_on"] else
        f"Approving opens a task titled <code style=\"{MONO} font-size:12px;\">{d['title']}</code> "
        f"and queues it for Jira. <strong>Jira is not configured, so nothing is sent</strong>: "
        f"the row waits in the Outbox until <code style=\"{MONO} font-size:12px;\">JIRA_ENABLED"
        f"</code> is set.")

    actions = "".join(
        f"<div style=\"padding:9px 12px; border-radius:9px; background:{FILL}; margin-bottom:6px;\">"
        f"<div style=\"font-size:13px; color:#161A23;\">{a['Action']}</div>"
        f"<div style=\"font-size:12px; color:#5F667A; font-style:italic;\">“{a['Evidence']}”</div>"
        f"</div>" for _, a in d["actions"].iterrows())

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Review document</title>{FONTS}
<style>
  body {{ margin:0; background:#CEDAEE; font-family:'IBM Plex Sans',system-ui,sans-serif;
         color:#161A23; -webkit-font-smoothing:antialiased; }}
  .frame {{ width:1120px; height:820px; margin:40px auto; background:#E8EDF7;
            border-radius:16px; overflow:hidden; display:flex; flex-direction:column;
            box-shadow:0 24px 60px rgba(22,26,35,0.28); }}
  .scroll {{ flex:1; overflow-y:auto; padding:0 28px 22px; }}
  code {{ background:rgba(22,26,35,0.06); border-radius:5px; padding:1px 5px; }}
</style></head><body>
<div class="frame">

  <!-- 1. What am I approving. Sticky, so it never scrolls away from the decision. -->
  <div style="position:sticky; top:0; z-index:2; background:#E8EDF7; padding:22px 28px 0;">
    <div style="display:flex; align-items:baseline; gap:12px;">
      <span style="font-size:18px; font-weight:600; letter-spacing:-0.01em;">{row['vendor_name']}</span>
      <span style="font-size:11.5px; color:#43495A; background:{FILL}; border-radius:20px;
                   padding:2px 9px;">{row['document_type']}</span>
      <span style="{MONO} margin-left:auto; font-size:20px; font-weight:500;
                   font-variant-numeric:tabular-nums;">{d['amount']}</span>
    </div>
    <div style="{MONO} font-size:12.5px; color:#5F667A; margin-top:5px;">{d['meta']}</div>

    <!-- 2. Is there a concern. The only filled band on the screen. -->
    <div style="margin:14px 0 4px; padding:12px 16px; border-radius:12px; background:#FAF0E2;
                display:flex; align-items:baseline; gap:10px; flex-wrap:wrap;">
      <span style="font-size:12px; font-weight:500; color:#8A4A18; background:#F0DCC4;
                   border-radius:20px; padding:3px 10px;">Worth a careful look</span>
      <span style="font-size:14px; color:#8A4A18;">{d['signal']}</span>
      <span style="font-size:13px; color:#7A7160;">{d['detail']}</span>
    </div>
  </div>

  <div class="scroll">
    <!-- 3. Let me look. Two panels, so the contradiction is one glance rather than a scroll. -->
    <div style="display:grid; grid-template-columns:1.35fr 1fr; gap:14px; margin-top:14px;">

      <div style="background:#FFFFFF; border-radius:12px; {LIFT} padding:16px 18px;">
        <div style="font-size:12px; color:#5F667A; margin-bottom:10px;">What the model read
          <span style="color:#8A4A18;">&middot; the {clash['count']} rows it missed are marked</span>
        </div>
        <div style="{MONO} font-size:12px; line-height:1.5; color:#43495A; max-height:330px;
                    overflow-y:auto;">{document_panel(d['text'], clash['rows'])}</div>
      </div>

      <div style="background:#FFFFFF; border-radius:12px; {LIFT} padding:16px 18px;">
        <div style="font-size:12px; color:#5F667A; margin-bottom:4px;">What the model stored</div>
        {field("Date", row['invoice_date'] or '-')}
        {field("Currency", row['currency'] or '-')}
        {field("Total", d['amount'], row['total_source'])}
        {field("Vendor", "read from the document" if row['vendor_source'] == 'model'
                          else "recovered by our code")}
        <div style="height:1px; background:rgba(22,26,35,0.08); margin:8px 0;"></div>
        {field("Line items", f"<span style='color:#8A4A18; font-weight:500;'>{d['stored_items'] or 'none'}</span>")}
        {clash_panel(clash, d['amount'])}
      </div>
    </div>

    <!-- 4. Where did it come from. Collapsed: it is context, not the decision. -->
    <div style="margin-top:16px;">
      {collapsed(f"Covering email &middot; {d['email']['sender']}")}
      {collapsed(f"What the AI found &middot; {len(d['actions'])} actions")}
      <div style="padding:12px 14px; border-radius:10px; background:{FILL};">
        <div style="font-size:12px; color:#5F667A; margin-bottom:8px;">
          Each action sits above the sentence it was read from. The quote is what separates a
          real action from an invented one.</div>
        {actions}
      </div>
    </div>

    <div style="margin-top:16px;">
      <div style="font-size:12.5px; color:#5F667A; margin-bottom:6px;">
        A note for whoever opens this next</div>
      <div style="background:#FFFFFF; border-radius:10px; {LIFT} padding:12px 14px;
                  font-size:13px; color:#98A0B3; min-height:52px;">
        Chased the vendor about the missing line items.</div>
    </div>
  </div>

  <!-- 5 and 6. The consequence, then the decision. Sticky, because these were below the fold
       and a screen built to slow a person down cannot hide the thing it is slowing them for. -->
  <div style="position:sticky; bottom:0; background:#E8EDF7; padding:14px 28px 18px;
              box-shadow:0 -10px 20px rgba(232,237,247,0.95);">
    <div style="font-size:12.5px; color:#43495A; line-height:1.6; margin-bottom:12px;">
      {consequence}</div>
    <div style="display:flex; gap:10px; justify-content:flex-end;">
      <span style="display:inline-flex; align-items:center; height:36px; padding:0 20px;
                   border-radius:9px; font-size:13.5px; background:rgba(22,26,35,0.05);
                   color:#161A23;">Reject</span>
      <span style="display:inline-flex; align-items:center; height:36px; padding:0 22px;
                   border-radius:9px; font-size:13.5px; background:#5B5BD6; color:#FFFFFF;
                   box-shadow:0 1px 2px rgba(22,26,35,0.06), 0 4px 10px rgba(91,91,214,0.28);">
        Approve</span>
    </div>
  </div>
</div>
</body></html>
"""


def main():
    OUT.write_text(build(read_pending()))
    print(f"wrote {OUT.name}")


if __name__ == "__main__":
    main()
