"""Shows every term behind a stored validation score, and which rule decided the verdict.

    ./.venv/bin/python evaluation/score_breakdown.py [--db workflow_platform.db] [--id 1]

Written 2026-09-28, after the supervisor asked where the score comes from and why it matters.
The report could describe the formula, but a description is not evidence: this recomputes the
score from the archived PDF and the stored extraction, prints each of the seven terms against
its maximum, and then prints the four hard rules separately.

**The separation is the point.** The score and the verdict answer different questions, and the
gate deliberately does not derive one from the other:

    score      how complete the extraction is, and how much of it is corroborated by the
               document. A weighted sum of seven terms, out of 1.00.
    verdict    whether it is safe to act on without a person. Four conditions, all of which
               must hold. Three of them are not about the score at all.

Run against the current sample set this prints a pair that makes the distinction concrete:
INV-2026-004 scores 0.87 and is refused, INV-2026-001 scores 0.85 and passes. A reader who saw
only the numbers would rank them the other way round.

Reads only. It opens the database and the archived files and writes nothing.
"""
import argparse
import json
import os
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from pypdf import PdfReader  # noqa: E402

from main import ConfidenceValidator, ExtractedInvoice  # noqa: E402

# The weights, mirrored from ConfidenceValidator.explain so this script can show a term
# against its maximum. `test_score_breakdown_weights` asserts the mirror still matches.
COMPLETENESS_EACH = 0.06
COMPLETENESS_FIELDS = ("invoice_number", "vendor", "date", "currency", "total")
ITEMS = 0.10
INVOICE_NUMBER_IN_TEXT = 0.10
VENDOR_IN_TEXT = 0.08
VENDOR_NOT_A_LABEL = 0.07
AMOUNT = {"verified": 0.25, "present": 0.125, "absent": 0.0}
RECONCILIATION = {"exact": 0.10, "plausible": 0.10, "unknown": 0.05, "short": 0.0}

AMOUNT_MEANING = {
    "verified": "sits within two lines of a grand-total label",
    "present": "appears in the document but NOT near a total label",
    "absent": "does not appear in the document at all",
}
RECONCILIATION_MEANING = {
    "exact": "the rows sum to the stated total",
    "plausible": "the total exceeds the rows; tax or shipping would explain it",
    "unknown": "no rows were stored, so nothing can be checked against the total",
    "short": "the rows add up to MORE than the stated total",
}


def document_text(path: str) -> str:
    return "\n".join((page.extract_text() or "") for page in PdfReader(path).pages)


def terms(detail: dict) -> list:
    """The seven terms as (label, awarded, maximum, note)."""
    checks = detail["checks"]
    filled = [f for f in COMPLETENESS_FIELDS if checks[f"{f}_present"]]
    missing = [f for f in COMPLETENESS_FIELDS if not checks[f"{f}_present"]]
    return [
        (f"completeness, {len(filled)}/5 header fields filled",
         COMPLETENESS_EACH * len(filled), COMPLETENESS_EACH * 5,
         "all present" if not missing else "empty: " + ", ".join(missing)),
        ("line items were stored",
         ITEMS * checks["items_present"], ITEMS,
         "yes" if checks["items_present"] else "NO ROWS STORED"),
        ("invoice number appears in the text",
         INVOICE_NUMBER_IN_TEXT * checks["invoice_number_in_text"], INVOICE_NUMBER_IN_TEXT,
         "found" if checks["invoice_number_in_text"] else "not found"),
        ("vendor appears in the text",
         VENDOR_IN_TEXT * checks["vendor_in_text"], VENDOR_IN_TEXT,
         "found" if checks["vendor_in_text"] else "not found"),
        ("vendor is not a field label",
         VENDOR_NOT_A_LABEL * checks["vendor_is_not_a_label"], VENDOR_NOT_A_LABEL,
         "ok" if checks["vendor_is_not_a_label"] else "CAPTURED THE CAPTION"),
        (f"amount check: {detail['amount_state']}",
         AMOUNT[detail["amount_state"]], max(AMOUNT.values()),
         AMOUNT_MEANING[detail["amount_state"]]),
        (f"reconciliation: {detail['reconciliation']}",
         RECONCILIATION[detail["reconciliation"]], max(RECONCILIATION.values()),
         RECONCILIATION_MEANING[detail["reconciliation"]]),
    ]


def hard_rules(detail: dict, threshold: float) -> list:
    """The four conditions, as (holds, description). Three are not about the score."""
    checks = detail["checks"]
    return [
        (detail["score"] >= threshold,
         f"score >= {threshold:.2f}   (scored {detail['score']:.2f})"),
        (detail["amount_state"] == "verified",
         f"the amount was located beside a grand-total label   (got {detail['amount_state']})"),
        (detail["reconciliation"] != "short",
         f"the rows do not exceed the total   (got {detail['reconciliation']})"),
        (checks["vendor_is_not_a_label"],
         "the vendor is a name and not a caption"),
    ]


def report(row: sqlite3.Row, validator: ConfidenceValidator) -> dict:
    data = ExtractedInvoice(**json.loads(row["raw_json"]))
    detail = validator.explain(data, document_text(row["archive_path"]))

    print("=" * 78)
    print(f"{row['vendor_name']}   {row['invoice_number']}")
    print(f"stored {row['validation_score']:.2f} / {row['validation_status']}"
          f"      recomputed {detail['score']:.2f} / {detail['status']}")
    if abs(row["validation_score"] - detail["score"]) > 0.005:
        print("  ** stored and recomputed disagree: the document or the weights changed **")
    print("-" * 78)
    for label, got, maximum, note in terms(detail):
        marker = "  " if abs(got - maximum) < 1e-9 else "<-"
        print(f"  {label:<40s} {got:5.2f} /{maximum:5.2f} {marker} {note}")
    print(f"  {'TOTAL':<40s} {detail['score']:5.2f} / 1.00")
    print("-" * 78)
    print("  the verdict is decided by four conditions, not by the total above:")
    for holds, description in hard_rules(detail, validator.threshold):
        print(f"     {'PASS' if holds else 'FAIL'}  {description}")
    print(f"  => {detail['status']}")
    print(f"  {detail['reason']}")
    return detail


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=os.path.join(REPO_ROOT, "workflow_platform.db"))
    parser.add_argument("--id", type=int, help="one invoice_id; default is all of them")
    args = parser.parse_args(argv)

    if not os.path.exists(args.db):
        print(f"[!] No database at {args.db}. Run main.py first.")
        return 1

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM invoices WHERE raw_json IS NOT NULL AND archive_path IS NOT NULL"
    params = ()
    if args.id:
        sql += " AND invoice_id = ?"
        params = (args.id,)
    sql += " ORDER BY validation_score DESC, invoice_id"
    rows = [r for r in conn.execute(sql, params)]
    conn.close()

    if not rows:
        print("[!] Nothing to report on.")
        return 1

    missing = [r["invoice_number"] for r in rows if not os.path.exists(r["archive_path"])]
    if missing:
        print(f"[!] Archived file missing for: {', '.join(missing)}. Skipping those.")
        rows = [r for r in rows if os.path.exists(r["archive_path"])]

    validator = ConfidenceValidator()
    details = [report(r, validator) for r in rows]

    print("=" * 78)
    passed = sum(d["status"] == "Validated" for d in details)
    print(f"{passed} of {len(details)} passed the gate. "
          f"Threshold {validator.threshold:.2f}, and three of the four conditions ignore it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
