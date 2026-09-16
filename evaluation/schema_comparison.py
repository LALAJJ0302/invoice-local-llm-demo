"""Measures whether the extraction schema, not the model, causes the empty fields.

    ./.venv/bin/python evaluation/schema_comparison.py [--model llama3.2] [--save results.json]

The controlled comparison. Everything is held constant except one variable:

    held constant   model, prompt (copied verbatim from main.py), documents,
                    temperature 0.0, the same InvoiceItem definition
    varied          whether the five scalar fields are Optional-with-default,
                    as ExtractedInvoice declares them, or required

This exists because the claim "required fields fix it" was being repeated from a
vault note rather than measured in this repository. It is now reproducible here.

Reads only from evaluation/samples/. Touches no database and edits no file.

What this does NOT show: that extraction is solved. Three synthetic invoices with clean
text layers, no tax, no shipping, no multi-page documents, no scans. It isolates a cause;
it does not measure production accuracy. It also does not measure the trade-off that
required fields introduce, which is that a model forced to answer can no longer stay
silent about a field that is genuinely absent, and may guess instead.
"""

import argparse
import json
import os
import re
import sys
from typing import List

from pydantic import BaseModel, Field

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL_DIR = os.path.join(REPO_ROOT, "evaluation")
SAMPLES_DIR = os.path.join(EVAL_DIR, "samples")
sys.path.insert(0, REPO_ROOT)

import ollama  # noqa: E402
from pypdf import PdfReader  # noqa: E402

from main import ExtractedInvoice, InvoiceItem  # noqa: E402
from samples_fixture import ensure_samples  # noqa: E402

FIELDS = ["invoice_number", "vendor_name", "date", "total_amount", "currency"]


class RequiredInvoice(BaseModel):
    """ExtractedInvoice with the Optional wrappers and defaults removed.

    Field descriptions are copied verbatim so the prompt the model effectively sees is
    identical. The only difference reaching Ollama is that `required` is now populated.
    """

    invoice_number: str = Field(description="Invoice, Tax Invoice, or Receipt number")
    vendor_name: str = Field(description="Vendor, Supplier, Seller, or Billed-From company name")
    date: str = Field(description="Invoice issue date, bill date, or transaction date in YYYY-MM-DD format if possible")
    total_amount: float = Field(description="Grand total amount, net payable, or balance due as a numeric float")
    currency: str = Field(description="Detected currency code (e.g. AUD, USD, EUR, TWD) or 'Unknown'")
    items: List[InvoiceItem] = Field(default=[], description="List of line items")


# Copied verbatim from DocumentExtractor.extract_invoice_data. If that prompt changes,
# this must change with it, or the comparison stops being controlled.
PROMPT = """
        You are an advanced Document Intelligence AI. Extract the invoice fields from the following document into structured JSON.

        Strict Extraction Rules:
        1. "vendor_name": Look at the header, sender, top letterhead, or logo text. If completely missing, return null.
        2. "invoice_number": Look for "Invoice #", "Tax Invoice No.", "Receipt No.", "Order #", or "Ref #".
        3. "date": Extract the invoice date, bill date, or transaction date.
        4. "total_amount": Locate the final payable amount ("Total", "Grand Total", "Amount Due", "Total AUD/USD"). Return as a numeric float only (e.g. 1500.50). Do not return 0.0 unless the invoice explicitly says 0.
        5. "currency": Detect the explicit currency (e.g. USD, AUD, EUR, GBP, CAD, TWD, $). If ambiguous or not found, return "Unknown". Do NOT assume USD.
        6. "items": Extract line items if visible.

        Document Content:
        \"\"\"{text}\"\"\"
        """


def normalise_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def normalise_date(value):
    return "-".join(re.findall(r"\d+", str(value or "")))


def matches(field, expected, actual):
    if actual is None:
        return False
    if field == "total_amount":
        try:
            return abs(float(actual) - float(expected)) < 0.01
        except (TypeError, ValueError):
            return False
    if field == "date":
        return normalise_date(actual) == normalise_date(expected)
    return normalise_text(actual) == normalise_text(expected)


def read_text(path):
    return "\n".join((page.extract_text() or "") for page in PdfReader(path).pages)


def run_variant(label, schema, ground_truth, model):
    print(f"\n=== {label} ===")
    print(f"    required list sent to Ollama: {schema.model_json_schema().get('required', '(absent)')}")

    per_field = {f: 0 for f in FIELDS}
    rows = []

    for file_name, truth in sorted(ground_truth.items()):
        # ensure_samples() generates the gitignored PDFs if they are absent. Without it this
        # loop skipped every document on a fresh clone and reported 0/15 as though measured.
        path = os.path.join(ensure_samples(), file_name)
        if not os.path.exists(path):
            print(f"    [skip] missing sample {file_name}")
            continue

        try:
            response = ollama.chat(
                model=model,
                messages=[{"role": "user", "content": PROMPT.format(text=read_text(path))}],
                format=schema.model_json_schema(),
                options={"temperature": 0.0},
            )
            extracted = json.loads(response["message"]["content"])
        except Exception as error:
            print(f"    [fail] {file_name}: {error}")
            rows.append({"file": file_name, "error": str(error)})
            continue

        row = {"file": file_name, "fields": {}}
        print(f"    {file_name}")
        for field in FIELDS:
            hit = matches(field, truth[field], extracted.get(field))
            per_field[field] += hit
            row["fields"][field] = {"expected": truth[field], "got": extracted.get(field), "correct": hit}
            print(f"        {field:<15} {str(extracted.get(field))[:34]:<34} {'ok' if hit else 'WRONG'}")
        rows.append(row)

    total = sum(per_field.values())
    denominator = len(ground_truth) * len(FIELDS)
    print(f"    {' | '.join(f'{f} {per_field[f]}/{len(ground_truth)}' for f in FIELDS)}")
    print(f"    OVERALL {total}/{denominator} ({total / denominator * 100:.1f}%)")

    return {"label": label, "per_field": per_field, "overall": {"correct": total, "total": denominator}, "rows": rows}


def main():
    parser = argparse.ArgumentParser(description="Optional vs required extraction schema.")
    parser.add_argument("--model", default="llama3.2")
    parser.add_argument("--save", help="write the full result to this JSON file")
    args = parser.parse_args()

    with open(os.path.join(EVAL_DIR, "ground_truth.json")) as handle:
        ground_truth = json.load(handle)["samples"]

    print("Controlled comparison: the schema is the only variable.")
    print(f"Model {args.model}, temperature 0.0, prompt copied verbatim from main.py.")

    results = [
        run_variant("Optional with defaults (what ships today)", ExtractedInvoice, ground_truth, args.model),
        run_variant("Required fields (the proposed change)", RequiredInvoice, ground_truth, args.model),
    ]

    before, after = results[0]["overall"], results[1]["overall"]
    print("\n" + "=" * 62)
    print(f"  Optional  {before['correct']}/{before['total']}  ({before['correct'] / before['total'] * 100:.1f}%)")
    print(f"  Required  {after['correct']}/{after['total']}  ({after['correct'] / after['total'] * 100:.1f}%)")
    print("=" * 62)
    print("The model, the prompt and the documents are identical across both rows.")
    print("n=3 synthetic invoices. This isolates a cause; it does not measure accuracy.")

    if args.save:
        with open(args.save, "w") as handle:
            json.dump({"model": args.model, "variants": results}, handle, indent=2)
        print(f"\nSaved to {args.save}")


if __name__ == "__main__":
    main()
