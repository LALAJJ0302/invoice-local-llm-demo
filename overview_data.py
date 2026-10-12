"""What the Overview page says about the invoices, worked out apart from how it is drawn.

Separate from app.py for the reason review_signals.py is: this is the logic on the page that is
worth testing, and app.py is layout, which is verified by looking at it.

Everything here takes a pandas frame or a row and returns plain values. Nothing reads the
database. The frames are the ones app.py already loads.

See fe-overview-v2-spec.md.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional

import pandas as pd

from review_signals import SIGNALS

# A pending document older than this is listed as needing attention. One constant, because the
# right number is a judgement the group may change (fe-overview-v2-spec.md, decision 2).
WAITING_TOO_LONG_DAYS = 2

# Signals that are information rather than a concern. "Tax or shipping would explain it" is
# printed on every document whose total exceeds its line items, and most of them are fine.
# Listing every one of them as needing attention would bury the ones that are not.
_NOT_A_CONCERN = {"Total exceeds the line items; tax or shipping would explain it"}

_SIGNAL_MESSAGES = [message for _column, _value, message in SIGNALS]


def totals_by_currency(frame: pd.DataFrame) -> List[tuple]:
    """(currency, amount) pairs, largest first. Never adds two currencies together.

    A document with no currency or no amount is left out: a sum over it would be a number
    nobody can label. Both are possible, since extraction fields are optional.
    """
    if frame.empty:
        return []
    usable = frame.dropna(subset=["currency", "total_amount"])
    sums = usable.groupby("currency")["total_amount"].sum().sort_values(ascending=False)
    return [(str(currency), float(amount)) for currency, amount in sums.items()]


def attention_reasons(row: Mapping[str, Any], days_waiting: Optional[int]) -> List[str]:
    """Why this document needs a person, most serious first. Empty when nothing does.

    Four kinds, in this order: it failed, the gate raised a concern, nothing links it to an
    email, it has waited too long. "Possible duplicate" is not here on purpose: the pipeline
    upserts on a content hash, so a duplicate never becomes a second row to look at.
    """
    reasons: List[str] = []
    if row.get("validation_status") == "Failed":
        reasons.append("Processing failed")
    for column, value, message in SIGNALS:
        if message in _NOT_A_CONCERN:
            continue
        if row.get(column) == value:
            reasons.append(message)
            break  # risk_signal() shows one message too: the most serious wins
    email_id = row.get("email_id")
    if email_id is None or pd.isna(email_id):
        reasons.append("No matching email")
    if days_waiting is not None and days_waiting >= WAITING_TOO_LONG_DAYS:
        reasons.append(f"Waiting {days_waiting} days")
    return reasons


def attention_rows(pending: pd.DataFrame,
                   days_waiting: Callable[[Any], Optional[int]]) -> List[Dict[str, Any]]:
    """Pending documents that have at least one reason, longest-waiting first.

    Returns dicts rather than frames so the page cannot accidentally render a row it has not
    explained: every entry carries its row and its non-empty reasons.
    """
    found = []
    for _, row in pending.iterrows():
        days = days_waiting(row)
        reasons = attention_reasons(row, days)
        if reasons:
            found.append({"row": row, "reasons": reasons, "days": days})
    found.sort(key=lambda item: (-(item["days"] or 0), -float(item["row"].get("total_amount") or 0)))
    return found


def jira_state_by_invoice(outbox: pd.DataFrame) -> Dict[int, str]:
    """invoice_id to "Sent" or "Queued", for invoices with a live Jira outbox row.

    Sent wins over Queued. A withdrawn row (the document was reopened) is not live. Invoices with
    no Jira row are absent.
    """
    states: Dict[int, str] = {}
    if outbox.empty:
        return states
    jira = outbox[outbox["channel"] == "Jira"]
    for _, row in jira.iterrows():
        if row.get("withdrawn"):
            continue
        invoice_id = int(row["invoice_id"])
        if row["state"] == "Sent":
            states[invoice_id] = "Sent"
        elif row["state"] in ("Pending", "Failed"):
            states.setdefault(invoice_id, "Queued")
    return states


def workflow_status(row: Mapping[str, Any], jira_state: Optional[str]) -> str:
    """Where the document is in the workflow. Only states the database can support.

    There is no "Completed": nothing records that a payment happened. Validation status is a
    different question and is shown beside this, never folded into it.
    """
    approval = row.get("approval_status")
    if approval == "Rejected":
        return "Rejected"
    if approval != "Approved":
        return "Awaiting approval"
    if jira_state == "Sent":
        return "Sent to Jira"
    if jira_state == "Queued":
        return "Queued for Jira"
    reviewed = row.get("reviewed_at")
    by_system = reviewed is None or pd.isna(reviewed)
    return "Approved by the system" if by_system else "Approved by a person"
