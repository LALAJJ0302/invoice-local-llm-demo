"""Tests for historical email retrieval.

The point of retrieval is choosing WHICH prior email to show, so these tests check
ordering and relevance, not that a query returns something. A retriever that returns
every email in date order passes 'it returned results' and is useless.
"""

import os
import sqlite3
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import retrieval  # noqa: E402
from storage import StorageManager  # noqa: E402


@pytest.fixture
def mailbox(tmp_path):
    """Three vendors, so a strategy that ignores the vendor is visible as a failure."""
    store = StorageManager(str(tmp_path / "t.db"))
    rows = [
        ("<a1@apex>", "billing@apex.io", "Invoice INV-100 for July",
         "2026-06-01 09:00:00", "Please find attached invoice INV-100 for July."),
        ("<a2@apex>", "billing@apex.io", "Query about line items on INV-100",
         "2026-07-01 09:00:00", "Could you confirm the storage quantity billed on INV-100?"),
        ("<a3@apex>", "billing@apex.io", "Credit note against INV-100",
         "2026-08-01 09:00:00", "A credit note has been raised for the storage discrepancy."),
        ("<n1@next>", "accounts@next.au", "Monthly statement",
         "2026-07-15 09:00:00", "Attached is your statement. No overdue amounts."),
        ("<s1@syn>", "invoicing@syn.ai", "Invoice INV-300",
         "2026-07-20 09:00:00", "Consulting hours for July are attached."),
    ]
    for message_id, sender, subject, received, body in rows:
        store.record_email(message_id=message_id, sender=sender, subject=subject,
                           received_at=received, body_text=body, body_source="mock")
    return store.db_path


class TestSenderStrategy:
    def test_returns_only_that_sender(self, mailbox):
        hits = retrieval.retrieve(sender="billing@apex.io", strategy="sender",
                                  db_path=mailbox)
        assert len(hits) == 3
        assert {h["sender"] for h in hits} == {"billing@apex.io"}

    def test_newest_first(self, mailbox):
        hits = retrieval.retrieve(sender="billing@apex.io", strategy="sender",
                                  db_path=mailbox)
        dates = [h["received_at"] for h in hits]
        assert dates == sorted(dates, reverse=True)

    def test_the_limit_is_respected(self, mailbox):
        assert len(retrieval.retrieve(sender="billing@apex.io", strategy="sender",
                                      limit=2, db_path=mailbox)) == 2

    def test_an_unknown_sender_returns_nothing(self, mailbox):
        assert retrieval.retrieve(sender="nobody@nowhere.com", strategy="sender",
                                  db_path=mailbox) == []

    def test_it_needs_a_sender(self, mailbox):
        with pytest.raises(ValueError, match="sender"):
            retrieval.retrieve(strategy="sender", db_path=mailbox)


class TestKeywordStrategy:
    def test_the_most_relevant_email_ranks_first(self, mailbox):
        """The whole job. 'storage quantity' must surface the query, not the newest email."""
        hits = retrieval.retrieve(query="storage quantity", strategy="keyword",
                                  db_path=mailbox)
        assert "Query about line items" in hits[0]["subject"]

    def test_it_beats_date_order(self, mailbox):
        """The credit note is newer. A date-ordered retriever would return it first."""
        hits = retrieval.retrieve(query="storage quantity billed", strategy="keyword",
                                  db_path=mailbox)
        assert hits[0]["received_at"] < "2026-08-01"

    def test_common_words_do_not_dominate(self, mailbox):
        """'invoice' and 'attached' appear everywhere and separate nothing."""
        assert retrieval.retrieve(query="invoice attached please", strategy="keyword",
                                  db_path=mailbox) == []

    def test_an_empty_query_returns_nothing(self, mailbox):
        assert retrieval.retrieve(query="", strategy="keyword", db_path=mailbox) == []

    def test_a_query_matching_nothing_returns_nothing(self, mailbox):
        assert retrieval.retrieve(query="helicopter maintenance", strategy="keyword",
                                  db_path=mailbox) == []

    def test_every_hit_explains_itself(self, mailbox):
        hits = retrieval.retrieve(query="credit note", strategy="keyword", db_path=mailbox)
        assert all(h["why"].startswith("matched:") for h in hits)

    def test_keyword_alone_does_not_scope_to_a_vendor(self, mailbox):
        """A known limitation, recorded so it is not mistaken for a bug later.

        Keyword scoring reads text, not the sender, so a term used by several vendors
        returns all of them. Scoping needs sender and keyword combined, which is the
        hybrid strategy retrieval_eval.py exists to measure.
        """
        hits = retrieval.retrieve(query="statement", strategy="keyword", db_path=mailbox)
        assert hits and all("sender" not in h["why"] for h in hits)


class TestContextBlock:
    def test_every_block_carries_its_provenance(self, mailbox):
        """A block pasted into a prompt with no note that it was generated is how a
        measurement over mock data gets reported as one over real correspondence."""
        hits = retrieval.retrieve(sender="billing@apex.io", strategy="sender",
                                  db_path=mailbox)
        assert "source: mock" in retrieval.as_context(hits)

    def test_it_stays_within_the_character_budget(self, mailbox):
        hits = retrieval.retrieve(sender="billing@apex.io", strategy="sender",
                                  db_path=mailbox)
        assert len(retrieval.as_context(hits, max_chars=200)) <= 200

    def test_no_hits_gives_an_empty_block(self):
        assert retrieval.as_context([]) == ""


class TestInterface:
    def test_an_unknown_strategy_is_refused(self, mailbox):
        with pytest.raises(ValueError, match="unknown strategy"):
            retrieval.retrieve(query="x", strategy="embeddings", db_path=mailbox)
