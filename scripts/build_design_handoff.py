"""Rebuild the design handoff package from the live database.

    ./.venv/bin/python scripts/build_design_handoff.py

Writes design-handoff/03-STRUCTURE.html: the approval screen as semantic, unstyled markup
carrying whatever the database holds right now. The other files in that folder are copies of
fe-screen-spec.md and approval-screen-design-brief.md, which are tracked, plus screenshots.

The folder itself is gitignored because it is derived. This script is tracked because the folder
is worthless without a way to regenerate it, and the content changes every time a document is
processed.

Class names are the interface to whatever stylesheet comes back from the design work: `.signal`,
`.amount`, `.queue`, `.consequence` and the rest are what app.py can target. Renaming one here
means renaming it there.
"""
import html
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from review_signals import risk_signal
from storage import connect

DB = os.path.join(REPO, "workflow_platform.db")
e = html.escape


def money(cents, currency):
    return f"{currency or ''} {cents / 100:,.2f}".strip() if cents is not None else "-"


with connect(DB) as c:
    invoices = [dict(r) for r in c.execute("SELECT * FROM invoices ORDER BY invoice_id")]
    notes = [dict(r) for r in c.execute(
        "SELECT o.created_at, o.channel, o.payload, i.vendor_name, i.invoice_number "
        "FROM outbound_messages o JOIN invoices i ON i.invoice_id = o.invoice_id "
        "WHERE o.state='Pending' ORDER BY o.created_at DESC")]
    pending = [i for i in invoices if i["approval_status"] == "Pending"]
    auto = [i for i in invoices if i["approval_status"] == "Approved" and not i["reviewed_at"]]
    first = pending[0] if pending else invoices[0]
    items = [dict(r) for r in c.execute(
        "SELECT * FROM invoice_action_items WHERE invoice_id = ? ORDER BY line_no",
        (first["invoice_id"],))]
    email = c.execute(
        "SELECT sender, subject, received_at, body_text FROM email_messages WHERE email_id = ?",
        (first["email_id"],)).fetchone()

pending_value = sum(i["total_cents"] or 0 for i in pending) / 100


def rows(records, with_signal=True):
    out = []
    for r in records:
        signal = risk_signal(r) if with_signal else None
        out.append(f"""      <tr>
        <td class="vendor">{e(r['vendor_name'] or '-')}</td>
        <td class="amount">{e(money(r['total_cents'], r['currency']))}</td>
        <td class="document">{e(r['invoice_number'] or '-')}</td>
        <td class="doctype">{e(r['document_type'])}</td>
        <td class="signal">{e(signal) if signal else ''}</td>
        <td class="action"><button>Open</button></td>
      </tr>""")
    return "\n".join(out)


note_rows = "\n".join(f"""      <tr>
        <td class="queued">{e(n['created_at'])}</td>
        <td class="channel">{e(n['channel'])}</td>
        <td class="vendor">{e(n['vendor_name'] or '-')}</td>
        <td class="document">{e(n['invoice_number'] or '-')}</td>
        <td class="message">{e(n['payload'])}</td>
      </tr>""" for n in notes)

action_html = "\n".join(f"""        <li>
          <label><input type="checkbox"{' checked' if a['is_done'] else ''}> {e(a['task'])}</label>
          <blockquote>{e(a['evidence_quote'] or '')}</blockquote>
        </li>""" for a in items)

doc = f"""<!-- Unstyled structure of the invoice approval screen, with the real data it holds.
     No CSS at all: class names mark what each element is, so they can be styled directly.
     Generated from the live database. -->
<main>
  <h1>Invoice approvals</h1>

  <nav class="tabs">
    <button class="tab" aria-selected="true">Awaiting approval <span class="count">{len(pending)}</span> <span class="value">{pending_value:,.0f}</span></button>
    <button class="tab">Approved by the system <span class="count">{len(auto)}</span></button>
    <button class="tab">Notifications <span class="count">{len(notes)}</span></button>
  </nav>

  <!-- TAB 1 -->
  <section class="panel" id="awaiting">
    <table class="queue">
      <thead>
        <tr><th>Vendor</th><th>Amount</th><th>Document</th><th>Type</th><th>Signal</th><th></th></tr>
      </thead>
      <tbody>
{rows(pending)}
      </tbody>
    </table>
    <p class="empty-state">Nothing is waiting for you.</p>
  </section>

  <!-- TAB 2 -->
  <section class="panel" id="auto" hidden>
    <p class="note">These were approved at a validation score of 1.00 with no person involved.
      Every check behind that score reads the document itself, so a duplicate, an unknown vendor
      and a well-formatted forgery all score the same.</p>
    <table class="queue">
      <thead><tr><th>Vendor</th><th>Amount</th><th>Document</th><th>Type</th><th></th><th></th></tr></thead>
      <tbody>
{rows(auto)}
      </tbody>
    </table>
  </section>

  <!-- TAB 3 -->
  <section class="panel" id="notifications" hidden>
    <p class="note">Recorded, never sent. The text was written when it was true and nothing
      rewrites it, which is why the age matters: the three oldest still say "NeedsReview at score
      0.25" for documents that now read Validated.</p>
    <table class="outbox">
      <thead><tr><th>Queued</th><th>Channel</th><th>Vendor</th><th>Document</th><th>Message</th></tr></thead>
      <tbody>
{note_rows}
      </tbody>
    </table>
  </section>

  <!-- DIALOG, opens from a queue row. The only place approve and reject exist. -->
  <dialog class="review" open>
    <header>
      <h2 class="vendor">{e(first['vendor_name'] or '-')}</h2>
      <p class="headline">
        <span class="amount">{e(money(first['total_cents'], first['currency']))}</span>
        <span class="document">{e(first['invoice_number'] or '-')}</span>
        <span class="doctype">{e(first['document_type'])}</span>
      </p>
      <p class="signal">{e(risk_signal(first) or '')}</p>
    </header>

    <div class="evidence">
      <section class="source">
        <h3>What the model read</h3>
        <pre class="document-text">TAX INVOICE
Vendor: Apex Cloud Solutions Pty Ltd
Address: Level 14, 100 George St, Sydney NSW 2000
Invoice Number: INV-2026-001
Date of Issue: 2026-08-10
Currency: USD
Dedicated Cloud Compute - EC2 Instance   2   USD 450.00   USD 900.00
High Performance SSD Storage (1TB)       3   USD 120.00   USD 360.00
Managed Database Service (PostgreSQL)    1   USD 240.00   USD 240.00
TOTAL: USD 1,500.00</pre>
        <button class="secondary">Download the original</button>
      </section>
      <dl class="fields">
        <dt>Date</dt><dd>{e(first['invoice_date'] or '-')}</dd>
        <dt>Currency</dt><dd>{e(first['currency'] or '-')}</dd>
        <dt>Total source</dt><dd>{e(first['total_source'])}</dd>
        <dt>Vendor source</dt><dd>{e(first['vendor_source'])}</dd>
        <dt>Line items</dt><dd class="contradiction">none</dd>
      </dl>
    </div>

    <details class="email">
      <summary>Covering email · {e(email['sender'])}</summary>
      <p class="subject">{e(email['subject'])}</p>
      <p class="received">{e(email['received_at'] or '-')}</p>
      <pre class="body">{e((email['body_text'] or '')[:220])}</pre>
    </details>

    <details class="ai-findings">
      <summary>What the AI found · {len(items)} actions</summary>
      <p class="note">Each action sits above the sentence it was read from. The quote is what
        separates a real action from an invented one.</p>
      <ul class="actions">
{action_html}
      </ul>
    </details>

    <footer>
      <p class="consequence">Approving creates a Jira task <strong>Payment</strong> immediately.</p>
      <button class="approve">Approve</button>
      <button class="reject">Reject</button>
    </footer>
  </dialog>
</main>
"""
out = os.path.join(REPO, "design-handoff", "03-STRUCTURE.html")
os.makedirs(os.path.dirname(out), exist_ok=True)
open(out, "w").write(doc)
print("wrote", out, len(doc.split(chr(10))), "lines")
