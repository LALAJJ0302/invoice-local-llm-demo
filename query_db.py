"""Prints the contents of the normalised invoice database.

Reads the invoices table. For the pre-migration flat table see workflow_records_v1,
which migration 001 keeps intact.
"""

import sys

from storage import DEFAULT_DB_PATH, connect, from_cents


def view_database_records(db_path: str = DEFAULT_DB_PATH):
    """Displays every processed invoice, with its line item count."""
    conn = connect(db_path)
    rows = conn.execute("""
        SELECT
            i.invoice_id,
            i.run_id,
            i.file_name,
            i.validation_status,
            i.validation_score,
            i.invoice_number,
            i.vendor_name,
            i.total_cents,
            i.currency,
            i.total_source,
            i.approval_status,
            (SELECT COUNT(*) FROM line_items l WHERE l.invoice_id = i.invoice_id) AS line_items
        FROM invoices i
        ORDER BY i.invoice_id
    """).fetchall()
    conn.close()

    if not rows:
        print("[*] No records found in the database.")
        return

    header = (f"{'ID':<4} | {'Run':<4} | {'File Name':<34} | {'Status':<12} | {'Score':<6} | "
              f"{'Invoice #':<14} | {'Vendor':<18} | {'Total':<14} | {'Source':<9} | {'Items':<5}")
    print("=" * len(header))
    print(header)
    print("=" * len(header))

    for row in rows:
        total = from_cents(row["total_cents"])
        total_str = "N/A" if total is None else f"{total:,.2f} {row['currency'] or ''}".strip()
        print(f"{row['invoice_id']:<4} | {row['run_id']:<4} | {row['file_name'][:34]:<34} | "
              f"{row['validation_status']:<12} | {row['validation_score']:<6.2f} | "
              f"{(row['invoice_number'] or 'N/A'):<14} | {(row['vendor_name'] or 'N/A')[:18]:<18} | "
              f"{total_str:<14} | {row['total_source']:<9} | {row['line_items']:<5}")

    print("=" * len(header))
    print("Source 'fallback' means the total was derived from line items, not extracted.")


if __name__ == "__main__":
    view_database_records(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB_PATH)
