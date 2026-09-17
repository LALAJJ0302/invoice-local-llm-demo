"""The full 2x2: prompt against schema, measured rather than inferred.

Why this exists. schema_comparison.py varies the SCHEMA while holding main.py's prompt
constant, and finds 3/15 against 15/15. That isolates the schema as *a* cause, and the
project has been reporting it as *the* cause.

On 2026-09-08 the compatibility probe returned 5/5 filled fields under BOTH schemas, on
all five candidate models, using a cleaner prompt. If that holds against ground truth,
the schema is not sufficient to cause the failure and the prompt was doing much of the
work. That is a different claim from the one in our documents, so it gets measured on all
three documents against ground truth, not asserted from one probe on one file.

    ORIGINAL prompt x Optional schema     the code as it shipped on 2026-08-26
    ORIGINAL prompt x Required schema     the proposed fix
    IMPROVED prompt x Optional schema     the cell nobody has measured
    IMPROVED prompt x Required schema     both fixes together

Held constant: model, documents, ground truth, temperature 0.
Varied: exactly two things, each with two levels.

Usage:
    python evaluation/prompt_schema_2x2.py --model llama3.2:latest
"""

import argparse
import json
import os
import sys
from typing import Optional

import ollama
from pydantic import BaseModel, Field
from pypdf import PdfReader

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL_DIR = os.path.join(REPO_ROOT, "evaluation")
SAMPLES_DIR = os.path.join(EVAL_DIR, "samples")
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, EVAL_DIR)

from samples_fixture import ensure_samples  # noqa: E402
from run_eval_shim import matches  # noqa: E402  (thin re-export, see bottom of file)

FIELDS = ["invoice_number", "vendor_name", "date", "total_amount", "currency"]


class OptionalInvoice(BaseModel):
    """Verbatim from main.py as it shipped. Pydantic emits an empty `required` list."""
    invoice_number: Optional[str] = Field(default=None, description="Invoice, Tax Invoice, or Receipt number")
    vendor_name: Optional[str] = Field(default=None, description="Vendor, Supplier, Seller, or Billed-From company name")
    date: Optional[str] = Field(default=None, description="Invoice issue date, bill date, or transaction date in YYYY-MM-DD format if possible")
    total_amount: Optional[float] = Field(default=0.0, description="Grand total amount, net payable, or balance due as a numeric float")
    currency: Optional[str] = Field(default="Unknown", description="Detected currency code (e.g. AUD, USD, EUR, TWD) or 'Unknown'")


class RequiredInvoice(BaseModel):
    """Same descriptions, so the only difference is which fields may be omitted."""
    invoice_number: str = Field(description="Invoice, Tax Invoice, or Receipt number")
    vendor_name: str = Field(description="Vendor, Supplier, Seller, or Billed-From company name")
    date: str = Field(description="Invoice issue date, bill date, or transaction date in YYYY-MM-DD format if possible")
    total_amount: float = Field(description="Grand total amount, net payable, or balance due as a numeric float")
    currency: str = Field(description="Detected currency code (e.g. AUD, USD, EUR, TWD) or 'Unknown'")


class NullableRequiredInvoice(BaseModel):
    """Optional[...] with NO default: Pydantic lists every field in `required`, and null is
    a legal value for each. Same descriptions again, so nullability is the only variable.

    This is the fifth cell, not part of the two-by-two. It exists to confirm the variant
    proposed in `nullable-required-schema-spec.md` still reaches the ceiling on complete
    documents, having been measured on absent fields in `sentinel_comparison.py`.
    """
    invoice_number: Optional[str] = Field(description="Invoice, Tax Invoice, or Receipt number")
    vendor_name: Optional[str] = Field(description="Vendor, Supplier, Seller, or Billed-From company name")
    date: Optional[str] = Field(description="Invoice issue date, bill date, or transaction date in YYYY-MM-DD format if possible")
    total_amount: Optional[float] = Field(description="Grand total amount, net payable, or balance due as a numeric float")
    currency: Optional[str] = Field(description="Detected currency code (e.g. AUD, USD, EUR, TWD) or 'Unknown'")


# NOT a copy. Imported from schema_comparison.py, which took it verbatim from main.py on
# 2026-08-28 while the original prompt was still in the tree. Luke rewrote main.py's prompt
# on 2026-09-03, so the original can no longer be copied from main.py at all.
#
# A first draft of this file reconstructed the prompt from memory and labelled it ORIGINAL.
# It scored 15/15 where the real one scores 3/15, which would have been reported as the
# project's central finding failing to reproduce. Import, never retype.
from schema_comparison import PROMPT as ORIGINAL  # noqa: E402

# The 2026-09-08 probe prompt: numbered, explicit about the label-not-the-value trap, and
# it states the date format. No other difference in kind.
IMPROVED = """Extract the invoice fields from the document below as JSON.

1. "invoice_number": the invoice, tax invoice or receipt number.
2. "vendor_name": the vendor, supplier or billed-from company name. The company only, not
   the field label.
3. "date": the issue date, in YYYY-MM-DD format.
4. "total_amount": the grand total or amount due, as a number.
5. "currency": the currency code, e.g. AUD, USD, EUR.

Never leave a field out if its value is printed in the document.

Document Content:
\"\"\"{text}\"\"\"
"""


def read_text(path):
    return "\n".join((p.extract_text() or "") for p in PdfReader(path).pages).strip()




def run_cell(prompt_template, schema_cls, truth, model):
    hits = {f: 0 for f in FIELDS}
    detail = []
    for file_name in sorted(truth):
        path = os.path.join(SAMPLES_DIR, file_name)
        if not os.path.exists(path):
            continue
        try:
            response = ollama.chat(
                model=model,
                messages=[{"role": "user",
                           "content": prompt_template.format(text=read_text(path))}],
                format=schema_cls.model_json_schema(),
                options={"temperature": 0},
            )
            parsed = json.loads(response["message"]["content"])
        except Exception as error:                      # noqa: BLE001
            detail.append({"file": file_name, "error": str(error)[:80]})
            continue

        row = {"file": file_name, "fields": {}}
        for field in FIELDS:
            actual = parsed.get(field)
            ok = matches(field, truth[file_name][field], actual)
            hits[field] += ok
            row["fields"][field] = {"expected": truth[file_name][field],
                                    "actual": actual, "correct": ok}
        detail.append(row)

    return {"per_field": hits, "correct": sum(hits.values()),
            "total": len(truth) * len(FIELDS), "rows": detail}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="llama3.2:latest")
    parser.add_argument("--save", default=os.path.join(EVAL_DIR, "results_prompt_schema_2x2.json"))
    args = parser.parse_args()

    ensure_samples()
    truth = json.load(open(os.path.join(EVAL_DIR, "ground_truth.json")))["samples"]

    cells = [
        ("ORIGINAL prompt", "Optional schema", ORIGINAL, OptionalInvoice),
        ("ORIGINAL prompt", "Required schema", ORIGINAL, RequiredInvoice),
        ("IMPROVED prompt", "Optional schema", IMPROVED, OptionalInvoice),
        ("IMPROVED prompt", "Required schema", IMPROVED, RequiredInvoice),
        # Fifth cell, outside the grid. See nullable-required-schema-spec.md.
        ("IMPROVED prompt", "Nullable-required schema", IMPROVED, NullableRequiredInvoice),
    ]

    print(f"\n=== Prompt x Schema, {args.model}, temperature 0 ===")
    print("Model, documents and ground truth identical across every cell.\n")
    print(f"{'prompt':<18}{'schema':<26}{'overall':>10}   per-field")
    print("-" * 86)

    results = []
    for prompt_label, schema_label, template, schema_cls in cells:
        cell = run_cell(template, schema_cls, truth, args.model)
        cell.update({"prompt": prompt_label, "schema": schema_label})
        results.append(cell)
        per = " ".join(f"{f[:4]}:{cell['per_field'][f]}" for f in FIELDS)
        print(f"{prompt_label:<18}{schema_label:<26}"
              f"{cell['correct']:>3}/{cell['total']:<6}   {per}")

    with open(args.save, "w") as handle:
        json.dump({"model": args.model, "cells": results}, handle, indent=2)
    print(f"\nSaved to {args.save}")

    grid = {(c["prompt"], c["schema"]): c["correct"] for c in results}
    # The fifth cell is deliberately excluded from the grid reading below: it varies a third
    # factor and would turn a clean two-by-two into an unbalanced design.
    orig_opt = grid[("ORIGINAL prompt", "Optional schema")]
    orig_req = grid[("ORIGINAL prompt", "Required schema")]
    impr_opt = grid[("IMPROVED prompt", "Optional schema")]

    print("\n--- reading the grid ---")
    print(f"  schema effect, under the ORIGINAL prompt:  {orig_opt} -> {orig_req}")
    print(f"  prompt effect, under the Optional schema:  {orig_opt} -> {impr_opt}")
    if impr_opt >= orig_req:
        print("\n  The prompt alone reaches what the schema fix reaches. On this evidence the")
        print("  schema is A cause and not THE cause, and the report must say so.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
