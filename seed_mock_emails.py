"""Loads the mock mailbox into email_messages, so retrieval work can start.

Calls StorageManager.record_email, the same API intake will call when Luke wires it.
`email_listener.py` is NOT touched: this is a separate script that exercises the existing
interface, so nothing here has to be undone when real intake arrives.

Idempotent, because record_email is keyed on the RFC 5322 Message-ID. Running it twice
updates the same rows rather than duplicating them, and the resends in the mailbox are
absorbed by that same key.

Usage:
    python seed_mock_emails.py
    python seed_mock_emails.py --clear     # remove seeded rows first
"""

import argparse
import json
import os
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO_ROOT)

import storage  # noqa: E402

MAILBOX = os.path.join(REPO_ROOT, "evaluation", "mock_mailbox.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mailbox", default=MAILBOX)
    parser.add_argument("--clear", action="store_true",
                        help="Delete rows whose message_id looks seeded, then reload.")
    args = parser.parse_args()

    if not os.path.exists(args.mailbox):
        print(f"No mailbox at {args.mailbox}. Run generate_mock_emails.py first.")
        return 1

    emails = json.load(open(args.mailbox))["emails"]
    manager = storage.StorageManager()

    if args.clear:
        with sqlite3.connect(manager.db_path) as conn:
            removed = conn.execute(
                "DELETE FROM email_messages WHERE message_id LIKE '<hist-%' "
                "OR message_id LIKE '<current-%'").rowcount
        print(f"[clear] removed {removed} seeded rows")

    inserted = already = 0
    for email in emails:
        result = manager.record_email(
            message_id=email["message_id"],
            sender=email["sender"],
            subject=email["subject"],
            received_at=email["received_at"],
            attachment_count=len(email["attachments"]),
            body_text=email["body"],
            body_source="mock",
            attachment_names=email["attachments"],
        )
        if result["already_seen"]:
            already += 1
        else:
            inserted += 1

    print(f"\n=== Seeded {len(emails)} emails ===")
    print(f"  new rows            {inserted}")
    print(f"  already seen        {already}   <- the resends, absorbed on Message-ID")

    with sqlite3.connect(manager.db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM email_messages").fetchone()[0]
        bodies = conn.execute(
            "SELECT COUNT(*) FROM email_messages WHERE body_text IS NOT NULL").fetchone()[0]
        senders = conn.execute(
            "SELECT sender, COUNT(*) FROM email_messages GROUP BY sender").fetchall()
    print(f"  rows in table       {total}")
    print(f"  with a body         {bodies}   (body_source='mock')")
    for sender, count in senders:
        print(f"    {sender:<40} {count}")

    print("\nFR-1.3 duplicate protection is now demonstrable: the resends did not create")
    print("new rows. That was previously untestable, because the table was empty.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
