import sqlite3
import json

def view_database_records(db_path: str = "workflow_platform.db"):
    """Reads and displays all processed invoice records from SQLite."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, file_name, status, confidence_score, invoice_number, vendor_name, total_amount, currency, processed_at
        FROM workflow_records
    """)
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        print("[*] No records found in the database.")
        return

    print("=" * 105)
    print(f"{'ID':<4} | {'File Name':<32} | {'Status':<12} | {'Score':<6} | {'Invoice #':<14} | {'Vendor':<18} | {'Total':<10}")
    print("=" * 105)

    for row in rows:
        rec_id, file_name, status, score, inv_num, vendor, total, currency, processed_at = row
        inv_str = inv_num if inv_num else "N/A"
        vendor_str = vendor[:16] if vendor else "N/A"
        total_str = f"{total:.2f} {currency}" if total else "N/A"
        
        print(f"{rec_id:<4} | {file_name:<32} | {status:<12} | {score:<6.2f} | {inv_str:<14} | {vendor_str:<18} | {total_str:<10}")

    print("=" * 105)

if __name__ == "__main__":
    view_database_records()