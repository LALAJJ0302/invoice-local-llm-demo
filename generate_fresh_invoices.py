"""Write three fresh invoice PDFs. Every run draws new details.

Two invoices use the same Grand Total layout as generate_mock_invoices.py, so a
complete extraction can score 1.00. The third never uses a total label, so the
validation gate cannot score it 1.00. This file does not write validation_score;
main.py does that after it reads the PDFs.

    python generate_fresh_invoices.py
    python generate_fresh_invoices.py --target-dir ./inbox
"""

import argparse
import os
import random
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from generate_mock_invoices import create_invoice_pdf

# The fixed samples already use these numbers. A fresh run must not reuse them.
RESERVED_INVOICE_NUMBERS = {f"INV-2026-{i:03d}" for i in range(1, 5)}

# Any of these on the review invoice would let the gate treat the amount as verified.
# "total" covers "grand total", "total due" and "invoice total".
REVIEW_FORBIDDEN = ("total", "amount due", "balance due")

VENDORS: Tuple[Tuple[str, str, str], ...] = (
    ("Northwind Analytics Pty Ltd", "41 Market St, Sydney NSW 2000", "accounts@northwind.example"),
    ("Bluegum Office Co", "18 Collins St, Melbourne VIC 3000", "billing@bluegum.example"),
    ("Lumen Field Services", "9 Edward St, Brisbane QLD 4000", "invoices@lumenfield.example"),
    ("Copperline Electrical", "220 South Tce, Adelaide SA 5000", "accounts@copperline.example"),
    ("Paperbark Studio", "5 Murray St, Perth WA 6000", "billing@paperbark.example"),
    ("Redcedar Logistics", "14 Davey St, Hobart TAS 7000", "accounts@redcedar.example"),
    ("Silverwattle Consulting", "80 Northbourne Ave, Canberra ACT 2600", "billing@silverwattle.example"),
    ("Kinship Software", "2 Darwin Esplanade, Darwin NT 0800", "accounts@kinship.example"),
)

CUSTOMERS = (
    "Enterprise Client Inc.",
    "Harbour Street Holdings",
    "Westfield Operations",
    "Civic Records Office",
)

SERVICES = (
    "Cloud compute hours",
    "Managed database hosting",
    "On-site document review",
    "Workstation setup",
    "Display docking station",
    "Architecture consulting",
    "Pipeline configuration",
    "Travel to the client site",
    "Software licence",
    "Priority support retainer",
)

# Whole cents, so a line total is quantity times unit price with no rounding.
UNIT_CENTS = (12000, 15000, 18000, 19000, 24000, 35000, 40000, 45000, 85000)
CURRENCIES = ("AUD", "USD")
PAYMENT_DAYS = (14, 15, 30)


def _money(cents: int) -> float:
    return cents / 100.0


def _invoice_number(rng: random.Random, used: set) -> str:
    """A number shaped like INV-2026-1842, unused in this run and not a fixed sample."""
    year = date.today().year
    for _ in range(100):
        number = f"INV-{year}-{rng.randint(1000, 9999)}"
        if number in used or number in RESERVED_INVOICE_NUMBERS:
            continue
        used.add(number)
        return number
    raise RuntimeError("could not draw a fresh invoice number")


def _invoice_data(rng: random.Random, vendor: Tuple[str, str, str], number: str) -> Dict[str, Any]:
    """One invoice whose line items add up to the payable amount, in cents."""
    name, address, email = vendor
    issued = date(date.today().year, 1, 1) + timedelta(days=rng.randint(0, 363))
    descriptions = rng.sample(SERVICES, k=rng.randint(2, 3))
    items = []
    total_cents = 0
    for description in descriptions:
        quantity = rng.randint(1, 5)
        unit_cents = rng.choice(UNIT_CENTS)
        line_cents = quantity * unit_cents
        total_cents += line_cents
        items.append({
            "description": description,
            "quantity": quantity,
            "unit_price": _money(unit_cents),
            "total": _money(line_cents),
        })
    days = rng.choice(PAYMENT_DAYS)
    return {
        "invoice_number": number,
        "vendor_name": name,
        "vendor_address": address,
        "vendor_email": email,
        "customer_name": rng.choice(CUSTOMERS),
        "date": issued.isoformat(),
        "currency": rng.choice(CURRENCIES),
        "payment_terms": f"Due within {days} days",
        "payment_days": days,
        "items": items,
        "total_amount": _money(total_cents),
    }


def _review_blob(data: Dict[str, Any]) -> str:
    parts = [
        data["vendor_name"], data["vendor_address"], data["vendor_email"],
        data["invoice_number"], data["date"], data["customer_name"], data["currency"],
        f"due within {data['payment_days']} days",
    ]
    for item in data["items"]:
        parts.append(item["description"])
    return " ".join(str(part) for part in parts).lower()


def create_fresh_review_pdf(output_path: str, data: Dict[str, Any]) -> None:
    """An invoice whose payable amount is not beside a grand-total label.

    The word 'total' is absent on purpose, as is 'amount due' and 'balance due'.
    Any of those would let ConfidenceValidator mark the amount verified.
    """
    blob = _review_blob(data)
    found = [label for label in REVIEW_FORBIDDEN if label in blob]
    if found:
        raise ValueError(f"review invoice contains forbidden label(s): {found}")

    doc = SimpleDocTemplate(
        output_path, pagesize=letter,
        rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        name="FreshReviewTitle", parent=styles["Heading1"], fontSize=24, leading=28,
        textColor=colors.HexColor("#1E293B"), fontName="Helvetica-Bold",
    )
    body = ParagraphStyle(
        name="FreshReviewBody", parent=styles["Normal"], fontSize=10, leading=14,
        textColor=colors.HexColor("#475569"),
    )
    header = ParagraphStyle(
        name="FreshReviewHeader", parent=styles["Normal"], fontSize=10, leading=12,
        textColor=colors.white, fontName="Helvetica-Bold",
    )
    cell = ParagraphStyle(
        name="FreshReviewCell", parent=styles["Normal"], fontSize=10, leading=12,
        textColor=colors.HexColor("#1E293B"),
    )
    currency = data["currency"]

    story = [
        Paragraph("TAX INVOICE", title_style),
        Spacer(1, 10),
        Paragraph(
            f"<b>Vendor:</b> {data['vendor_name']}<br/>"
            f"<b>Address:</b> {data['vendor_address']}<br/>"
            f"<b>Email:</b> {data['vendor_email']}",
            body,
        ),
        Spacer(1, 12),
        Paragraph(
            f"<b>Invoice Number:</b> {data['invoice_number']}<br/>"
            f"<b>Date of Issue:</b> {data['date']}<br/>"
            f"<b>Bill To:</b> {data['customer_name']}<br/>"
            f"<b>Currency:</b> {currency}",
            body,
        ),
        Spacer(1, 20),
    ]

    table_data: List[List[Any]] = [[
        Paragraph("Description", header),
        Paragraph("Qty", header),
        Paragraph("Unit Price", header),
        Paragraph("Line amount", header),
    ]]
    for item in data["items"]:
        table_data.append([
            Paragraph(item["description"], cell),
            Paragraph(str(item["quantity"]), cell),
            Paragraph(f"{currency} {item['unit_price']:.2f}", cell),
            Paragraph(f"{currency} {item['total']:.2f}", cell),
        ])

    item_table = Table(table_data, colWidths=[240, 60, 110, 110])
    item_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(item_table)
    story.append(Spacer(1, 16))
    story.append(Paragraph(
        f"<b>Amount payable:</b> {currency} {data['total_amount']:.2f}", body,
    ))
    story.append(Spacer(1, 12))
    story.append(Paragraph(
        f"Payment terms: due within {data['payment_days']} days.", body,
    ))
    doc.build(story)
    print(f"  [Generated] {output_path}")


def generate_fresh_invoices(
    target_dir: str = "./inbox",
    rng: Optional[random.Random] = None,
) -> List[Dict[str, Any]]:
    """Write two complete invoices and one review invoice. Returns what was written.

    Each dict is the invoice data plus ``path`` and ``role`` (``complete`` or ``review``).
    """
    rng = rng or random.Random()
    os.makedirs(target_dir, exist_ok=True)

    used: set = set()
    vendors = rng.sample(VENDORS, 3)
    written: List[Dict[str, Any]] = []

    print(f"=== Generating fresh invoices into '{target_dir}' ===")
    for vendor in vendors[:2]:
        data = _invoice_data(rng, vendor, _invoice_number(rng, used))
        filename = f"fresh_{data['invoice_number']}.pdf"
        path = os.path.join(target_dir, filename)
        create_invoice_pdf(path, data)
        written.append({**data, "path": path, "role": "complete"})

    review = _invoice_data(rng, vendors[2], _invoice_number(rng, used))
    review_path = os.path.join(target_dir, f"fresh_review_{review['invoice_number']}.pdf")
    create_fresh_review_pdf(review_path, review)
    written.append({**review, "path": review_path, "role": "review"})

    print("=== Wrote 3 fresh invoice PDFs ===")
    for row in written:
        if row["role"] == "complete":
            note = "Grand Total on the page"
        else:
            note = "no total label, so the gate cannot score 1.00"
        print(f"  {os.path.basename(row['path'])}: {note}")
    print()
    return written


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Write three fresh invoice PDFs.")
    parser.add_argument(
        "--target-dir", default="./inbox",
        help="Directory to write the PDFs into (default: ./inbox)",
    )
    args = parser.parse_args(argv)
    generate_fresh_invoices(target_dir=args.target_dir)


if __name__ == "__main__":
    main()
