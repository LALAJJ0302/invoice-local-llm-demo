"""Tests for the file that connects email_ai to storage.

No test here calls Ollama. `email_ai.analyse_email` and `summarise_thread` are replaced with
stubs, because what is under test is the wiring: which emails are selected, how they are
grouped, what happens when one raises, and whether a re-run updates or duplicates. Model
quality is a different question and it cannot be answered at all until there is ground truth.
"""

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

pytest.importorskip("ollama")

import email_ai  # noqa: E402
import email_pipeline  # noqa: E402
from storage import StorageManager, connect  # noqa: E402


@pytest.fixture
def store(tmp_path):
    return StorageManager(str(tmp_path / "pipeline.db"))


@pytest.fixture
def mailbox(store):
    """A mailbox with the subject collision the mock data actually contains."""
    store.record_email("<a1@apex>", "billing@apex.io", "Invoice INV-2026-005",
                       body_text="Please pay invoice INV-2026-005.", body_source="mock")
    store.record_email("<a2@apex>", "ap@client.com", "Re: Invoice INV-2026-005",
                       body_text="Payment sent for INV-2026-005.", body_source="mock")
    # Three vendors, one subject. This is the defect subject grouping has, present on purpose.
    store.record_email("<m1@apex>", "billing@apex.io", "Monthly statement",
                       body_text="Statement from Apex.", body_source="mock")
    store.record_email("<m2@nextgen>", "billing@nextgen.io", "Monthly statement",
                       body_text="Statement from NextGen.", body_source="mock")
    store.record_email("<m3@synthetix>", "billing@synthetix.io", "Monthly statement",
                       body_text="Statement from Synthetix.", body_source="mock")
    # No body. Skipped, not failed.
    store.record_email("<empty@apex>", "billing@apex.io", "Headers only")
    return store


def stub_outcome(category="Invoice", status="Validated", reason=None, actions=None):
    return email_ai.EmailAnalysisOutcome(
        analysis=email_ai.EmailAnalysis(
            category=category, summary="A stub summary.",
            action_items=actions if actions is not None else []),
        validation_status=status, validation_reason=reason, attempt_count=1)


def stub_thread_outcome(decisions=None, actions=None):
    return email_ai.ThreadAnalysisOutcome(
        analysis=email_ai.ThreadSummary(
            summary="A stub thread summary.",
            latest_decisions=decisions if decisions is not None else ["Agreed."],
            outstanding_actions=actions if actions is not None else []),
        validation_status="Validated", validation_reason=None, attempt_count=1)


@pytest.fixture
def no_model(monkeypatch):
    """Every test runs against this unless it asks for something else."""
    monkeypatch.setattr(email_pipeline.email_ai, "analyse_email",
                        lambda message: stub_outcome())
    monkeypatch.setattr(email_pipeline.email_ai, "summarise_thread",
                        lambda thread: stub_thread_outcome())


# =====================================================================
# Subject grouping
# =====================================================================
class TestThreadKey:
    @pytest.mark.parametrize("subject,expected", [
        ("Invoice INV-2026-005", "invoice inv-2026-005"),
        ("Re: Invoice INV-2026-005", "invoice inv-2026-005"),
        ("RE: Invoice INV-2026-005", "invoice inv-2026-005"),
        ("Fwd: Invoice INV-2026-005", "invoice inv-2026-005"),
        ("FW: Invoice INV-2026-005", "invoice inv-2026-005"),
        ("Re: Re: Fwd: Invoice INV-2026-005", "invoice inv-2026-005"),
        ("re:re:Invoice INV-2026-005", "invoice inv-2026-005"),
        ("  Invoice   INV-2026-005  ", "invoice inv-2026-005"),
        ("INVOICE inv-2026-005", "invoice inv-2026-005"),
    ])
    def test_a_reply_chain_is_stripped(self, subject, expected):
        assert email_pipeline.thread_key(subject) == expected

    def test_a_missing_subject_does_not_raise(self):
        assert email_pipeline.thread_key(None) == ""
        assert email_pipeline.thread_key("") == ""

    def test_a_subject_that_only_looks_like_a_prefix_survives(self):
        """'Reminder' starts with 're' and must not be eaten."""
        assert email_pipeline.thread_key("Reminder: pay us") == "reminder: pay us"
        assert email_pipeline.thread_key("Review the budget") == "review the budget"


class TestGrouping:
    def test_three_vendors_one_subject_become_one_thread(self, mailbox):
        """The known defect, asserted as present rather than fixed.

        This is what thread_source exists to record, and it is why no thread summary from this
        path may be reported as though it came from a reply chain."""
        result = email_pipeline.assign_threads(mailbox)
        assert result["collision_threads"] == 1

        with connect(mailbox.db_path) as conn:
            senders = conn.execute("""
                SELECT COUNT(DISTINCT sender) c FROM email_messages
                WHERE thread_id = 'monthly statement'""").fetchone()["c"]
        assert senders == 3

    def test_a_reply_joins_its_parent(self, mailbox):
        email_pipeline.assign_threads(mailbox)
        with connect(mailbox.db_path) as conn:
            rows = conn.execute(
                "SELECT message_id FROM email_messages WHERE thread_id = 'invoice inv-2026-005' "
                "ORDER BY email_id").fetchall()
        assert [r["message_id"] for r in rows] == ["<a1@apex>", "<a2@apex>"]

    def test_every_row_is_labelled_a_guess(self, mailbox):
        email_pipeline.assign_threads(mailbox)
        with connect(mailbox.db_path) as conn:
            sources = {r["thread_source"] for r in
                       conn.execute("SELECT thread_source FROM email_messages")}
        assert sources == {"subject"}

    def test_is_idempotent(self, mailbox):
        first = email_pipeline.assign_threads(mailbox)
        second = email_pipeline.assign_threads(mailbox)
        assert first == second

    def test_a_thread_from_real_headers_is_never_overwritten(self, mailbox):
        """When intake starts capturing In-Reply-To, a guess must not clobber the truth."""
        with connect(mailbox.db_path) as conn:
            conn.execute("UPDATE email_messages SET thread_id = 'real-chain', "
                         "thread_source = 'headers' WHERE message_id = '<m1@apex>'")
            conn.commit()

        email_pipeline.assign_threads(mailbox)

        with connect(mailbox.db_path) as conn:
            row = conn.execute("SELECT thread_id, thread_source FROM email_messages "
                               "WHERE message_id = '<m1@apex>'").fetchone()
        assert (row["thread_id"], row["thread_source"]) == ("real-chain", "headers")


# =====================================================================
# Selection and the run
# =====================================================================
class TestSelection:
    def test_an_email_without_a_body_is_skipped_not_failed(self, mailbox, no_model):
        code, summary = email_pipeline.run(db_path=mailbox.db_path)
        assert code == 0
        assert summary["analysed"] == 5
        assert summary["failed"] == 0

    def test_limit_stops_where_it_says(self, mailbox, no_model):
        _, summary = email_pipeline.run(db_path=mailbox.db_path, limit=2)
        assert summary["analysed"] == 2

    def test_dry_run_writes_nothing_and_calls_nothing(self, mailbox, monkeypatch):
        def explode(message):
            raise AssertionError("the model must not be called on a dry run")

        monkeypatch.setattr(email_pipeline.email_ai, "analyse_email", explode)
        with connect(mailbox.db_path) as conn:
            messages_before = [
                tuple(row) for row in conn.execute(
                    "SELECT email_id, thread_id, thread_source "
                    "FROM email_messages ORDER BY email_id"
                )
            ]

        code, summary = email_pipeline.run(db_path=mailbox.db_path, dry_run=True)

        assert code == 0
        assert summary == {"planned": 5}
        with connect(mailbox.db_path) as conn:
            messages_after = [
                tuple(row) for row in conn.execute(
                    "SELECT email_id, thread_id, thread_source "
                    "FROM email_messages ORDER BY email_id"
                )
            ]
            assert messages_after == messages_before
            assert conn.execute("SELECT COUNT(*) c FROM email_analysis").fetchone()["c"] == 0
            assert conn.execute("SELECT COUNT(*) c FROM processing_runs").fetchone()["c"] == 0


class TestRun:
    def test_results_reach_the_database(self, mailbox, no_model):
        email_pipeline.run(db_path=mailbox.db_path)
        stored = mailbox.email_analysis_for("<a1@apex>")
        assert stored["category"] == "Invoice"
        assert stored["validation_status"] == "Validated"

    def test_the_run_is_opened_as_an_email_run_and_closed(self, mailbox, no_model):
        email_pipeline.run(db_path=mailbox.db_path, model="qwen3:14b")
        with connect(mailbox.db_path) as conn:
            row = conn.execute("SELECT * FROM processing_runs").fetchone()
        assert row["run_kind"] == "email"
        assert row["threshold"] is None
        assert row["model_name"] == "qwen3:14b"
        assert row["finished_at"] is not None
        assert row["doc_count"] == 5

    def test_a_needs_review_result_is_counted_and_stored(self, mailbox, monkeypatch):
        monkeypatch.setattr(
            email_pipeline.email_ai, "analyse_email",
            lambda message: stub_outcome(status="NeedsReview",
                                         reason="evidence_quote_not_found"))
        _, summary = email_pipeline.run(db_path=mailbox.db_path)
        assert summary["needs_review"] == 5 and summary["validated"] == 0
        assert mailbox.email_analysis_for("<a1@apex>")["validation_reason"] == \
            "evidence_quote_not_found"

    def test_one_bad_email_does_not_end_the_run(self, mailbox, monkeypatch):
        """A run over twenty messages must survive one malformed message."""
        def sometimes(message):
            if message.message_id == "<m2@nextgen>":
                raise ValueError("model returned unparsable JSON")
            return stub_outcome()

        monkeypatch.setattr(email_pipeline.email_ai, "analyse_email", sometimes)
        code, summary = email_pipeline.run(db_path=mailbox.db_path)

        assert code == 1, "a failure must be visible in the exit code"
        assert (summary["analysed"], summary["failed"]) == (4, 1)
        with connect(mailbox.db_path) as conn:
            open_runs = conn.execute(
                "SELECT COUNT(*) c FROM processing_runs WHERE finished_at IS NULL"
            ).fetchone()["c"]
        assert open_runs == 0, "an open run means a crash, and reprocess.py --list reads that"

    def test_rerunning_updates_rather_than_duplicating(self, mailbox, no_model):
        email_pipeline.run(db_path=mailbox.db_path)
        email_pipeline.run(db_path=mailbox.db_path)
        with connect(mailbox.db_path) as conn:
            analyses = conn.execute("SELECT COUNT(*) c FROM email_analysis").fetchone()["c"]
            runs = conn.execute("SELECT COUNT(*) c FROM processing_runs").fetchone()["c"]
        # Two runs, so two rows per email. That is the model comparison run_id exists for,
        # not a duplicate: a duplicate would be two rows for the same run.
        assert (analyses, runs) == (10, 2)
        with connect(mailbox.db_path) as conn:
            per_run = conn.execute(
                "SELECT COUNT(*) c FROM email_analysis GROUP BY email_id, run_id "
                "HAVING COUNT(*) > 1").fetchall()
        assert per_run == []

    def test_action_items_and_their_undated_count_are_reported(self, mailbox, monkeypatch):
        monkeypatch.setattr(
            email_pipeline.email_ai, "analyse_email",
            lambda message: stub_outcome(actions=[
                email_ai.ActionItem(task="Pay it", owner="Neo",
                                    deadline_text="by 30 September 2026",
                                    evidence_quote="Pay by 30 September 2026."),
                email_ai.ActionItem(task="Chase it", owner=None,
                                    deadline_text="end of month",
                                    evidence_quote="Chase by end of month."),
            ]))
        _, summary = email_pipeline.run(db_path=mailbox.db_path)
        assert summary["actions"] == 10
        assert summary["undated"] == 5


class TestThreads:
    def test_threads_are_summarised_and_stored(self, mailbox, no_model):
        _, summary = email_pipeline.run(db_path=mailbox.db_path, threads=True)
        assert summary["threads"] == 2      # 2 invoice messages collapse, 3 statements collapse

        stored = mailbox.thread_analysis_for("monthly statement")
        assert stored["thread_source"] == "subject"
        assert stored["latest_decisions"] == ["Agreed."]

    def test_a_failing_thread_does_not_end_the_run(self, mailbox, no_model, monkeypatch):
        def sometimes(thread):
            if thread.thread_id == "monthly statement":
                raise ValueError("model returned unparsable JSON")
            return stub_thread_outcome()

        monkeypatch.setattr(email_pipeline.email_ai, "summarise_thread", sometimes)
        _, summary = email_pipeline.run(db_path=mailbox.db_path, threads=True)
        assert (summary["threads"], summary["thread_failed"]) == (1, 1)

    def test_threads_are_off_by_default(self, mailbox, no_model):
        email_pipeline.run(db_path=mailbox.db_path)
        with connect(mailbox.db_path) as conn:
            assert conn.execute("SELECT COUNT(*) c FROM thread_analysis").fetchone()["c"] == 0


class TestTheCollisionMetric:
    """The first version of this counted senders per thread, and it was wrong.

    A genuine reply chain normally spans several senders, because a reply comes from a
    different address than the message it answers. Counting senders flagged every real
    conversation as a defect and hid the real collision inside that number. Caught by the
    fixture above, which holds one real two-sender thread and one three-way collision.
    """

    def test_a_real_two_sender_thread_is_not_a_collision(self, mailbox):
        result = email_pipeline.assign_threads(mailbox)
        assert result["collision_threads"] == 1, "only 'Monthly statement' should count"

    def test_a_thread_with_a_reply_is_never_a_collision(self, store):
        store.record_email("<x1@a>", "a@a.com", "Budget", body_text="b", body_source="mock")
        store.record_email("<x2@b>", "b@b.com", "Re: Budget", body_text="b", body_source="mock")
        assert email_pipeline.assign_threads(store)["collision_threads"] == 0

    def test_two_originals_sharing_a_subject_are_a_collision(self, store):
        store.record_email("<y1@a>", "a@a.com", "Monthly statement",
                           body_text="b", body_source="mock")
        store.record_email("<y2@b>", "b@b.com", "Monthly statement",
                           body_text="b", body_source="mock")
        assert email_pipeline.assign_threads(store)["collision_threads"] == 1

    def test_one_message_alone_is_never_a_collision(self, store):
        store.record_email("<z1@a>", "a@a.com", "Solo", body_text="b", body_source="mock")
        result = email_pipeline.assign_threads(store)
        assert (result["threads"], result["collision_threads"]) == (1, 0)

    def test_the_false_positive_is_known(self, store):
        """Somebody who replies after deleting the 'Re:' is counted as a collision.

        Asserted so the limitation is recorded rather than discovered later. Subject matching
        cannot tell this apart from a real collision, which is the argument for capturing
        In-Reply-To at intake instead of a cleverer heuristic here."""
        store.record_email("<w1@a>", "a@a.com", "Budget", body_text="b", body_source="mock")
        store.record_email("<w2@b>", "b@b.com", "Budget", body_text="reply", body_source="mock")
        assert email_pipeline.assign_threads(store)["collision_threads"] == 1
