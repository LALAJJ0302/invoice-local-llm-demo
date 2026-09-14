"""Measures the date, currency and subtotal checks proposed on 2026-09-08, without shipping them.

Reads only. Imports main.ConfidenceValidator to report what the gate does today, and
implements the proposed replacements locally so main.py is untouched until the numbers
below are agreed. See the 2026-09-08 amendment in validation-gate-spec.md.

The design point is that accepting the truth is only half a check. A check that says
'verified' for the correct date and also says 'verified' for an invented one has measured
nothing. Every check here is therefore run twice: once on the transcribed ground truth,
which it must accept, and once on a deliberately invented value, which it must reject.

Usage:
    python evaluation/gate_verification_preview.py
    python evaluation/gate_verification_preview.py --save results_gate_verification.json
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

from main import ConfidenceValidator  # noqa: E402

LABEL_WINDOW = ConfidenceValidator.LABEL_WINDOW

# -- proposed label matching ------------------------------------------------
# A label matches on word boundaries, not as a free substring. The current gate uses
# `label in line.lower()`, and 'total' is a substring of 'subtotal', so every Subtotal
# line is currently counted as a grand-total label.
#
# The boundary rule alone is not enough: in 'Sub Total' the word 'total' stands alone and
# matches legitimately. Anything naming a subtotal is therefore excluded outright first,
# whatever else the line contains.

SUBTOTAL = re.compile(r"sub[\s\-]*total")

TOTAL_LABELS = ("grand total", "total due", "amount due", "balance due", "invoice total", "total")
# Deliberately excludes anything meaning a future payment: a due date is a different value
# from an issue date and a loose match would accept either.
DATE_LABELS = ("date of issue", "invoice date", "issue date", "date")
DATE_EXCLUDE = re.compile(r"(due|payment|delivery|ship\w*|received)\s+date|date\s+due")
CURRENCY_LABELS = ("currency", "currency code")


def _matches(line, labels, exclude=None):
    low = line.lower()
    if exclude is not None and exclude.search(low):
        return False
    return any(re.search(r"(?<![\w-])" + re.escape(lab) + r"\b", low) for lab in labels)


def _label_lines(lines, labels, exclude=None):
    return [i for i, line in enumerate(lines) if _matches(line, labels, exclude)]


def _near(i, label_lines):
    return any(abs(i - j) <= LABEL_WINDOW for j in label_lines)


def _three_way(lines, label_lines, on_line):
    """The shape verify_amount already uses: verified / present / absent."""
    found = False
    for i, line in enumerate(lines):
        if not on_line(line):
            continue
        found = True
        if _near(i, label_lines):
            return "verified"
    return "present" if found else "absent"


# -- the proposed checks ----------------------------------------------------
def verify_total_proposed(amount, raw_text):
    """verify_amount with the subtotal hole closed. Everything else is unchanged."""
    v = ConfidenceValidator()
    target = None
    try:
        import storage
        target = storage.to_cents(amount)
    except Exception:
        pass
    if not target:
        return "absent"
    lines = raw_text.splitlines()
    labels = [i for i, l in enumerate(lines)
              if not SUBTOTAL.search(l.lower()) and _matches(l, TOTAL_LABELS)]

    # Excluding subtotal lines from the LABEL list is not sufficient, and measuring said so
    # before this shipped. On a Subtotal / GST / Grand Total block the subtotal amount sits
    # two lines above the grand-total label, which is inside LABEL_WINDOW, so it was verified
    # by a label belonging to a different figure. The line the amount was FOUND on must be
    # rejected too.
    found = False
    for i, line in enumerate(lines):
        if target not in v._cents_on_line(line):
            continue
        if SUBTOTAL.search(line.lower()):
            continue                      # this amount is a subtotal, whatever sits near it
        found = True
        if _near(i, labels):
            return "verified"
    return "present" if found else "absent"


def _digit_groups(value):
    return set(re.findall(r"\d+", str(value or "")))


def verify_date_proposed(date_value, raw_text):
    """A date is verified when its digits sit within LABEL_WINDOW lines of a date label.

    Compares digit groups as a set rather than the formatted string, so 2026-08-10 and
    10/08/2026 both match the same line. That tolerance is the check's main weakness and
    is recorded rather than hidden: a set comparison cannot tell 08-10 from 10-08.
    """
    want = _digit_groups(date_value)
    if not want:
        return "absent"
    lines = raw_text.splitlines()
    labels = _label_lines(lines, DATE_LABELS, DATE_EXCLUDE)
    return _three_way(lines, labels, lambda l: want <= _digit_groups(l))


def verify_currency_proposed(currency, raw_text):
    """A currency code is three letters and would match almost anything as a bare substring.

    Label proximity is the only form of this check worth having, for the same reason the
    invoice-number substring check is the weakest one the gate currently runs.
    """
    code = str(currency or "").strip()
    if not code or code.lower() in {"unknown", "none"}:
        return "absent"
    lines = raw_text.splitlines()
    labels = _label_lines(lines, CURRENCY_LABELS)
    pat = re.compile(r"\b" + re.escape(code.lower()) + r"\b")
    return _three_way(lines, labels, lambda l: bool(pat.search(l.lower())))


# -- what the gate does today, for comparison -------------------------------
def current_date_check(date_value, raw_text):
    """The gate has no date check. It asks whether the field is non-empty, and nothing else."""
    return "non-empty" if (date_value and str(date_value) != "None") else "empty"


def current_currency_check(currency, raw_text):
    return "non-empty" if (currency and currency != "Unknown") else "empty"


INVENTED = {
    "date": "2026-01-01",          # plausible, and in none of the samples
    "currency": "EUR",             # a real code, wrong for every sample
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", metavar="FILE")
    args = ap.parse_args()

    truth = json.load(open(os.path.join(EVAL_DIR, "ground_truth.json")))["samples"]
    results = {"samples": {}, "subtotal_regression": {}}

    print("=" * 78)
    print("PROPOSED GATE CHECKS, measured against the transcribed ground truth")
    print("=" * 78)
    print("Each check runs twice. It must ACCEPT the true value and REJECT an invented one.")
    print(f"Invented values used: date={INVENTED['date']!r}  currency={INVENTED['currency']!r}")
    print()

    hdr = f"{'sample':<34} {'field':<9} {'today':<10} {'true':<9} {'invented':<9} {'verdict'}"
    print(hdr)
    print("-" * 78)

    tally = {"date": [0, 0], "currency": [0, 0]}
    for name in sorted(truth):
        path = os.path.join(SAMPLES_DIR, name)
        if not os.path.exists(path):
            continue
        raw = "".join(p.extract_text() for p in PdfReader(path).pages)
        g = truth[name]
        row = {}

        for field, proposed, current in (
            ("date", verify_date_proposed, current_date_check),
            ("currency", verify_currency_proposed, current_currency_check),
        ):
            on_true = proposed(g[field], raw)
            on_fake = proposed(INVENTED[field], raw)
            today = current(g[field], raw)
            ok_accept = on_true == "verified"
            ok_reject = on_fake == "absent"
            if ok_accept:
                tally[field][0] += 1
            if ok_reject:
                tally[field][1] += 1
            verdict = "pass" if (ok_accept and ok_reject) else "FAIL"
            print(f"{name[:33]:<34} {field:<9} {today:<10} {on_true:<9} {on_fake:<9} {verdict}")
            row[field] = {"today": today, "on_true": on_true, "on_invented": on_fake}

        results["samples"][name] = row

    n = len(results["samples"])
    print()
    print(f"accepts the true value:   date {tally['date'][0]}/{n}   currency {tally['currency'][0]}/{n}")
    print(f"rejects the invented one: date {tally['date'][1]}/{n}   currency {tally['currency'][1]}/{n}")
    print()
    print("Today's column is the whole finding: the gate reports 'non-empty' for the true value")
    print("and would report 'non-empty' for the invented one too. It cannot tell them apart.")

    # -- the subtotal regression ------------------------------------------
    print()
    print("=" * 78)
    print("SUBTOTAL REGRESSION")
    print("=" * 78)
    doc = ("Acme Supplies Pty Ltd\nInvoice INV-2026-009\n\n"
           "Widget A    10 x 100.00     1000.00\n"
           "Widget B     5 x 100.00      500.00\n\n"
           "Subtotal:                   1500.00\n"
           "GST 10%:                     150.00\n"
           "Grand Total:                1650.00\n")
    print("Document: Subtotal 1500.00 / GST 150.00 / Grand Total 1650.00")
    print("The payable amount is 1650.00. Accepting 1500.00 underpays by the tax.")
    print()
    v = ConfidenceValidator()
    print(f"{'amount':<12} {'today':<12} {'proposed':<12} {'verdict'}")
    print("-" * 78)
    for amt, should in ((1650.00, "verified"), (1500.00, "absent")):
        today = v.verify_amount(amt, doc)
        prop = verify_total_proposed(amt, doc)
        verdict = "pass" if prop == should else "FAIL"
        print(f"{amt:<12.2f} {today:<12} {prop:<12} {verdict}   (want {should})")
        results["subtotal_regression"][f"{amt:.2f}"] = {
            "today": today, "proposed": prop, "want": should}

    # -- the real samples must not regress --------------------------------
    print()
    print("=" * 78)
    print("TOTAL CHECK ON THE REAL SAMPLES: the fix must not break what already works")
    print("=" * 78)
    print(f"{'sample':<34} {'true total':<12} {'today':<12} {'proposed':<12} {'verdict'}")
    print("-" * 78)
    regressions = 0
    for name in sorted(truth):
        path = os.path.join(SAMPLES_DIR, name)
        if not os.path.exists(path):
            continue
        raw = "".join(p_.extract_text() for p_ in PdfReader(path).pages)
        amt = truth[name]["total_amount"]
        today = v.verify_amount(amt, raw)
        prop = verify_total_proposed(amt, raw)
        ok = not (today == "verified" and prop != "verified")
        if not ok:
            regressions += 1
        print(f"{name[:33]:<34} {amt:<12.2f} {today:<12} {prop:<12} "
              f"{'pass' if ok else 'REGRESSION'}")
        results["samples"][name]["total"] = {"today": today, "proposed": prop}
    print()
    print(f"regressions: {regressions}")

    print()
    print("The gate does not change until these numbers are agreed. See validation-gate-spec.md,")
    print("amendment 2026-09-08, which deliberately leaves the weights and the hard rules open")
    print("until this script has run.")

    if args.save:
        out = os.path.join(EVAL_DIR, args.save)
        json.dump(results, open(out, "w"), indent=2)
        print(f"\nsaved {out}")


if __name__ == "__main__":
    main()
