"""Measures per-field extraction accuracy against hand-transcribed ground truth.

Reads nothing from the live pipeline's inbox/ or archive/, and modifies no file in
the repository. The fallback switch is applied by monkey-patching at runtime so
main.py stays untouched.

The switch covers EVERY repair our code applies to the model's output, discovered
at runtime by naming convention rather than listed here: _infer_*_fallback fills a
field the model omitted, _clean_* rewrites one it returned badly. It used to name
one method. On 2026-09-03 a second fallback was added to main.py, the switch kept
reporting "disabled", and the harness reported 93.3% for a model that scores 66.7%.
A hardcoded list cannot know about a repair added after it was written; a
convention can.

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
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL_DIR = os.path.join(REPO_ROOT, "evaluation")
SAMPLES_DIR = os.path.join(EVAL_DIR, "samples")
sys.path.insert(0, REPO_ROOT)

from pypdf import PdfReader  # noqa: E402

from samples_fixture import ensure_samples  # noqa: E402
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


# Every repair our own code applies to the model's output, by naming convention:
# _infer_*_fallback produces a value the model omitted, _clean_* rewrites one it
# returned. Both are our code, not the model, so both must be switchable off.
FALLBACK_PATTERN = re.compile(r"^(?:_infer_.*_fallback|_clean_.*)$")


def _neutraliser(name):
    """A do-nothing stand-in matching how the pipeline uses each repair.

    Two shapes exist, and getting them the wrong way round corrupts the run rather
    than disabling it. A *transformer* is handed a value or record and returns it,
    so it must return its first argument unchanged. A *producer* returns a value the
    model did not supply, so it must return None.
    """
    if name.startswith("_clean_") or "missing_fields" in name:
        return lambda first, *a, **k: first
    return lambda *a, **k: None


def disable_fallbacks(extractor):
    """Neutralise every fallback on the extractor. Returns the names switched off.

    Refuses to continue if none match. Printing an accuracy figure while claiming
    fallbacks are disabled, having disabled nothing, is the exact failure this
    function exists to prevent, so it fails loudly instead.
    """
    names = sorted(n for n in dir(extractor) if FALLBACK_PATTERN.match(n))
    if not names:
        raise SystemExit(
            "No methods matched _infer_*_fallback or _clean_* on DocumentExtractor.\n"
            "Either main.py renamed them or the convention changed. Refusing to "
            "report a number that claims fallbacks are disabled when nothing was."
        )
    for n in names:
        setattr(extractor, n, _neutraliser(n))
    return names




def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="llama3.2")
    parser.add_argument(
        "--with-fallback",
        action="store_true",
        help="Leave every _infer_*_fallback active, measuring shipped behaviour. Off by "
             "default: a filename-derived vendor or a regex-recovered total is our code "
             "working, not the model, and counting it inflates the score.",
    )
    parser.add_argument("--threshold", type=float, default=0.80)
    parser.add_argument(
        "--rag",
        action="store_true",
        help="Retrieve local anonymised examples and add them to the extraction prompt.",
    )
    parser.add_argument(
        "--rag-limit",
        type=int,
        default=1,
        help="Maximum number of retrieved examples added to each prompt.",
    )
    parser.add_argument(
        "--rag-examples",
        default=os.path.join(EVAL_DIR, "rag_examples.json"),
        help="Path to the local RAG example repository.",
    )
    parser.add_argument("--save", help="Write the results to a JSON file for before/after comparison")
    args = parser.parse_args()

    truth = json.load(open(os.path.join(EVAL_DIR, "ground_truth.json")))["samples"]
    ensure_samples(quiet=False)

    extractor = DocumentExtractor(
        model_name=args.model,
        use_rag=args.rag,
        rag_limit=args.rag_limit,
        rag_examples_path=args.rag_examples,
    )
    disabled = [] if args.with_fallback else disable_fallbacks(extractor)

    validator = ConfidenceValidator(threshold=args.threshold)

    hits = {f: 0 for f in FIELDS}
    rows, validated = [], 0
    latencies = []

    for file_name in sorted(truth):
        path = os.path.join(SAMPLES_DIR, file_name)
        if not os.path.exists(path):
            print(f"[skip] missing sample: {file_name}")
            continue

        raw_text = "\n".join((p.extract_text() or "") for p in PdfReader(path).pages).strip()
        started_at = time.perf_counter()
        data = extractor.extract_invoice_data(raw_text, file_name)
        latency_seconds = round(time.perf_counter() - started_at, 3)
        latencies.append(latency_seconds)
        retrieval = [
            {
                "example_id": item["example_id"],
                "score": item["retrieval_score"],
                "reason": item["retrieval_reason"],
            }
            for item in extractor.last_retrieval
        ]
        if data is None:
            print(f"[fail] extraction returned nothing for {file_name}")
            rows.append({
                "file": file_name,
                "fields": {f: False for f in FIELDS},
                "score": 0.0,
                "status": "Failed",
                "latency_seconds": latency_seconds,
                "retrieval": retrieval,
            })
            continue

        score, status = validator.evaluate(data, raw_text)
        validated += status == "Validated"

        result = {}
        for f in FIELDS:
            actual = getattr(data, f, None)
            ok = matches(f, truth[file_name][f], actual)
            hits[f] += ok
            result[f] = {"expected": truth[file_name][f], "actual": actual, "correct": ok}
        rows.append({
            "file": file_name,
            "fields": result,
            "score": score,
            "status": status,
            "latency_seconds": latency_seconds,
            "retrieval": retrieval,
        })

    total = len(rows)
    if not total:
        print("No samples evaluated.")
        return 1

    mode = "enabled" if args.with_fallback else "disabled"
    rag_mode = "enabled" if args.rag else "disabled"
    # Name what was switched off rather than asserting a state, so a pasted result
    # can be checked by whoever reads it.
    detail = ", ".join(disabled) if disabled else "none, measuring shipped behaviour"
    print(f"\n=== Extraction accuracy: {args.model} ===")
    print(f"    fallbacks {mode}: {detail}\n")
    print(f"    RAG {rag_mode}: limit {args.rag_limit if args.rag else 0}\n")
    print(f"{'Field':<16}{'Correct':>10}{'Accuracy':>12}")
    print("-" * 38)
    for f in FIELDS:
        print(f"{f:<16}{f'{hits[f]}/{total}':>10}{hits[f] / total * 100:>11.1f}%")
    print("-" * 38)
    got, poss = sum(hits.values()), total * len(FIELDS)
    print(f"{'OVERALL':<16}{f'{got}/{poss}':>10}{got / poss * 100:>11.1f}%")
    print(f"\nGate outcome: {validated}/{total} Validated "
          f"({validated / total * 100:.1f}% automation pass rate, threshold {args.threshold})")
    average_latency = round(sum(latencies) / len(latencies), 3)
    print(f"Average model latency: {average_latency:.3f} seconds per document")

    misses = [(r["file"], f, r["fields"][f]) for r in rows if isinstance(r["fields"].get("vendor_name"), dict)
              for f in FIELDS if not r["fields"][f]["correct"]]
    if misses:
        print(f"\n--- Incorrect fields ({len(misses)}) ---")
        for file_name, f, d in misses:
            print(f"{file_name}\n    {f}: expected {d['expected']!r}, got {d['actual']!r}")

    if args.save:
        # "fallbacks" is kept for compatibility with the frozen results_*.json files.
        payload = {"model": args.model, "fallbacks": mode,
                   "fallbacks_disabled": disabled, "threshold": args.threshold,
                   "rag": rag_mode, "rag_limit": args.rag_limit if args.rag else 0,
                   "rag_examples": args.rag_examples if args.rag else None,
                   "average_latency_seconds": average_latency,
                   "per_field": {f: {"correct": hits[f], "total": total} for f in FIELDS},
                   "overall": {"correct": got, "total": poss},
                   "validated": validated, "documents": total, "rows": rows}
        with open(args.save, "w") as fh:
            json.dump(payload, fh, indent=2, default=str)
        print(f"\nSaved to {args.save}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
