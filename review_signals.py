"""What to tell an approver about a document, in words rather than a score.

Separate from app.py because it is the only piece of new logic on the approval screen and the
only piece worth testing. app.py itself is layout, and layout is verified by looking at it.

The screen used to show `validation_score` as a bare number. A person reading 0.85 cannot act on
it: the number does not say what the gate was unhappy about, and the weights behind it changed
twice during the project, so it is not even comparable across dates. A person reading "no line
items to check the total against" knows to open the document.

See fe-screen-spec.md section 4.
"""

from typing import Any, Mapping, Optional

# Ordered. The first condition that matches is the one shown, so the most serious concern wins
# and a row never carries two warnings competing for the same glance.
#
# `short` leads because it is the only genuine anomaly in the list: tax and shipping can push a
# total above the sum of its line items, and nothing legitimate pushes it below.
SIGNALS = (
    ("reconciliation", "short",
     "Total is less than the line items add up to"),
    ("validation_status", "NeedsReview",
     "Did not pass the validation gate"),
    ("reconciliation", "unknown",
     "No line items to check the total against"),
    ("total_source", "fallback",
     "Total was recovered by our code, not read from the document"),
    ("vendor_source", "fallback",
     "Vendor name was inferred, not read from the document"),
    ("reconciliation", "plausible",
     "Total exceeds the line items; tax or shipping would explain it"),
)


# The second sentence, keyed by the first. A parallel mapping rather than a fourth element in
# SIGNALS, because three modules unpack those tuples as triples and a fourth would break all of
# them for a string none of them use. Two structures that must agree can drift, so
# tests/test_review_signals.py asserts every message has exactly one explanation and that none
# is orphaned.
#
# Each one says what the gate saw, not what the score was. "Validation score 0.25" tells an
# approver nothing they can act on; "the model read a total but no rows" tells them what to look
# for when they open the document.
DETAIL = {
    "Total is less than the line items add up to":
        "Tax and shipping push a total above its parts. Nothing legitimate pushes it below.",
    "Did not pass the validation gate":
        "At least one check failed. The document is here because a person has to decide anyway.",
    "No line items to check the total against":
        "The model read a total but no rows, so nothing adds up to it. Every other check passed.",
    "Total was recovered by our code, not read from the document":
        "A fallback found the amount after the model returned none. Worth confirming against the "
        "document.",
    "Vendor name was inferred, not read from the document":
        "The name was taken from the file or the email rather than the page itself.",
    "Total exceeds the line items; tax or shipping would explain it":
        "Common and usually fine. It is flagged because nothing here checks which of the two it "
        "is.",
}


def risk_detail(row: Mapping[str, Any]) -> Optional[str]:
    """The sentence that explains the signal, or None when there is no signal."""
    message = risk_signal(row)
    return DETAIL.get(message) if message else None


def risk_signal(row: Mapping[str, Any]) -> Optional[str]:
    """The one sentence to show for a document, or None when there is nothing to say.

    Reads the invoice's **current** columns every time it is called.

    It deliberately does not read `tasks.reason`, even though that column holds a sentence
    written for exactly this purpose. Measured on 2026-09-19: invoice 1's open Review task was
    created on 2026-08-28 and still said "No total amount was extracted", while the invoice had
    been reprocessed on 2026-09-19 and carried a total of 1,500.00. The unique index
    `ux_tasks_one_open` stops a second open task of the same type being created, so a re-run
    never refreshes the reason. Putting that column on screen would have shown an approver a
    sentence that was three weeks stale and false.
    """
    for column, value, message in SIGNALS:
        if row.get(column) == value:
            return message
    return None


def has_signal(row: Mapping[str, Any]) -> bool:
    return risk_signal(row) is not None
