import os
from typing import Dict, Any, List
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

def create_invoice_pdf(output_path: str, data: Dict[str, Any]) -> None:
    """
    Generates a single structured invoice PDF using ReportLab Flowables.
    
    Args:
        output_path: Target file path for the generated PDF.
        data: Dictionary containing invoice metadata, vendor, items, and totals.
    """
    # Initialize PDF document setup
    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=40
    )
    
    story = []
    styles = getSampleStyleSheet()
    
    # Custom Typography Styles
    title_style = ParagraphStyle(
        name="InvoiceTitle",
        parent=styles["Heading1"],
        fontSize=24,
        leading=28,
        textColor=colors.HexColor("#1E293B"),
        fontName="Helvetica-Bold"
    )
    
    header_style = ParagraphStyle(
        name="HeaderInfo",
        parent=styles["Normal"],
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#475569")
    )
    
    table_text_style = ParagraphStyle(
        name="TableText",
        parent=styles["Normal"],
        fontSize=10,
        leading=12,
        textColor=colors.HexColor("#1E293B")
    )
    
    table_header_style = ParagraphStyle(
        name="TableHeader",
        parent=styles["Normal"],
        fontSize=10,
        leading=12,
        textColor=colors.white,
        fontName="Helvetica-Bold"
    )

    # 1. Invoice Title & Vendor Section
    story.append(Paragraph("TAX INVOICE", title_style))
    story.append(Spacer(1, 10))
    
    vendor_info = f"""
    <b>Vendor:</b> {data['vendor_name']}<br/>
    <b>Address:</b> {data['vendor_address']}<br/>
    <b>Email:</b> {data['vendor_email']}
    """
    story.append(Paragraph(vendor_info, header_style))
    story.append(Spacer(1, 12))

    # 2. Metadata Section (Invoice #, Date, Customer)
    metadata_info = f"""
    <b>Invoice Number:</b> {data['invoice_number']}<br/>
    <b>Date of Issue:</b> {data['date']}<br/>
    <b>Bill To:</b> {data['customer_name']}<br/>
    <b>Currency:</b> {data['currency']}
    """
    story.append(Paragraph(metadata_info, header_style))
    story.append(Spacer(1, 20))

    # 3. Line Items Table Construction
    table_data: List[List[Any]] = [
        [
            Paragraph("Description", table_header_style),
            Paragraph("Qty", table_header_style),
            Paragraph("Unit Price", table_header_style),
            Paragraph("Total", table_header_style)
        ]
    ]

    for item in data["items"]:
        table_data.append([
            Paragraph(item["description"], table_text_style),
            Paragraph(str(item["quantity"]), table_text_style),
            Paragraph(f"{data['currency']} {item['unit_price']:.2f}", table_text_style),
            Paragraph(f"{data['currency']} {item['total']:.2f}", table_text_style)
        ])

    # Add Grand Total Row
    total_label = Paragraph("<b>Grand Total</b>", table_text_style)
    total_value = Paragraph(f"<b>{data['currency']} {data['total_amount']:.2f}</b>", table_text_style)
    table_data.append(["", "", total_label, total_value])

    # Configure Table Layout & Styling
    item_table = Table(table_data, colWidths=[240, 60, 110, 110])
    item_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#0F172A")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('TOPPADDING', (0, 0), (-1, 0), 8),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
        ('TOPPADDING', (0, 1), (-1, -1), 6),
        ('LINEBELOW', (0, 0), (-1, -2), 0.5, colors.HexColor("#E2E8F0")),
        ('LINEBELOW', (2, -1), (3, -1), 1.5, colors.HexColor("#0F172A")),
    ]))

    story.append(item_table)
    story.append(Spacer(1, 25))

    # 4. Payment Notes / Footer
    notes = f"<b>Payment Terms:</b> {data.get('payment_terms', 'Due within 30 days')}. Thank you for your business!"
    story.append(Paragraph(notes, header_style))

    # Build the document
    doc.build(story)
    print(f"  [Generated] {output_path}")


def create_review_invoice_pdf(output_path: str) -> None:
    """An invoice whose payable amount is not beside a grand-total label.

    The gate only scores 1.00 when the amount sits next to a total label. Without
    that label the amount is 'present' at best, the score stays below 1, and the
    document waits for a person instead of auto-approving. The word 'total' is
    absent on purpose: the column header 'Total' would itself count as a label.
    """
    doc = SimpleDocTemplate(
        output_path, pagesize=letter,
        rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        name="ReviewTitle", parent=styles["Heading1"], fontSize=24, leading=28,
        textColor=colors.HexColor("#1E293B"), fontName="Helvetica-Bold",
    )
    body = ParagraphStyle(
        name="ReviewBody", parent=styles["Normal"], fontSize=10, leading=14,
        textColor=colors.HexColor("#475569"),
    )
    header = ParagraphStyle(
        name="ReviewHeader", parent=styles["Normal"], fontSize=10, leading=12,
        textColor=colors.white, fontName="Helvetica-Bold",
    )
    cell = ParagraphStyle(
        name="ReviewCell", parent=styles["Normal"], fontSize=10, leading=12,
        textColor=colors.HexColor("#1E293B"),
    )

    story = [
        Paragraph("TAX INVOICE", title_style),
        Spacer(1, 10),
        Paragraph(
            "<b>Vendor:</b> Harbour Review Supplies<br/>"
            "<b>Address:</b> 12 Wharf Lane, Sydney NSW 2000<br/>"
            "<b>Email:</b> accounts@harbourreview.example",
            body,
        ),
        Spacer(1, 12),
        Paragraph(
            "<b>Invoice Number:</b> INV-2026-004<br/>"
            "<b>Date of Issue:</b> 2026-09-01<br/>"
            "<b>Bill To:</b> Enterprise Client Inc.<br/>"
            "<b>Currency:</b> AUD",
            body,
        ),
        Spacer(1, 20),
    ]

    table_data = [[
        Paragraph("Description", header),
        Paragraph("Qty", header),
        Paragraph("Unit Price", header),
        Paragraph("Line amount", header),
    ]]
    for description, qty, unit, line in (
        ("On-site document review", "2", "400.00", "800.00"),
        ("Travel to the client site", "1", "190.00", "190.00"),
    ):
        table_data.append([
            Paragraph(description, cell),
            Paragraph(qty, cell),
            Paragraph(f"AUD {unit}", cell),
            Paragraph(f"AUD {line}", cell),
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
    story.append(Paragraph("<b>Amount payable:</b> AUD 990.00", body))
    story.append(Spacer(1, 12))
    story.append(Paragraph("Payment terms: due within 14 days.", body))
    doc.build(story)
    print(f"  [Generated] {output_path}")


def generate_all_mock_invoices(target_dir: str = "./inbox") -> None:
    """Creates a sample set of mock invoice PDFs inside the target directory."""
    os.makedirs(target_dir, exist_ok=True)

    mock_datasets = [
        {
            "invoice_number": "INV-2026-001",
            "vendor_name": "Apex Cloud Solutions Pty Ltd",
            "vendor_address": "Level 14, 100 George St, Sydney NSW 2000",
            "vendor_email": "billing@apexcloud.io",
            "customer_name": "Enterprise Client Inc.",
            "date": "2026-08-10",
            "currency": "USD",
            "payment_terms": "Net 15 days",
            "items": [
                {"description": "Dedicated Cloud Compute - EC2 Instance", "quantity": 2, "unit_price": 450.00, "total": 900.00},
                {"description": "High Performance SSD Storage (1TB)", "quantity": 3, "unit_price": 120.00, "total": 360.00},
                {"description": "Managed Database Service (PostgreSQL)", "quantity": 1, "unit_price": 240.00, "total": 240.00}
            ],
            "total_amount": 1500.00
        },
        {
            "invoice_number": "INV-2026-002",
            "vendor_name": "NextGen Hardware Supplies",
            "vendor_address": "88 Industrial Road, Melbourne VIC 3000",
            "vendor_email": "accounts@nextgenhardware.com",
            "customer_name": "Enterprise Client Inc.",
            "date": "2026-08-15",
            "currency": "AUD",
            "payment_terms": "Net 30 days",
            "items": [
                {"description": "Ergonomic Office Chair - Model X", "quantity": 5, "unit_price": 350.00, "total": 1750.00},
                {"description": "USB-C Dual 4K Display Docking Station", "quantity": 5, "unit_price": 180.00, "total": 900.00}
            ],
            "total_amount": 2650.00
        },
        {
            "invoice_number": "INV-2026-003",
            "vendor_name": "Synthetix AI Consulting",
            "vendor_address": "Suite 4, Innovation Park, Brisbane QLD 4000",
            "vendor_email": "invoicing@synthetix-ai.com",
            "customer_name": "Enterprise Client Inc.",
            "date": "2026-08-20",
            "currency": "USD",
            "payment_terms": "Due on Receipt",
            "items": [
                {"description": "Workflow Automation Architecture Consulting", "quantity": 10, "unit_price": 150.00, "total": 1500.00},
                {"description": "Local LLM Fine-Tuning & Pipeline Setup", "quantity": 1, "unit_price": 850.00, "total": 850.00}
            ],
            "total_amount": 2350.00
        }
    ]

    print(f"=== Generating Mock Invoices into '{target_dir}' ===")
    for idx, data in enumerate(mock_datasets, start=1):
        filename = f"sample_invoice_{idx}_{data['invoice_number']}.pdf"
        output_file = os.path.join(target_dir, filename)
        create_invoice_pdf(output_file, data)

    review_path = os.path.join(target_dir, "sample_invoice_4_INV-2026-004.pdf")
    create_review_invoice_pdf(review_path)

    print("=== All mock invoice PDFs successfully generated! ===\n")


if __name__ == "__main__":
    generate_all_mock_invoices(target_dir="./inbox")