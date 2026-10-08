"""Generate the 30-document synthetic held-out extraction benchmark."""

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

EVAL_DIR = Path(__file__).resolve().parent
SAMPLES_DIR = EVAL_DIR / "extended_samples"

CASES = [('ext_01.pdf', 'Blue Harbour Logistics Pty Ltd', 'BHL-7841', '2026-01-07', 1842.75, 'AUD', 'standard'), ('ext_02.pdf', 'Orchid Medical Systems', 'OMS/26/118', '2026-02-14', 9275.4, 'USD', 'compact'), ('ext_03.pdf', 'Cedar Ridge Analytics', 'CRA-00391', '2026-03-02', 3150.0, 'GBP', 'remittance'), ('ext_04.pdf', 'Kestrel Office Interiors', 'KOI-8807', '2026-03-19', 2468.35, 'AUD', 'tax'), ('ext_05.pdf', 'Meridian Legal Research', 'MLR-2026-44', '2026-04-05', 5800.0, 'USD', 'multi_date'), ('ext_06.pdf', 'Summit Renewable Parts', 'SRP-6102', '2026-04-22', 11760.9, 'EUR', 'reverse'), ('ext_07.pdf', 'Copper Finch Studios', 'CFS-0926', '2026-05-01', 1325.5, 'AUD', 'standard'), ('ext_08.pdf', 'North Quay Safety Equipment', 'NQ-55109', '2026-05-16', 6420.0, 'NZD', 'compact'), ('ext_09.pdf', 'Atlas Bioinformatics Lab', 'ABL-2026-73', '2026-05-29', 8888.88, 'USD', 'remittance'), ('ext_10.pdf', 'Willow Creek Catering', 'WCC-1408', '2026-06-03', 2197.25, 'AUD', 'tax'), ('ext_11.pdf', 'Ember Network Services', 'ENS-77820', '2026-06-18', 4750.0, 'SGD', 'multi_date'), ('ext_12.pdf', 'Pacific Archive Solutions', 'PAS/00917', '2026-07-01', 10240.6, 'AUD', 'reverse'), ('ext_13.pdf', 'Juniper Training Collective', 'JTC-6631', '2026-07-09', 3560.0, 'CAD', 'standard'), ('ext_14.pdf', 'Red Gum Laboratory Supplies', 'RGLS-2245', '2026-07-21', 7999.95, 'AUD', 'compact'), ('ext_15.pdf', 'Silverline Translation Bureau', 'STB-1066', '2026-08-02', 1680.45, 'EUR', 'remittance'), ('ext_16.pdf', 'Eastbridge Civil Design', 'ECD-2026-81', '2026-08-17', 12550.0, 'AUD', 'tax'), ('ext_17.pdf', 'Nimbus Research Instruments', 'NRI-48003', '2026-08-30', 22100.75, 'USD', 'multi_date'), ('ext_18.pdf', 'Harbourlight Event Services', 'HES-7310', '2026-09-04', 4932.1, 'NZD', 'reverse'), ('ext_19.pdf', 'Ironbark Data Security', 'IDS-260919', '2026-09-19', 6750.0, 'AUD', 'standard'), ('ext_20.pdf', 'Golden Wattle Publishing', 'GWP-9914', '2026-09-28', 2875.3, 'GBP', 'compact'), ('ext_21.pdf', 'Lighthouse Marine Testing', 'LMT-4402', '2026-10-06', 14420.0, 'AUD', 'remittance'), ('ext_22.pdf', 'Fernhill Community Health', 'FCH-12608', '2026-10-13', 5340.8, 'CAD', 'tax'), ('ext_23.pdf', 'Quartz Automation Works', 'QAW/2026/57', '2026-10-25', 19850.0, 'USD', 'multi_date'), ('ext_24.pdf', 'Southern Cross Calibration', 'SCC-87031', '2026-11-02', 7125.65, 'AUD', 'reverse'), ('ext_25.pdf', 'Mosaic Urban Planning', 'MUP-3055', '2026-11-11', 9600.0, 'SGD', 'standard'), ('ext_26.pdf', 'Tamarind Food Technology', 'TFT-6127', '2026-11-19', 4389.7, 'AUD', 'compact'), ('ext_27.pdf', 'Aurora Geological Services', 'AGS-2026-204', '2026-12-01', 17325.25, 'USD', 'remittance'), ('ext_28.pdf', 'Banksia Workplace Consulting', 'BWC-9408', '2026-12-08', 3995.0, 'AUD', 'tax'), ('ext_29.pdf', 'Vertex Agricultural Robotics', 'VAR-11802', '2026-12-17', 26400.0, 'EUR', 'multi_date'), ('ext_30.pdf', 'Oceanview Compliance Group', 'OCG-7609', '2026-12-23', 6150.9, 'NZD', 'reverse')]


def _money(value):
    return f"{value:,.2f}"


def _lines(vendor, number, date, total, currency, layout):
    subtotal = round(total / 1.1, 2)
    tax = round(total - subtotal, 2)
    item_one = round(subtotal * 0.6, 2)
    item_two = round(subtotal - item_one, 2)
    common_items = [
        f"Professional service package  1 x {currency} {_money(item_one)}  {_money(item_one)}",
        f"Support and materials         1 x {currency} {_money(item_two)}  {_money(item_two)}",
    ]
    if layout == "standard":
        return [
            vendor, "INVOICE", f"Invoice #: {number}", f"Invoice Date: {date}",
            f"Currency: {currency}", "Description  Qty  Unit Price  Amount", *common_items,
            f"Subtotal {currency} {_money(subtotal)}", f"Tax {currency} {_money(tax)}",
            f"GRAND TOTAL {currency} {_money(total)}",
        ]
    if layout == "compact":
        return [
            f"{vendor} | COMMERCIAL INVOICE", f"Reference {number} | Issued {date}",
            f"Settlement currency {currency}", *common_items,
            f"Goods and services {_money(subtotal)}", f"Tax component {_money(tax)}",
            f"NET PAYABLE: {currency} {_money(total)}",
        ]
    if layout == "remittance":
        return [
            "PAYMENT REQUEST", f"From: {vendor}", f"Payment reference: {number}",
            f"Billing date: {date}", f"Denomination: {currency}", *common_items,
            f"Pre-tax amount {currency} {_money(subtotal)}", f"Tax {currency} {_money(tax)}",
            f"AMOUNT TO REMIT {currency} {_money(total)}",
        ]
    if layout == "tax":
        return [
            "TAX DOCUMENT", vendor, f"Document ID: {number}", f"Issue date: {date}",
            f"All amounts in {currency}", "ITEMS", *common_items,
            f"SUBTOTAL {_money(subtotal)}", f"GST/VAT {_money(tax)}",
            f"BALANCE DUE {currency} {_money(total)}",
        ]
    if layout == "multi_date":
        return [
            vendor, "SERVICE INVOICE", "Service period: 2026-01-01 to 2026-01-31",
            f"Invoice Date: {date}", "Due Date: 2027-01-15", f"Invoice No. {number}",
            f"Account currency: {currency}", *common_items,
            f"Subtotal {_money(subtotal)}", f"Tax {_money(tax)}",
            f"TOTAL AMOUNT DUE {currency} {_money(total)}",
        ]
    return [
        "CUSTOMER PAYMENT ADVICE", f"TOTAL PAYABLE {currency} {_money(total)}",
        f"Currency code: {currency}", f"Tax included: {_money(tax)}",
        *common_items, f"Invoice identifier: {number}", f"Issued on: {date}",
        "Supplier details", vendor,
    ]


def generate_extended_samples(target_dir=SAMPLES_DIR):
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    for name, vendor, number, date, total, currency, layout in CASES:
        pdf = canvas.Canvas(str(target / name), pagesize=A4)
        pdf.setTitle(f"Held-out invoice {number}")
        y = 800
        for index, line in enumerate(_lines(vendor, number, date, total, currency, layout)):
            pdf.setFont("Helvetica-Bold" if index in {0, 1} else "Helvetica", 12 if index < 2 else 10)
            pdf.drawString(52, y, line)
            y -= 24 if index < 2 else 19
        pdf.save()
    return str(target)


def ensure_extended_samples(target_dir=SAMPLES_DIR, quiet=True):
    target = Path(target_dir)
    existing = list(target.glob("*.pdf")) if target.exists() else []
    if len(existing) != len(CASES):
        if not quiet:
            print(f"[setup] Generating {len(CASES)} extended samples into {target}")
        generate_extended_samples(target)
    return str(target)


if __name__ == "__main__":
    print(ensure_extended_samples(quiet=False))
