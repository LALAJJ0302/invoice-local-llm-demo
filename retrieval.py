"""Historical email retrieval: finds prior correspondence relevant to a document.

Item 4 of the 2026-09-08 assignment. Given an invoice being processed, this returns the
earlier emails from the same vendor that a person would want to have read first: the
query about a line item, the credit note that followed it, the last invoice.

**Two strategies, deliberately, because which one is better is a measurement nobody in
this project has taken.** The RAG literature assumes embeddings. At n=18 emails that
assumption is worth testing rather than inheriting:

    sender      exact match on the sender address, newest first. Cheap and precise.
    keyword     TF-IDF-style scoring over subject and body, no model, no index.

A third strategy using embeddings is deliberately NOT implemented yet. Adding it before
measuring these two would mean never learning whether it was needed, and "we used a vector
database" is not a finding. See evaluation/retrieval_eval.py.

Nothing here calls a language model. Retrieval selects context; the model consumes it.

Usage:
    python retrieval.py --vendor "Apex Cloud Solutions Pty Ltd"
    python retrieval.py --sender billing@apexcloud.io --strategy keyword
    python retrieval.py --query "storage line item query" --strategy keyword
"""

import argparse
import math
import os
import re
import sys
from collections import Counter
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from storage import connect, DEFAULT_DB_PATH  # noqa: E402

# Words that appear in nearly every invoice email and so separate nothing. Kept short and
# domain-specific rather than importing a general stop-word list: "invoice" is a stop word
# in THIS corpus and a strong signal in most others.
NOISE = frozenset("""
a an the and or of to for from in on at is are was were be been this that it its
please find attached regards thanks thank you hello hi dear team kind best
invoice tax receipt payment terms net days amount total
""".split())

TOKEN = re.compile(r"[a-z0-9][a-z0-9\-]{1,}")


def tokenise(text: Optional[str]) -> List[str]:
    return [t for t in TOKEN.findall((text or "").lower()) if t not in NOISE]


def _rows(db_path: str) -> List[Dict[str, Any]]:
    with connect(db_path) as conn:
        return [dict(r) for r in conn.execute(
            """SELECT email_id, message_id, sender, subject, received_at,
                      attachment_count, body_text, body_source
               FROM email_messages ORDER BY received_at DESC""")]


def by_sender(rows, sender: str, limit: int) -> List[Dict[str, Any]]:
    """Everything this address sent, newest first.

    The obvious baseline, and the one to beat. If a smarter strategy cannot beat 'show me
    what this vendor sent before', the smarter strategy is not earning its complexity.
    """
    hits = [r for r in rows if r["sender"].lower() == sender.lower()]
    for r in hits:
        r["score"] = 1.0
        r["why"] = "same sender"
    return hits[:limit]


def by_keyword(rows, query: str, limit: int) -> List[Dict[str, Any]]:
    """Scores subject and body against the query using inverse document frequency.

    A term appearing in every email carries no information, so it is weighted down. This
    is TF-IDF without a library: at 18 documents, importing one would add a dependency and
    hide the arithmetic that the report has to explain anyway.
    """
    terms = tokenise(query)
    if not terms:
        return []

    docs = [tokenise(f"{r['subject']} {r['body_text']}") for r in rows]
    total = len(docs) or 1
    seen = Counter()
    for doc in docs:
        seen.update(set(doc))

    scored = []
    for row, doc in zip(rows, docs):
        if not doc:
            continue
        counts = Counter(doc)
        score, matched = 0.0, []
        for term in terms:
            if counts[term] == 0:
                continue
            # +1 so a term present in every document scores above zero, not exactly zero.
            idf = math.log(total / (1 + seen[term])) + 1.0
            score += (counts[term] / len(doc)) * idf
            matched.append(term)
        if score > 0:
            row = dict(row)
            row["score"] = round(score, 4)
            row["why"] = "matched: " + ", ".join(sorted(set(matched)))
            scored.append(row)

    scored.sort(key=lambda r: (-r["score"], r["received_at"]))
    return scored[:limit]


def retrieve(query: str = "", sender: Optional[str] = None, strategy: str = "sender",
             limit: int = 5, db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    """The interface JJ's module calls. Returns rows newest-first or best-first."""
    rows = _rows(db_path)
    if strategy == "sender":
        if not sender:
            raise ValueError("strategy 'sender' needs a sender address")
        return by_sender(rows, sender, limit)
    if strategy == "keyword":
        return by_keyword(rows, query or "", limit)
    raise ValueError(f"unknown strategy {strategy!r}, expected 'sender' or 'keyword'")


def as_context(hits: List[Dict[str, Any]], max_chars: int = 2000) -> str:
    """Flattens hits into a block for a prompt, newest first, truncated on a budget.

    Every line carries its provenance. A block of text pasted into a prompt with no note
    that it was generated is exactly how a measurement over mock data gets reported as a
    measurement over real correspondence.
    """
    parts, used = [], 0
    for hit in hits:
        block = (f"[{hit['received_at']}] from {hit['sender']} "
                 f"(source: {hit.get('body_source') or 'unknown'})\n"
                 f"Subject: {hit['subject']}\n{(hit['body_text'] or '').strip()}\n")
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return "\n---\n".join(parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sender")
    parser.add_argument("--vendor", help="Convenience: matches a sender containing this text")
    parser.add_argument("--query", default="")
    parser.add_argument("--strategy", default="sender", choices=["sender", "keyword"])
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    parser.add_argument("--context", action="store_true", help="Print the prompt block")
    args = parser.parse_args()

    sender = args.sender
    if args.vendor and not sender:
        rows = _rows(args.db)
        key = args.vendor.split()[0].lower()
        matches = {r["sender"] for r in rows if key in r["sender"].lower()}
        if not matches:
            print(f"No sender matches {args.vendor!r}.")
            return 1
        sender = sorted(matches)[0]
        print(f"[*] {args.vendor!r} -> {sender}")

    hits = retrieve(query=args.query, sender=sender, strategy=args.strategy,
                    limit=args.limit, db_path=args.db)

    if not hits:
        print("No prior correspondence found.")
        return 0

    print(f"\n=== {len(hits)} emails, strategy '{args.strategy}' ===\n")
    for hit in hits:
        print(f"  {hit['received_at']}  score {hit['score']:<8} {hit['subject'][:52]}")
        print(f"      {hit['why']}")

    if args.context:
        print("\n=== as prompt context ===\n")
        print(as_context(hits))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
