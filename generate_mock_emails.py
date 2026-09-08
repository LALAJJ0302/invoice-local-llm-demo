"""Generates a mock mailbox: vendor correspondence with invoice attachments.

Why this exists. Item 4 of the 2026-09-08 assignment is a historical email retrieval
module for RAG. It cannot be built against an empty table, and `email_messages` has no
rows because intake never calls `record_email`. That file is Luke's and he is on task
assignment, so waiting would block the work indefinitely.

Mock data unblocks it honestly, provided two rules hold:

  1. The mailbox has a HISTORY. Retrieval that only ever sees one email per vendor is not
     retrieval. Each vendor here has prior correspondence: earlier invoices, payment
     confirmations, a query, a credit note. That is what a retrieval module is for.
  2. Ground truth is transcribed SEPARATELY, never derived from this file. Deriving it
     here would test the generator against itself, which is the rule
     evaluation/evaluation-method.md already sets for the invoice samples.

Writes evaluation/mock_mailbox.json. Nothing is sent, received, or connected to Gmail.

Usage:
    python generate_mock_emails.py
    python generate_mock_emails.py --out evaluation/mock_mailbox.json
"""

import argparse
import json
import os
from datetime import datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(REPO_ROOT, "evaluation", "mock_mailbox.json")

# The three vendors already in evaluation/samples, so a retrieved history lines up with a
# document the pipeline actually processes.
VENDORS = [
    {"name": "Apex Cloud Solutions Pty Ltd", "domain": "apexcloud.io",
     "contact": "billing", "currency": "USD", "invoice": "INV-2026-001"},
    {"name": "NextGen Hardware Supplies", "domain": "nextgenhardware.com.au",
     "contact": "accounts", "currency": "AUD", "invoice": "INV-2026-002"},
    {"name": "Synthetix AI Consulting", "domain": "synthetix.ai",
     "contact": "invoicing", "currency": "USD", "invoice": "INV-2026-003"},
]

# Prior correspondence, oldest first. `kind` is the label a classifier would predict, and
# it is written here only as a convenience for building the mailbox. The evaluation labels
# live in a separate file, transcribed by a person.
HISTORY = [
    {"kind": "invoice", "days_ago": 92, "subject": "Invoice {prev} for July services",
     "body": "Hello,\n\nPlease find attached invoice {prev} covering July.\nPayment terms are Net 15.\n\nRegards,\n{contact} team"},
    {"kind": "payment_confirmation", "days_ago": 78,
     "subject": "Payment received for {prev}",
     "body": "Thank you, we have received payment for invoice {prev}.\nNo further action is required.\n\n{contact} team"},
    {"kind": "query", "days_ago": 54,
     "subject": "Query about line items on {prev}",
     "body": "Hi,\n\nCould you confirm the storage line on invoice {prev}? Our records show a\ndifferent quantity from the one billed.\n\nThanks"},
    {"kind": "credit_note", "days_ago": 40,
     "subject": "Credit note against {prev}",
     "body": "A credit note has been raised against invoice {prev} following the query\nabout the storage line. The corrected amount will appear on your next invoice.\n\n{contact} team"},
    {"kind": "statement", "days_ago": 21,
     "subject": "Monthly statement",
     "body": "Attached is your monthly statement. There are no overdue amounts on this\naccount at present.\n\n{contact} team"},
]


def build(vendor, index):
    """One vendor's mailbox: its history, then the current invoice email."""
    emails = []
    prev_number = f"INV-2026-{(index * 10) + 5:03d}"
    base = datetime(2026, 9, 8)

    for seq, item in enumerate(HISTORY, start=1):
        received = base - timedelta(days=item["days_ago"])
        emails.append({
            "message_id": f"<hist-{index}-{seq}@{vendor['domain']}>",
            "sender": f"{vendor['contact']}@{vendor['domain']}",
            "sender_name": vendor["name"],
            "subject": item["subject"].format(prev=prev_number),
            "body": item["body"].format(prev=prev_number, contact=vendor["contact"]),
            "received_at": received.strftime("%Y-%m-%d %H:%M:%S"),
            "attachments": ([f"{prev_number}.pdf"] if item["kind"] in
                            ("invoice", "credit_note", "statement") else []),
            "kind": item["kind"],
            "is_history": True,
        })

    # The current one. Its attachment is a real file in evaluation/samples.
    attachment = f"sample_invoice_{index + 1}_{vendor['invoice']}.pdf"
    emails.append({
        "message_id": f"<current-{index}@{vendor['domain']}>",
        "sender": f"{vendor['contact']}@{vendor['domain']}",
        "sender_name": vendor["name"],
        "subject": f"Tax Invoice {vendor['invoice']} from {vendor['name']}",
        "body": (f"Hello,\n\nPlease find attached tax invoice {vendor['invoice']}.\n"
                 f"Amounts are in {vendor['currency']}. Payment terms are Net 15 days.\n\n"
                 f"Regards,\n{vendor['contact']} team\n{vendor['name']}"),
        "received_at": (base - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"),
        "attachments": [attachment],
        "kind": "invoice",
        "is_history": False,
    })

    # A resend of the same email, same Message-ID. Real mailboxes do this, and it is what
    # has_seen_email exists to absorb. A dataset without one cannot test deduplication.
    duplicate = dict(emails[-1])
    duplicate["received_at"] = (base - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    duplicate["subject"] = "Reminder: " + duplicate["subject"]
    duplicate["is_duplicate"] = True
    emails.append(duplicate)

    return emails


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args()

    mailbox = []
    for index, vendor in enumerate(VENDORS):
        mailbox.extend(build(vendor, index))

    payload = {
        "_comment": ("Synthetic. Generated by generate_mock_emails.py. Evaluation labels are "
                     "NOT derived from this file: see evaluation/email_ground_truth.json, "
                     "which is transcribed separately for the same reason the invoice ground "
                     "truth is."),
        "_generated": datetime.now().strftime("%Y-%m-%d"),
        "_counts": {
            "emails": len(mailbox),
            "vendors": len(VENDORS),
            "historical": sum(1 for e in mailbox if e.get("is_history")),
            "duplicates": sum(1 for e in mailbox if e.get("is_duplicate")),
        },
        "emails": mailbox,
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as handle:
        json.dump(payload, handle, indent=2)

    print(f"=== Mock mailbox written to {args.out} ===")
    for key, value in payload["_counts"].items():
        print(f"  {key:<12} {value}")
    print("\nEach vendor has 5 prior emails plus a current invoice and a resend of it.")
    print("The resend carries the same Message-ID, which is what has_seen_email absorbs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
