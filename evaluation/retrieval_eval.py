"""Measures the retrieval strategies against each other. Item 14, and Neo's own named gap.

retrieval.py ships two strategies and reports neither against the other. Building two
approaches and measuring none is the thing this project would criticise in someone else's
work, so this script exists before a third strategy is added rather than after.

Relevance is judged mechanically, never by hand. Two query families, each with a rule that
can be recomputed from the corpus by anyone:

    vendor   "what has this supplier sent us before"
             relevant = every email from that sender, excluding the one being processed
    thread   "what happened with invoice INV-2026-005"
             relevant = every email whose subject or body names that invoice number

Three strategies. `sender` and `keyword` are the shipped ones, imported from retrieval.py.
`hybrid` is implemented HERE and not in retrieval.py, because measuring it is the point and
shipping it is a separate decision. It filters by sender, then ranks what survives by the
keyword score, which is the obvious combination and costs about ten lines.

Reads only. Touches no shipped file.

Usage:
    python evaluation/retrieval_eval.py
    python evaluation/retrieval_eval.py --save results_retrieval.json
"""

import argparse
import json
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import retrieval  # noqa: E402
from storage import connect, DEFAULT_DB_PATH  # noqa: E402

K = 5
INV = re.compile(r"INV-\d{4}-\d{3}")


def load_rows():
    with connect(DEFAULT_DB_PATH) as conn:
        return [dict(r) for r in conn.execute(
            """SELECT email_id, message_id, sender, subject, received_at, body_text
               FROM email_messages ORDER BY received_at DESC""")]


def hybrid(rows, query, sender, limit):
    """Filter by sender, then rank the survivors by keyword score.

    Deliberately not in retrieval.py. Whether a third strategy ships is a decision for the
    group; whether it is better than the two we have is a measurement, and this is it.
    """
    same = [r for r in rows if r["sender"].lower() == sender.lower()]
    # by_keyword computes IDF over the rows it is given. Scoring within the vendor's own
    # mail is the intended behaviour here: a term common across ALL vendors but rare for
    # this one should still separate this vendor's messages.
    ranked = retrieval.by_keyword(same, query, limit=len(same))
    seen = {r["email_id"] for r in ranked}
    # Anything the keyword pass scored zero on still belongs to the vendor, so it is kept
    # behind the ranked hits rather than dropped. Recall must not fall below `sender`.
    tail = [dict(r, score=0.0, why="same sender, no term matched") for r in same
            if r["email_id"] not in seen]
    return (ranked + tail)[:limit]


def metrics(hits, relevant):
    ids = [h["email_id"] for h in hits]
    got = [i for i in ids if i in relevant]
    p = len(got) / len(ids) if ids else 0.0
    r = len(got) / len(relevant) if relevant else 0.0
    rr = 0.0
    for rank, i in enumerate(ids, start=1):
        if i in relevant:
            rr = 1.0 / rank
            break
    return p, r, rr


def build_queries(rows):
    """Derives the query set from the corpus rather than hardcoding it."""
    senders = sorted({r["sender"] for r in rows})
    queries = []

    for s in senders:
        mine = [r for r in rows if r["sender"] == s]
        newest = max(mine, key=lambda r: r["received_at"])
        # What the pipeline knows when an invoice arrives is the supplier name, so that is
        # the query. Using the triggering email's whole subject would hand the keyword
        # strategy that email's own unique tokens and measure nothing.
        m = re.search(r"from (.+)$", newest["subject"])
        vendor_words = m.group(1) if m else newest["subject"]
        queries.append({
            "family": "vendor",
            "label": f"prior mail from {s.split('@')[1]}",
            "sender": s,
            "text": vendor_words,
            "relevant": {r["email_id"] for r in mine if r["email_id"] != newest["email_id"]},
        })

        # The prior thread is the invoice number this vendor mentions MOST, not the first
        # one alphabetically. Sorting picked INV-2026-001, the invoice being delivered right
        # now, which only its own email references; the query then had a single relevant
        # document and could not separate the strategies at all.
        from collections import Counter
        nums = Counter(n for r in mine
                       for n in set(INV.findall(f"{r['subject']} {r['body_text'] or ''}")))
        if not nums:
            continue
        target = nums.most_common(1)[0][0]
        rel = {r["email_id"] for r in mine
               if target in f"{r['subject']} {r['body_text'] or ''}"}
        queries.append({
            "family": "thread",
            "label": f"thread {target}",
            "sender": s,
            "text": target,
            "relevant": rel,
        })
    return queries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", metavar="FILE")
    args = ap.parse_args()

    rows = load_rows()
    queries = build_queries(rows)
    strategies = ("sender", "keyword", "hybrid")

    print("=" * 84)
    print(f"RETRIEVAL STRATEGY COMPARISON   corpus = {len(rows)} emails, k = {K}")
    print("=" * 84)
    print("Relevance is computed from the corpus, not judged by hand. See the module docstring.")
    print()

    agg = {s: {"vendor": [], "thread": []} for s in strategies}
    detail = []

    for q in queries:
        print(f"[{q['family']:<6}] {q['label']:<42} relevant: {len(q['relevant'])}")
        row = {"query": q["label"], "family": q["family"],
               "n_relevant": len(q["relevant"]), "strategies": {}}
        for s in strategies:
            if s == "sender":
                hits = retrieval.by_sender(rows, q["sender"], K)
            elif s == "keyword":
                hits = retrieval.by_keyword(rows, q["text"], K)
            else:
                hits = hybrid(rows, q["text"], q["sender"], K)
            p, r, rr = metrics(hits, q["relevant"])
            agg[s][q["family"]].append((p, r, rr))
            print(f"    {s:<8} P@{K}={p:.2f}  R@{K}={r:.2f}  MRR={rr:.2f}   "
                  f"returned {len(hits)}")
            row["strategies"][s] = {"precision": round(p, 3), "recall": round(r, 3),
                                    "mrr": round(rr, 3), "returned": len(hits)}
        detail.append(row)
        print()

    print("=" * 84)
    print("MEANS BY QUERY FAMILY")
    print("=" * 84)
    print(f"{'strategy':<10} {'family':<8} {'P@5':<8} {'R@5':<8} {'MRR':<8}")
    print("-" * 84)
    summary = {}
    for s in strategies:
        summary[s] = {}
        for fam in ("vendor", "thread"):
            vals = agg[s][fam]
            if not vals:
                continue
            p = sum(v[0] for v in vals) / len(vals)
            r = sum(v[1] for v in vals) / len(vals)
            m = sum(v[2] for v in vals) / len(vals)
            summary[s][fam] = {"precision": round(p, 3), "recall": round(r, 3),
                               "mrr": round(m, 3), "n_queries": len(vals)}
            print(f"{s:<10} {fam:<8} {p:<8.2f} {r:<8.2f} {m:<8.2f}")
    print()

    if args.save:
        out = os.path.join(REPO_ROOT, "evaluation", args.save)
        json.dump({"corpus_size": len(rows), "k": K,
                   "queries": detail, "summary": summary}, open(out, "w"), indent=2)
        print(f"saved {out}")


if __name__ == "__main__":
    main()
