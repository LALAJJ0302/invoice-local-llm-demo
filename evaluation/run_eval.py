"""Measures per-field extraction accuracy against hand-transcribed ground truth.

Reads nothing from the live pipeline's inbox/ or archive/, and modifies no file in
the repository. The fallback switch is applied by monkey-patching at runtime so
main.py stays untouched.

Usage:
    python evaluation/run_eval.py                    # default: fallbacks disabled
    python evaluation/run_eval.py --with-fallback    # measure the shipped behaviour
    python evaluation/run_eval.py --model llama3.2
    python evaluation/run_eval.py --save before.json
"""

import argparse
import json
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL_DIR = os.path.join(REPO_ROOT, "evaluation")
SAMPLES_DIR = os.path.join(EVAL_DIR, "samples")
sys.path.insert(0, REPO_ROOT)

from pypdf import PdfReader  # noqa: E402

import generate_mock_invoices  # noqa: E402
from main import ConfidenceValidator, DocumentExtractor  # noqa: E402

FIELDS = ["vendor_name", "invoice_number", "date", "total_amount", "currency"]


def normalise_text(value):
    """Casefold and collapse whitespace so formatting differences are not scored as errors."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def normalise_date(value):
    """Accept YYYY-MM-DD, DD/MM/YYYY and similar; compare on the digits only."""
    digits = re.findall(r"\d+", str(value or ""))
    return "-".join(digits) if digits else ""


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


def ensure_samples():
    existing = [f for f in os.listdir(SAMPLES_DIR)] if os.path.isdir(SAMPLES_DIR) else []
    if not any(f.endswith(".pdf") for f in existing):
        print(f"[setup] Generating sample invoices into {SAMPLES_DIR}")
        generate_mock_invoices.generate_all_mock_invoices(target_dir=SAMPLES_DIR)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="llama3.2")
    parser.add_argument(
        "--with-fallback",
        action="store_true",
        help="Leave _infer_vendor_fallback active. Off by default: a filename-derived "
             "vendor is a guess, not an extraction, and inflates the score.",
    )
    parser.add_argument("--threshold", type=float, default=0.80)
    parser.add_argument("--save", help="Write the results to a JSON file for before/after comparison")
    args = parser.parse_args()

    truth = json.load(open(os.path.join(EVAL_DIR, "ground_truth.json")))["samples"]
    ensure_samples()

    extractor = DocumentExtractor(model_name=args.model)
    if not args.with_fallback:
        extractor._infer_vendor_fallback = lambda raw_text, file_name: None

    validator = ConfidenceValidator(threshold=args.threshold)

    hits = {f: 0 for f in FIELDS}
    rows, validated = [], 0

    for file_name in sorted(truth):
        path = os.path.join(SAMPLES_DIR, file_name)
        if not os.path.exists(path):
            print(f"[skip] missing sample: {file_name}")
            continue

        raw_text = "\n".join((p.extract_text() or "") for p in PdfReader(path).pages).strip()
        data = extractor.extract_invoice_data(raw_text, file_name)
        if data is None:
            print(f"[fail] extraction returned nothing for {file_name}")
            rows.append({"file": file_name, "fields": {f: False for f in FIELDS}, "score": 0.0, "status": "Failed"})
            continue

        score, status = validator.evaluate(data, raw_text)
        validated += status == "Validated"

        result = {}
        for f in FIELDS:
            actual = getattr(data, f, None)
            ok = matches(f, truth[file_name][f], actual)
            hits[f] += ok
            result[f] = {"expected": truth[file_name][f], "actual": actual, "correct": ok}
        rows.append({"file": file_name, "fields": result, "score": score, "status": status})

    total = len(rows)
    if not total:
        print("No samples evaluated.")
        return 1

    mode = "enabled" if args.with_fallback else "disabled"
    print(f"\n=== Extraction accuracy: {args.model} (heuristic fallbacks: {mode}) ===\n")
    print(f"{'Field':<16}{'Correct':>10}{'Accuracy':>12}")
    print("-" * 38)
    for f in FIELDS:
        print(f"{f:<16}{f'{hits[f]}/{total}':>10}{hits[f] / total * 100:>11.1f}%")
    print("-" * 38)
    got, poss = sum(hits.values()), total * len(FIELDS)
    print(f"{'OVERALL':<16}{f'{got}/{poss}':>10}{got / poss * 100:>11.1f}%")
    print(f"\nGate outcome: {validated}/{total} Validated "
          f"({validated / total * 100:.1f}% automation pass rate, threshold {args.threshold})")

    misses = [(r["file"], f, r["fields"][f]) for r in rows if isinstance(r["fields"].get("vendor_name"), dict)
              for f in FIELDS if not r["fields"][f]["correct"]]
    if misses:
        print(f"\n--- Incorrect fields ({len(misses)}) ---")
        for file_name, f, d in misses:
            print(f"{file_name}\n    {f}: expected {d['expected']!r}, got {d['actual']!r}")

    if args.save:
        payload = {"model": args.model, "fallbacks": mode, "threshold": args.threshold,
                   "per_field": {f: {"correct": hits[f], "total": total} for f in FIELDS},
                   "overall": {"correct": got, "total": poss},
                   "validated": validated, "documents": total, "rows": rows}
        with open(args.save, "w") as fh:
            json.dump(payload, fh, indent=2, default=str)
        print(f"\nSaved to {args.save}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
