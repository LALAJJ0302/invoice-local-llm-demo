"""Checks whether a model can be trusted with constrained JSON decoding, before accuracy.

Accuracy is meaningless from a model that cannot return valid JSON, or that ignores the
schema's `required` list. Those two failures look identical to the schema defect this
project already documented: fields silently absent. They have to be separated first.

Three checks per model, each run REPEATS times because the decoder is not deterministic:

  valid_json   the response parses at all
  required     with every field required, are they all present
  optional     with every field Optional (what ships today), how many appear

The gap between `required` and `optional` on the SAME model, prompt and document is the
schema effect isolated. Measured per model rather than assumed to be constant.

Usage:
    python evaluation/model_compatibility.py
    python evaluation/model_compatibility.py --models llama3.2:3b,qwen2.5:7b --repeats 5
"""

import argparse
import json
import os
import statistics
import sys
import time
from typing import List, Optional

import ollama
from pydantic import BaseModel, Field
from pypdf import PdfReader

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL_DIR = os.path.join(REPO_ROOT, "evaluation")
SAMPLES_DIR = os.path.join(EVAL_DIR, "samples")
sys.path.insert(0, REPO_ROOT)

from samples_fixture import ensure_samples  # noqa: E402

FIELDS = ["invoice_number", "vendor_name", "date", "total_amount", "currency"]

DEFAULT_MODELS = ["llama3.2:3b", "gemma3:4b", "qwen2.5:7b",
                  "llama3.1:8b", "phi4:14b", "qwen3:14b"]


class OptionalInvoice(BaseModel):
    """What main.py ships today. Every field Optional, so `required` comes out empty."""
    invoice_number: Optional[str] = Field(default=None)
    vendor_name: Optional[str] = Field(default=None)
    date: Optional[str] = Field(default=None)
    total_amount: Optional[float] = Field(default=0.0)
    currency: Optional[str] = Field(default="Unknown")


class RequiredInvoice(BaseModel):
    """The proposed change. Every field required, so the decoder cannot stop early."""
    invoice_number: str
    vendor_name: str
    date: str
    total_amount: float
    currency: str


PROMPT = """Extract the invoice fields from the document below as JSON.

1. "invoice_number": the invoice, tax invoice or receipt number.
2. "vendor_name": the vendor, supplier or billed-from company name. The company only, not
   the field label.
3. "date": the issue date, in YYYY-MM-DD format.
4. "total_amount": the grand total or amount due, as a number.
5. "currency": the currency code, e.g. AUD, USD, EUR.

Document Content:
\"\"\"{text}\"\"\"
"""




def read_sample() -> str:
    """One document is enough. This measures decoder behaviour, not extraction accuracy."""
    ensure_samples()
    path = sorted(f for f in os.listdir(SAMPLES_DIR) if f.endswith(".pdf"))[0]
    full = os.path.join(SAMPLES_DIR, path)
    return "\n".join((p.extract_text() or "") for p in PdfReader(full).pages).strip()


def probe(model: str, schema_cls, text: str, timeout: int):
    """One call. Returns (parsed_or_None, seconds, error_or_None)."""
    started = time.time()
    try:
        response = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": PROMPT.format(text=text)}],
            format=schema_cls.model_json_schema(),
            options={"temperature": 0, "num_predict": 512},
        )
        elapsed = time.time() - started
        return json.loads(response["message"]["content"]), elapsed, None
    except json.JSONDecodeError as error:
        return None, time.time() - started, f"invalid JSON: {error}"
    except Exception as error:                      # noqa: BLE001
        return None, time.time() - started, str(error)[:90]


def filled(parsed) -> int:
    """Fields carrying a usable value, not a default backfilled by Pydantic."""
    if not parsed:
        return 0
    count = 0
    for f in FIELDS:
        value = parsed.get(f)
        if value in (None, "", 0, 0.0, "Unknown", "None", "null"):
            continue
        count += 1
    return count


def assess(model: str, text: str, repeats: int, timeout: int) -> dict:
    row = {"model": model, "valid": 0, "runs": repeats, "errors": [],
           "required_filled": [], "optional_filled": [], "seconds": []}

    for _ in range(repeats):
        parsed, secs, error = probe(model, RequiredInvoice, text, timeout)
        row["seconds"].append(secs)
        if error:
            row["errors"].append(error)
            continue
        row["valid"] += 1
        row["required_filled"].append(filled(parsed))

    for _ in range(repeats):
        parsed, _, error = probe(model, OptionalInvoice, text, timeout)
        if not error:
            row["optional_filled"].append(filled(parsed))

    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
    parser.add_argument("--repeats", type=int, default=3,
                        help="Runs per schema. The decoder is not deterministic.")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--save", default=os.path.join(EVAL_DIR, "results_model_compatibility.json"))
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    installed = {m["model"] for m in ollama.list()["models"]}
    text = read_sample()

    print(f"\n=== Constrained-decoding compatibility, {args.repeats} runs per schema ===")
    print("Measured before accuracy: a model that cannot return valid JSON, or that ignores")
    print("`required`, fails the same way the schema defect does. They must be told apart.\n")
    print(f"{'model':<16}{'valid':>7}{'required':>11}{'optional':>11}{'sec/call':>10}  note")
    print("-" * 74)

    rows = []
    for model in models:
        if model not in installed and f"{model}:latest" not in installed:
            print(f"{model:<16}{'--':>7}{'--':>11}{'--':>11}{'--':>10}  not installed")
            continue

        row = assess(model, text, args.repeats, args.timeout)
        rows.append(row)

        req = statistics.mean(row["required_filled"]) if row["required_filled"] else 0
        opt = statistics.mean(row["optional_filled"]) if row["optional_filled"] else 0
        sec = statistics.mean(row["seconds"]) if row["seconds"] else 0
        note = row["errors"][0] if row["errors"] else ("honours required" if req == 5 else
                                                       "INCOMPLETE under required")
        print(f"{model:<16}{row['valid']}/{row['runs']:<5}{req:>10.1f}/5{opt:>10.1f}/5"
              f"{sec:>10.1f}  {note}")

    if rows:
        with open(args.save, "w") as handle:
            json.dump({"repeats": args.repeats, "rows": rows}, handle, indent=2)
        print(f"\nSaved to {args.save}")
        print("\n'required' vs 'optional' on the same model, prompt and document is the")
        print("schema effect isolated. A model where they match is one where the schema")
        print("is not the variable, and its accuracy needs a different explanation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
