"""Tooling for re-running a document, and for looking at runs that never finished.

The pipeline moves files into archive/ and upserts on a content hash, so re-extracting a
document meant copying the file back by hand. This does that properly.

    ./.venv/bin/python reprocess.py --list                     # what is archived, and its row
    ./.venv/bin/python reprocess.py --file invoice.pdf         # archive/ -> inbox/, ready to re-run
    ./.venv/bin/python reprocess.py --invoice-id 3 --delete    # drop the row and its line items
    ./.venv/bin/python reprocess.py --list-open-runs           # runs that crashed before finishing

Re-running main.py after --file updates the same row, because the content hash has not
changed. Use --delete only when you want a genuinely fresh row.
"""

import argparse
import os
import shutil
import sys

import storage
from storage import connect, from_cents

ARCHIVE_DIR = "./archive"
INBOX_DIR = "./inbox"


def list_documents(db_path):
    """Shows every archived file alongside the row it belongs to."""
    with connect(db_path) as conn:
        rows = conn.execute("""
            SELECT invoice_id, file_name, archive_path, total_cents, currency,
                   status, approval_status, total_source
            FROM invoices ORDER BY invoice_id
        """).fetchall()

    if not rows:
        print("[*] No invoices in the database.")
        return 0

    print(f"{'ID':<4} | {'File':<36} | {'Total':>12} | {'Quality':<12} | {'Approval':<9} | File on disk")
    print("-" * 104)
    for row in rows:
        total = from_cents(row["total_cents"])
        total_text = "n/a" if total is None else f"{total:,.2f}"
        present = "yes" if row["archive_path"] and os.path.exists(row["archive_path"]) else "MISSING"
        print(f"{row['invoice_id']:<4} | {row['file_name'][:36]:<36} | {total_text:>12} | "
              f"{row['status']:<12} | {row['approval_status']:<9} | {present}")
    return 0


def restore_to_inbox(db_path, file_name):
    """Copies an archived file back to inbox/ so the next run re-extracts it."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT invoice_id, archive_path, approval_status FROM invoices WHERE file_name = ?",
            (file_name,)).fetchone()

    source = row["archive_path"] if row and row["archive_path"] else os.path.join(ARCHIVE_DIR, file_name)
    if not os.path.exists(source):
        print(f"[!] Not found: {source}")
        return 1

    os.makedirs(INBOX_DIR, exist_ok=True)
    target = os.path.join(INBOX_DIR, file_name)
    if os.path.exists(target):
        print(f"[*] Already in the inbox: {target}. Nothing to do.")
        return 0

    # Copy rather than move, so the archive stays complete if the re-run fails.
    shutil.copy2(source, target)
    print(f"[*] Copied to {target}")

    if row:
        print(f"[*] This will update invoice_id={row['invoice_id']}, not create a new row: "
              "the content hash is unchanged.")
        if row["approval_status"] != "Pending":
            print(f"[*] Its approval status is '{row['approval_status']}' and will be preserved. "
                  "Re-processing never overwrites a human decision.")
    print("[*] Now run: ./.venv/bin/python main.py")
    return 0


def delete_invoice(db_path, invoice_id, assume_yes=False):
    """Deletes a row and its line items. The archived file is left alone."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT file_name, total_cents, approval_status FROM invoices WHERE invoice_id = ?",
            (invoice_id,)).fetchone()
        if not row:
            print(f"[!] No invoice with invoice_id={invoice_id}.")
            return 1
        items = conn.execute(
            "SELECT COUNT(*) c FROM line_items WHERE invoice_id = ?", (invoice_id,)).fetchone()["c"]

    total = from_cents(row["total_cents"])
    print(f"[*] About to delete invoice_id={invoice_id}")
    print(f"      file       {row['file_name']}")
    print(f"      total      {'n/a' if total is None else f'{total:,.2f}'}")
    print(f"      approval   {row['approval_status']}")
    print(f"      line items {items} (deleted by cascade)")
    print("    The archived file is NOT deleted.")

    if not assume_yes:
        answer = input("Type the invoice id to confirm: ").strip()
        if answer != str(invoice_id):
            print("[*] Cancelled.")
            return 1

    with connect(db_path) as conn:
        conn.execute("DELETE FROM invoices WHERE invoice_id = ?", (invoice_id,))
        conn.commit()
        remaining = conn.execute(
            "SELECT COUNT(*) c FROM line_items WHERE invoice_id = ?", (invoice_id,)).fetchone()["c"]

    print(f"[*] Deleted. Line items remaining for that id: {remaining}.")
    return 0


def list_open_runs(db_path):
    """A run with no finished_at crashed. Nothing else surfaces that."""
    with connect(db_path) as conn:
        rows = conn.execute("""
            SELECT r.run_id, r.started_at, r.model_name, r.threshold,
                   (SELECT COUNT(*) FROM invoices i WHERE i.run_id = r.run_id) AS wrote
            FROM processing_runs r
            WHERE r.finished_at IS NULL
            ORDER BY r.run_id
        """).fetchall()

    if not rows:
        print("[*] No open runs. Every run finished cleanly.")
        return 0

    print(f"[!] {len(rows)} run(s) never finished. Each one crashed part way through.")
    print(f"{'Run':<5} | {'Started':<20} | {'Model':<28} | Rows written")
    print("-" * 76)
    for row in rows:
        print(f"{row['run_id']:<5} | {row['started_at']:<20} | {row['model_name'][:28]:<28} | {row['wrote']}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Re-run a document, or inspect crashed runs.")
    parser.add_argument("--db", default=storage.DEFAULT_DB_PATH)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="list invoices and whether their file is on disk")
    group.add_argument("--file", metavar="NAME", help="copy an archived file back to inbox/")
    group.add_argument("--invoice-id", type=int, help="target row for --delete")
    group.add_argument("--list-open-runs", action="store_true", help="runs with no finished_at")
    parser.add_argument("--delete", action="store_true", help="with --invoice-id, delete the row")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"[!] No database at {args.db}.")
        return 1

    if args.list:
        return list_documents(args.db)
    if args.list_open_runs:
        return list_open_runs(args.db)
    if args.file:
        return restore_to_inbox(args.db, args.file)
    if args.invoice_id is not None:
        if not args.delete:
            print("[!] --invoice-id needs --delete. Nothing else acts on a single row yet.")
            return 1
        return delete_invoice(args.db, args.invoice_id, assume_yes=args.yes)
    return 1


if __name__ == "__main__":
    sys.exit(main())
