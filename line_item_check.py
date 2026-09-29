"""Count the priced rows a document shows, independently of what the model stored.

This exists for one sentence on the review dialog. Invoice 1's extracted text lists three
priced rows that sum to exactly the total the model reported, and the model stored none of
them. `fe-screen-spec.md` §5 calls that contradiction the most useful thing on the screen, and
until now it was only available: the two facts sat side by side and nothing connected them.

It is a **check on the document**, not a second extractor. It never writes to the database and
its result never reaches the validation score. If it disagrees with the stored line items, the
disagreement is the output.

Why four lines rather than a regex over one. `pypdf` returns each table cell on its own line,
so a row arrives as description, quantity, unit price, line total. Matching currency amounts
alone finds seven in invoice 1 and there are three rows.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

AMOUNT = re.compile(r"^([A-Z]{3})\s*([\d,]+\.\d{2})$")


@dataclass(frozen=True)
class DocumentRow:
    description: str
    quantity: int
    unit_price: str
    line_total: str

    @property
    def total_value(self) -> float:
        return float(AMOUNT.match(self.line_total).group(2).replace(",", ""))


def rows_in(text: str) -> list[DocumentRow]:
    """Every four-line group that reads as a priced table row."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    found: list[DocumentRow] = []
    for i in range(max(0, len(lines) - 3)):
        description, quantity, unit, total = lines[i:i + 4]
        if (quantity.isdigit()
                and AMOUNT.match(unit) and AMOUNT.match(total)
                and not AMOUNT.match(description) and not description.isdigit()):
            found.append(DocumentRow(description, int(quantity), unit, total))
    return found


def contradiction(text: str, stored_count: int, stored_total: float | None) -> dict | None:
    """What to say when the document shows rows the model did not store.

    Returns None when there is nothing to say, which is the common case and the one worth
    keeping quiet: a document whose rows were read correctly should produce no warning at all.
    """
    rows = rows_in(text)
    if not rows or stored_count >= len(rows):
        return None
    total = sum(row.total_value for row in rows)
    return {
        "rows": rows,
        "count": len(rows),
        "sum": total,
        # The strongest version of the sentence is available only when the arithmetic lands.
        # When it does, the model's own total corroborates the rows it failed to store, and
        # there is no reading of that except a miss.
        "matches_stored_total": (stored_total is not None
                                 and abs(total - stored_total) < 0.005),
    }
