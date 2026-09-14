"""Measures what a required extraction schema does when a field is genuinely absent.

    ./.venv/bin/python evaluation/sentinel_comparison.py [--model llama3.2] [--save results.json]

`schema_comparison.py` showed that making fields required takes extraction from 3/15 to
15/15. It could not show the cost, because every field is present in every one of those
documents. This measures the cost.

Four variants, one variable at a time:

    optional           Optional[...] with a default, what ships today
    required           the same fields with the wrappers removed
    sentinel           required, plus a description telling the model to say NOT_FOUND
    nullable-required  Optional[...] with NO default: in `required`, but null is a legal value

Two things are scored, and they pull in opposite directions:

    on a field that IS present   did it extract the right value?
    on a field that is ABSENT    did it admit that, or invent something?

A schema that scores well on the first and badly on the second is not an improvement. It
has traded a visible failure (an empty field) for an invisible one (a confident wrong
answer), which is worse in a system that touches money.

Note on enforcement: the sentinel cannot be forced through the JSON Schema. Pydantic renders
Union[str, Literal["NOT_FOUND"]] as anyOf[{type:string},{const:NOT_FOUND}], and the first
branch already admits any string, so the constrained decoder gains nothing. The sentinel is
therefore an instruction the model may paraphrase, and this script records the paraphrases.
"""

import argparse
import json
import os
import re
import sys
from typing import List, Optional

from pydantic import BaseModel, Field

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import ollama  # noqa: E402
from pypdf import PdfReader  # noqa: E402

from main import ExtractedInvoice, InvoiceItem  # noqa: E402

FIELDS = ["invoice_number", "vendor_name", "date", "total_amount", "currency"]


class RequiredInvoice(BaseModel):
    invoice_number: str = Field(description="Invoice, Tax Invoice, or Receipt number")
    vendor_name: str = Field(description="Vendor, Supplier, Seller, or Billed-From company name")
    date: str = Field(description="Invoice issue date, bill date, or transaction date in YYYY-MM-DD format if possible")
    total_amount: float = Field(description="Grand total amount, net payable, or balance due as a numeric float")
    currency: str = Field(description="Detected currency code (e.g. AUD, USD, EUR, TWD) or 'Unknown'")
    items: List[InvoiceItem] = Field(default=[], description="List of line items")


class NullableRequiredInvoice(BaseModel):
    """Required, but nullable: `Optional[T]` with NO default.

    Pydantic puts a field with no default into `required` even when its type admits null,
    so the constrained decoder must emit every key (it cannot terminate the object early,
    which is the 2026-08-26 omission defect) and may emit null (so it is never forced to
    invent, which is the group's rule 3).

    `items` is left exactly as RequiredInvoice declares it, so the only variable that
    changes between the two arms is the nullability of the five scalars.
    """
    invoice_number: Optional[str] = Field(description="Invoice, Tax Invoice, or Receipt number. Return null if the document does not contain one.")
    vendor_name: Optional[str] = Field(description="Vendor, Supplier, Seller, or Billed-From company name. Return null if the document does not name one.")
    date: Optional[str] = Field(description="Invoice issue date, bill date, or transaction date in YYYY-MM-DD format if possible. Return null if the document has no date.")
    total_amount: Optional[float] = Field(description="Grand total amount, net payable, or balance due as a numeric float. Return null if the document states no total.")
    currency: Optional[str] = Field(description="Detected currency code (e.g. AUD, USD, EUR, TWD). Return null if the document does not state one.")
    items: List[InvoiceItem] = Field(default=[], description="List of line items")


class SentinelInvoice(BaseModel):
    invoice_number: str = Field(description="Invoice, Tax Invoice, or Receipt number. If the document does not contain one, return exactly NOT_FOUND. Do not guess or substitute another number.")
    vendor_name: str = Field(description="Vendor, Supplier, Seller, or Billed-From company name. If the document does not name one, return exactly NOT_FOUND.")
    date: str = Field(description="Invoice issue date in YYYY-MM-DD format. If the document has no date, return exactly NOT_FOUND.")
    total_amount: float = Field(description="Grand total, net payable, or balance due as a number. If the document states no total, return exactly -1.")
    currency: str = Field(description="3-letter currency code. If the document does not state one, return exactly NOT_FOUND.")
    items: List[InvoiceItem] = Field(default=[], description="List of line items")


# Phrasings that count as the model admitting it found nothing. Deliberately generous: the
# question is whether it refused to invent, not whether it obeyed the exact wording.
ADMISSIONS = {
    "notfound", "notspecified", "notavailable", "none", "null", "na", "n/a", "nan",
    "unknown", "notprovided", "notstated", "missing", "", "-1", "-1.0",
}


def is_admission(value) -> bool:
    return re.sub(r"[^a-z0-9/-]", "", str(value).lower()) in ADMISSIONS


# Test documents. The first is real; the rest are written to be missing specific fields.
def load_documents():
    real = "\n".join(
        (p.extract_text() or "")
        for p in PdfReader(os.path.join(REPO_ROOT, "evaluation", "samples",
                                        "sample_invoice_1_INV-2026-001.pdf")).pages)
    return {
        "full_invoice": {
            "text": real,
            "present": {"invoice_number": "INV-2026-001",
                        "vendor_name": "Apex Cloud Solutions Pty Ltd",
                        "date": "2026-08-10", "total_amount": 1500.00, "currency": "USD"},
            "absent": [],
        },
        "cafe_receipt": {
            "text": "Corner Cafe\nTable 4\n\nLatte            4.50\nCroissant        5.00\n"
                    "Total            9.50\n\nThank you!",
            "present": {"vendor_name": "Corner Cafe", "total_amount": 9.50},
            "absent": ["invoice_number", "date", "currency"],
        },
        "no_vendor": {
            "text": "TAX INVOICE\nInvoice No: 88231\nDate: 12 March 2026\n\n"
                    "Consulting services      AUD 2,000.00\n\nAmount Due   AUD 2,000.00",
            "present": {"invoice_number": "88231", "date": "2026-03-12",
                        "total_amount": 2000.00, "currency": "AUD"},
            "absent": ["vendor_name"],
        },
        "no_total": {
            "text": "Northwind Supplies\nStatement of Account\nRef: ST-4419\nDate: 2026-04-01\n\n"
                    "Opening balance      1,200.00\nPayments received      800.00\n\n"
                    "This statement is for information only.",
            "present": {"vendor_name": "Northwind Supplies", "date": "2026-04-01"},
            "absent": ["total_amount"],
        },
        "sparse_note": {
            "text": "paid cash for taxi to airport\n45 dollars",
            "present": {},
            "absent": ["invoice_number", "vendor_name", "date", "currency"],
        },
    }


PROMPT = 'Extract the invoice fields from this document.\n"""{text}"""'


def normalise(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def correct(field, expected, actual):
    if actual is None:
        return False
    if field == "total_amount":
        try:
            return abs(float(actual) - float(expected)) < 0.01
        except (TypeError, ValueError):
            return False
    if field == "date":
        return "-".join(re.findall(r"\d+", str(actual))) == "-".join(re.findall(r"\d+", str(expected)))
    return normalise(actual) == normalise(expected)


def run(variant, schema, documents, model):
    found_right = found_total = 0
    admitted = invented = 0
    rows = []

    for name, doc in documents.items():
        try:
            response = ollama.chat(
                model=model,
                messages=[{"role": "user", "content": PROMPT.format(text=doc["text"])}],
                format=schema.model_json_schema(),
                options={"temperature": 0.0},
            )
            data = json.loads(response["message"]["content"])
        except Exception as error:
            rows.append({"document": name, "error": str(error)})
            continue

        row = {"document": name, "present": {}, "absent": {}}

        for field, expected in doc["present"].items():
            hit = correct(field, expected, data.get(field))
            found_total += 1
            found_right += hit
            row["present"][field] = {"expected": expected, "got": data.get(field), "correct": hit}

        for field in doc["absent"]:
            value = data.get(field)
            honest = value is None or is_admission(value)
            admitted += honest
            invented += not honest
            row["absent"][field] = {"got": value, "admitted": honest}

        rows.append(row)

    return {
        "variant": variant,
        "extracted_correctly": {"right": found_right, "of": found_total},
        "absent_fields": {"admitted": admitted, "invented": invented},
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description="Optional vs required vs sentinel vs nullable-required schemas.")
    parser.add_argument("--model", default="llama3.2")
    parser.add_argument("--save")
    args = parser.parse_args()

    documents = load_documents()
    absent_count = sum(len(d["absent"]) for d in documents.values())
    present_count = sum(len(d["present"]) for d in documents.values())

    print(f"{len(documents)} documents, {present_count} fields genuinely present, "
          f"{absent_count} genuinely absent.")
    print(f"Model {args.model}, temperature 0.0.\n")

    results = []
    for variant, schema in [("optional", ExtractedInvoice),
                            ("required", RequiredInvoice),
                            ("sentinel", SentinelInvoice),
                            ("nullable-required", NullableRequiredInvoice)]:
        result = run(variant, schema, documents, args.model)
        results.append(result)
        e, a = result["extracted_correctly"], result["absent_fields"]
        print(f"=== {variant} ===")
        print(f"    present fields extracted correctly   {e['right']}/{e['of']}")
        print(f"    absent fields admitted, not invented  {a['admitted']}/{a['admitted'] + a['invented']}")
        for row in result["rows"]:
            for field, detail in row.get("absent", {}).items():
                mark = "admitted" if detail["admitted"] else "INVENTED"
                print(f"        {row['document']:<15} {field:<15} {str(detail['got'])[:24]:<24} {mark}")
        print()

    print("=" * 79)
    print(f"{'variant':<17} | {'extracted right':>16} | {'admitted when absent':>22}")
    print("-" * 79)
    for r in results:
        e, a = r["extracted_correctly"], r["absent_fields"]
        print(f"{r['variant']:<17} | {e['right']:>7}/{e['of']:<8} | "
              f"{a['admitted']:>10}/{a['admitted'] + a['invented']:<11}")
    print("=" * 79)
    print("A variant that extracts well and invents freely is not an improvement: it trades a")
    print("visible failure for an invisible one. Both columns have to be read together.")

    if args.save:
        with open(args.save, "w") as handle:
            json.dump({"model": args.model, "variants": results}, handle, indent=2)
        print(f"\nSaved to {args.save}")


if __name__ == "__main__":
    main()
