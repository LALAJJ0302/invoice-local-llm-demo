"""Classifies every extraction error already on disk. Item 15, the second half of JJ's item 3.

`model-evaluation-spec.md` says "counting right and wrong is not analysis" and lists six error
classes. Nothing implemented it. This does, over results files already collected, so no model
re-run is needed and the numbers cannot drift from the ones the report quotes.

The finding that motivated it: `sentinel_comparison.py` reports `invented: 3`. Read those three
values against the document text they came from and one is invented, two are mislocated. A
mislocation means the model found a real string and filed it under the wrong key, which a later
check can catch because the value is on the page. An invention means a plausible number that
appears nowhere, which nothing downstream can catch. Reporting them as one number hides that.

Every rule here is mechanical. Nothing is classified by reading it and deciding, for the same
reason `retrieval_eval.py` judges relevance by rule: a measurement a marker cannot recompute is
not a measurement.

Reads only. Touches no shipped file, runs no model, needs no Ollama.

Usage:
    python evaluation/error_taxonomy.py
    python evaluation/error_taxonomy.py --save results_error_taxonomy.json
    python evaluation/error_taxonomy.py --detail
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
sys.path.insert(0, EVAL_DIR)

from datetime import date  # noqa: E402

from dateutil import parser as date_parser  # noqa: E402
from pypdf import PdfReader  # noqa: E402

import sentinel_comparison  # noqa: E402
from main import ConfidenceValidator  # noqa: E402

# Ordered. The first rule that fires wins, so every error lands in exactly one class.
CLASSES = ("omitted", "placeholder", "malformed", "caption", "truncated", "mislocated", "invented")

# Strings that mean "no value" while still occupying the field. Distinguished from `omitted`
# because the pipeline stores them: a vendor_name of 'Not specified' reaches SQLite as text and
# reads as a company name to anything downstream, where a NULL does not.
NO_VALUE_TOKENS = frozenset({
    "", "none", "null", "n/a", "na", "nil", "unknown", "not specified",
    "not available", "not provided", "not found", "no value",
})

MONEY_STRIP = re.compile(r"[^\d.\-]")
WHITESPACE = re.compile(r"\s+")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# -- normalising -------------------------------------------------------------

def _text(value):
    """Casefolded, whitespace-collapsed string form. None becomes None, not 'none'."""
    if value is None:
        return None
    return WHITESPACE.sub(" ", str(value)).strip().casefold()


def _is_placeholder(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        # 0.0 is the default the Optional schema fills in, not a total anyone billed.
        return float(value) == 0.0
    return _text(value) in NO_VALUE_TOKENS


def _as_money(value):
    """Best-effort number. Returns None when the value is not a number in any format."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    stripped = MONEY_STRIP.sub("", str(value))
    if not stripped or stripped in ("-", ".", "-."):
        return None
    try:
        return float(stripped)
    except ValueError:
        return None


def _as_date(value):
    """ISO date string, or None when it does not parse.

    An already-ISO value is returned without going near dateutil. This is not an optimisation:
    `date_parser.parse('2026-03-12', dayfirst=True)` returns 3 December, because dayfirst is
    applied to the last two components whatever the shape of the string. Every expected value in
    ground_truth.json is ISO, so without this branch the taxonomy corrupts its own ground truth
    and reports correctly-formatted dates as mislocated.

    Everything else is parsed dayfirst, because the documents are Australian: 03/04/2026 is
    3 April. Guessing the other way turns a correct extraction into a wrong one.
    """
    if value is None or isinstance(value, (int, float)):
        return None
    text = str(value).strip()
    if ISO_DATE.match(text):
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            return None
    try:
        return date_parser.parse(text, dayfirst=True).date().isoformat()
    except (ValueError, OverflowError, TypeError):
        return None


def canonical(field, value):
    """The field's comparable form. Two values that canonicalise the same differ only in format."""
    if field == "date":
        return _as_date(value)
    if field in ("total_amount", "subtotal"):
        return _as_money(value)
    return _text(value)


def _number_spellings(number):
    """How a number could be written in a document: 2000, 2000.00, 2,000, 2,000.00."""
    out = set()
    for places in (0, 2):
        plain = f"{number:,.{places}f}"
        out.add(plain)
        out.add(plain.replace(",", ""))
    return {s for s in out if s}


def appears_in(value, document_text):
    """True when the value can be found in the document, as text or as a written number.

    The whole invented / mislocated split rests on this, so numbers are searched in every
    spelling a document might use. Without the comma-grouped forms a real 2,000.00 on the page
    would be called invented.
    """
    if value is None or document_text is None:
        return False
    haystack = _text(document_text)
    number = _as_money(value)
    if number is not None and not isinstance(value, str):
        return any(_text(s) in haystack for s in _number_spellings(number))
    needle = _text(value)
    if not needle:
        return False
    if needle in haystack:
        return True
    if number is not None:
        return any(_text(s) in haystack for s in _number_spellings(number))
    return False


def _caption_stripped(value):
    """The value with a leading field caption removed, or None when it carries no caption."""
    if not isinstance(value, str):
        return None
    lowered = value.strip().casefold()
    for label in ConfidenceValidator.VENDOR_LABELS:
        if lowered.startswith(label):
            return value.strip()[len(label):].strip()
    return None


# -- the classifier ----------------------------------------------------------

def classify(field, expected, actual, document_text):
    """Assign one class to one wrong field value. Returns (class, reason).

    `expected` is None for a field that is genuinely absent from the document, in which case the
    only right answer was null and anything else is an error.
    """
    if actual is None:
        return "omitted", "value is null"

    if _is_placeholder(actual):
        return "placeholder", f"{actual!r} means 'no value' but occupies the field"

    if expected is not None:
        want, got = canonical(field, expected), canonical(field, actual)
        if want is not None and want == got:
            return "malformed", f"{actual!r} normalises to {want!r}, the expected value"

        stripped = _caption_stripped(actual)
        if stripped is not None and canonical(field, stripped) == want:
            return "caption", f"{actual!r} is the field label plus the correct value"

        want_text, got_text = _text(expected), _text(actual)
        if want_text and got_text and want_text != got_text and want_text.startswith(got_text):
            return "truncated", f"{actual!r} is the start of {expected!r}, cut short"

    if appears_in(actual, document_text):
        return "mislocated", f"{actual!r} is in the document, but not as this field"

    return "invented", f"{actual!r} appears nowhere in the document"


# -- loading what is already on disk -----------------------------------------

def _results(name):
    with open(os.path.join(EVAL_DIR, name), encoding="utf-8") as handle:
        return json.load(handle)


def _sample_text(file_name):
    path = os.path.join(SAMPLES_DIR, file_name)
    if not os.path.exists(path):
        return None
    return "\n".join((page.extract_text() or "") for page in PdfReader(path).pages)


def _got(entry):
    """Results files disagree on the key: run_eval writes `actual`, the comparisons write `got`."""
    return entry["actual"] if "actual" in entry else entry.get("got")


def _rows_to_errors(rows, texts):
    errors = []
    for row in rows:
        document = row["file"]
        for field, entry in row["fields"].items():
            if entry.get("correct"):
                continue
            errors.append({
                "document": document,
                "field": field,
                "expected": entry.get("expected"),
                "actual": _got(entry),
                "document_text": texts(document),
            })
    return errors


def load_arms():
    """Every measured arm that carries per-field detail, as (source, arm, errors).

    Four results files are skipped and named in the output: results_model_compatibility and
    results_retrieval store counts or rankings rather than values, and results_gate_verification
    measures the gate rather than the extraction.
    """
    texts = {}

    def sample_text(name):
        if name not in texts:
            texts[name] = _sample_text(name)
        return texts[name]

    arms = []

    for name, label in (("results_before.json", "repairs off"),
                        ("results_before_with_fallback.json", "repairs on")):
        data = _results(name)
        arms.append({
            "source": name,
            "arm": f"2026-08-26 baseline, {label}",
            "errors": _rows_to_errors(data["rows"], sample_text),
        })

    data = _results("results_schema_comparison.json")
    for variant in data["variants"]:
        arms.append({
            "source": "results_schema_comparison.json",
            "arm": variant["label"],
            "errors": _rows_to_errors(variant["rows"], sample_text),
        })

    data = _results("results_prompt_schema_2x2.json")
    for cell in data["cells"]:
        arms.append({
            "source": "results_prompt_schema_2x2.json",
            "arm": f"{cell['prompt']} + {cell['schema']}",
            "errors": _rows_to_errors(cell["rows"], sample_text),
        })

    documents = sentinel_comparison.load_documents()
    data = _results("results_sentinel_comparison.json")
    for variant in data["variants"]:
        errors = []
        for row in variant["rows"]:
            document = row["document"]
            text = documents[document]["text"]
            for field, entry in row.get("present", {}).items():
                if entry.get("correct"):
                    continue
                errors.append({"document": document, "field": field,
                               "expected": entry.get("expected"), "actual": _got(entry),
                               "document_text": text})
            # An absent field's only right answer is null. `admitted` is the sentinel script's
            # looser test: it accepts 'Not specified' as an honest refusal. That string still
            # reaches the database as a value, so it is counted here and classed `placeholder`.
            for field, entry in row.get("absent", {}).items():
                if entry.get("got") is None:
                    continue
                errors.append({"document": document, "field": field,
                               "expected": None, "actual": entry.get("got"),
                               "document_text": text})
        arms.append({
            "source": "results_sentinel_comparison.json",
            "arm": f"sentinel: {variant['variant']} schema",
            "errors": errors,
        })

    return arms


SKIPPED = {
    "results_model_compatibility.json": "counts only, no per-field values",
    "results_retrieval.json": "measures ranking, not extraction",
    "results_gate_verification.json": "measures the gate's verdicts, not extracted values",
}


# -- reporting ---------------------------------------------------------------

def classify_arms(arms):
    for arm in arms:
        counts = dict.fromkeys(CLASSES, 0)
        for error in arm["errors"]:
            error["class"], error["reason"] = classify(
                error["field"], error["expected"], error["actual"], error["document_text"])
            counts[error["class"]] += 1
        arm["counts"] = counts
        arm["total"] = len(arm["errors"])
    return arms


def _duplicate_arms(arms):
    """Arms whose error list is identical to an earlier one. Summing across them double-counts."""
    seen, duplicates = {}, []
    for arm in arms:
        key = json.dumps([[e["document"], e["field"], str(e["actual"])] for e in arm["errors"]],
                         sort_keys=True)
        if not arm["errors"]:
            continue
        if key in seen:
            duplicates.append((arm["arm"], seen[key]))
        else:
            seen[key] = arm["arm"]
    return duplicates


def print_report(arms, detail=False):
    width = max(len(a["arm"]) for a in arms)
    header = f"{'arm':<{width}} | " + " | ".join(f"{c[:9]:>9}" for c in CLASSES) + " | total"
    print("\nErrors by class, per measured arm")
    print(header)
    print("-" * len(header))
    for arm in arms:
        row = f"{arm['arm']:<{width}} | " + " | ".join(
            f"{arm['counts'][c] or '.':>9}" for c in CLASSES) + f" | {arm['total']:>5}"
        print(row)

    print("\nSkipped results files")
    for name, why in SKIPPED.items():
        print(f"  {name:<38} {why}")

    duplicates = _duplicate_arms(arms)
    if duplicates:
        print("\nIdentical arms. Summing across these double-counts the same errors:")
        for later, first in duplicates:
            print(f"  '{later}' == '{first}'")

    print("\nTotals across arms, which double-count the duplicates above and are"
          "\nprinted only to show which classes are populated at all:")
    totals = dict.fromkeys(CLASSES, 0)
    for arm in arms:
        for klass, count in arm["counts"].items():
            totals[klass] += count
    for klass in CLASSES:
        note = ""
        if totals[klass] == 0:
            note = "  <- no instance in any saved result"
        print(f"  {klass:<12} {totals[klass]:>3}{note}")

    if detail:
        print("\nEvery error, with the rule that fired")
        for arm in arms:
            if not arm["errors"]:
                continue
            print(f"\n  {arm['arm']}")
            for error in arm["errors"]:
                print(f"    {error['class']:<11} {error['document']:<34} "
                      f"{error['field']:<14} {error['reason']}")
    return totals


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--save", metavar="FILE", help="write the classification to evaluation/FILE")
    parser.add_argument("--detail", action="store_true", help="print every error and its reason")
    args = parser.parse_args()

    arms = classify_arms(load_arms())
    totals = print_report(arms, detail=args.detail)

    if args.save:
        payload = {
            "classes": list(CLASSES),
            "skipped_files": SKIPPED,
            "duplicate_arms": [{"arm": a, "same_as": b} for a, b in _duplicate_arms(arms)],
            "totals_across_arms_double_counted": totals,
            "arms": [{
                "source": arm["source"],
                "arm": arm["arm"],
                "counts": arm["counts"],
                "total": arm["total"],
                "errors": [{k: v for k, v in e.items() if k != "document_text"}
                           for e in arm["errors"]],
            } for arm in arms],
        }
        path = os.path.join(EVAL_DIR, args.save)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        print(f"\nSaved to {path}")


if __name__ == "__main__":
    main()
