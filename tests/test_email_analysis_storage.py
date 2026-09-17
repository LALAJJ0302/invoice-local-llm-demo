"""Tests for storing the email AI module's output.

Covers migration 010 and 011, the four analysis tables, and the five storage methods that
write and read them. Nothing here imports email_ai: that module does `from ollama import
chat` at import time, and the point of the mapping-shaped interface is that storage and its
tests do not need Ollama installed. The records below are the shape
`EmailAnalysisRecord.model_dump()` produces, written out by hand so that a change to his
models fails here loudly rather than passing against a mock of itself.

The one thing these tests cannot check is that the shape stays true. If JJ renames a field,
this file keeps passing and the integration breaks. That is what the field list in
email-analysis-schema-spec.md section 0 is for, and it is why the records are transcribed
rather than generated.
"""

import os
import sqlite3
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from storage import (  # noqa: E402
    StorageManager,
    UnknownEmail,
    coerce_deadline_date,
    connect,
)


@pytest.fixture
def store(tmp_path):
    return StorageManager(str(tmp_path / "analysis.db"))


@pytest.fixture
def mailbox(store):
    """Two emails and an email run, which is the minimum an analysis needs to exist."""
    store.record_email("<a@mail>", "pm@client.com", "Budget review", attachment_count=0)
    store.record_email("<b@mail>", "pm@client.com", "Re: Budget review", attachment_count=0)
    return store.start_email_run("llama3.2:latest")


def email_record(run_id, *, message_id="<a@mail>", category="Project update",
                 status="Validated", reason=None, attempts=1, actions=None):
    return {
        "message_id": message_id,
        "run_id": run_id,
        "processed_at": "2026-09-17T09:00:00Z",
        "validation_status": status,
        "validation_reason": reason,
        "attempt_count": attempts,
        "analysis": {
            "category": category,
            "summary": "The client asked for the revised budget.",
            "action_items": actions if actions is not None else [{
                "task": "Send the revised budget document",
                "owner": "Neo",
                "deadline_text": "by 30 September 2026",
                "evidence_quote": "Please send the revised budget by 30 September 2026.",
            }],
        },
    }


def thread_record(run_id, *, thread_id="<a@mail>", latest="<b@mail>",
                  status="Validated", reason=None, decisions=None, actions=None):
    return {
        "thread_id": thread_id,
        "latest_message_id": latest,
        "run_id": run_id,
        "processed_at": "2026-09-17T09:05:00Z",
        "validation_status": status,
        "validation_reason": reason,
        "attempt_count": 1,
        "analysis": {
            "summary": "The budget was agreed and the revision is outstanding.",
            "latest_decisions": decisions if decisions is not None else [
                "The budget ceiling stays at 40,000.",
                "The revision is due before the September board meeting.",
            ],
            "outstanding_actions": actions if actions is not None else [{
                "task": "Circulate the revised budget",
                "owner": "Neo",
                "deadline_text": "end of month",
                "evidence_quote": "Please circulate it by end of month.",
            }],
        },
    }


# =====================================================================
# Runs
# =====================================================================
class TestRunKind:
    def test_an_email_run_has_no_threshold(self, store):
        run_id = store.start_email_run("llama3.2:latest")
        with connect(store.db_path) as conn:
            row = conn.execute(
                "SELECT run_kind, threshold FROM processing_runs WHERE run_id = ?",
                (run_id,)).fetchone()
        assert row["run_kind"] == "email"
        assert row["threshold"] is None

    def test_an_invoice_run_still_has_one(self, store):
        run_id = store.start_run("llama3.2:latest", 0.8)
        with connect(store.db_path) as conn:
            row = conn.execute(
                "SELECT run_kind, threshold FROM processing_runs WHERE run_id = ?",
                (run_id,)).fetchone()
        assert row["run_kind"] == "invoice"
        assert row["threshold"] == 0.8

    def test_an_invoice_run_without_a_threshold_is_rejected(self, store):
        """The pairing is the whole reason threshold became nullable."""
        with connect(store.db_path) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO processing_runs (model_name, threshold, run_kind) "
                    "VALUES ('x', NULL, 'invoice')")

    def test_an_email_run_with_a_threshold_is_rejected(self, store):
        with connect(store.db_path) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO processing_runs (model_name, threshold, run_kind) "
                    "VALUES ('x', 0.8, 'email')")


# =====================================================================
# Saving and reading back
# =====================================================================
class TestEmailAnalysis:
    def test_round_trip(self, store, mailbox):
        result = store.save_email_analysis(email_record(mailbox))
        assert result["was_update"] is False
        assert result["action_item_count"] == 1

        stored = store.email_analysis_for("<a@mail>")
        assert stored["category"] == "Project update"
        assert stored["validation_status"] == "Validated"
        assert stored["validation_reason"] is None
        assert stored["attempt_count"] == 1
        assert stored["processed_at"] == "2026-09-17T09:00:00Z"
        assert len(stored["action_items"]) == 1
        assert stored["action_items"][0]["owner"] == "Neo"

    def test_a_needs_review_record_is_stored_not_dropped(self, store, mailbox):
        """The point of JJ's retry change: a failed analysis is retained for measurement."""
        store.save_email_analysis(email_record(
            mailbox, status="NeedsReview", reason="evidence_quote_not_found", attempts=3))
        stored = store.email_analysis_for("<a@mail>")
        assert stored["validation_status"] == "NeedsReview"
        assert stored["validation_reason"] == "evidence_quote_not_found"
        assert stored["attempt_count"] == 3

    def test_resaving_the_same_run_updates_in_place(self, store, mailbox):
        store.save_email_analysis(email_record(mailbox))
        again = store.save_email_analysis(email_record(mailbox, category="Meeting"))
        assert again["was_update"] is True

        with connect(store.db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM email_analysis").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM action_items").fetchone()[0] == 1
        assert store.email_analysis_for("<a@mail>")["category"] == "Meeting"

    def test_two_runs_over_the_same_email_are_two_rows(self, store, mailbox):
        """This is the comparison run_id exists for, and it is why model_name came off."""
        other = store.start_email_run("mistral:latest")
        store.save_email_analysis(email_record(mailbox, category="Project update"))
        store.save_email_analysis(email_record(other, category="Meeting"))

        with connect(store.db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM email_analysis").fetchone()[0] == 2
        assert store.email_analysis_for("<a@mail>", run_id=mailbox)["category"] == "Project update"
        assert store.email_analysis_for("<a@mail>", run_id=other)["category"] == "Meeting"
        assert store.email_analysis_for("<a@mail>")["run_id"] == other

    def test_an_unknown_message_id_raises_rather_than_orphaning(self, store, mailbox):
        with pytest.raises(UnknownEmail):
            store.save_email_analysis(email_record(mailbox, message_id="<never-seen@mail>"))
        with connect(store.db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM email_analysis").fetchone()[0] == 0

    def test_missing_analysis_returns_none(self, store, mailbox):
        assert store.email_analysis_for("<a@mail>") is None

    def test_deleting_the_email_cascades(self, store, mailbox):
        store.save_email_analysis(email_record(mailbox))
        with connect(store.db_path) as conn:
            conn.execute("DELETE FROM email_messages WHERE message_id = '<a@mail>'")
            conn.commit()
            assert conn.execute("SELECT COUNT(*) FROM email_analysis").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM action_items").fetchone()[0] == 0

    def test_no_action_items_is_fine(self, store, mailbox):
        result = store.save_email_analysis(email_record(mailbox, actions=[]))
        assert result["action_item_count"] == 0
        assert store.email_analysis_for("<a@mail>")["action_items"] == []


class TestThreadAnalysis:
    def test_round_trip_keeps_decision_order(self, store, mailbox):
        result = store.save_thread_analysis(thread_record(mailbox), thread_source="subject")
        assert result["decision_count"] == 2

        stored = store.thread_analysis_for("<a@mail>")
        assert stored["thread_source"] == "subject"
        assert stored["latest_decisions"] == [
            "The budget ceiling stays at 40,000.",
            "The revision is due before the September board meeting.",
        ]
        assert len(stored["outstanding_actions"]) == 1

    def test_a_thread_whose_latest_message_is_unknown_still_saves(self, store, mailbox):
        """latest_message_id is str | None, and a thread analysed from text that was never
        in the mailbox has nothing to point at. Losing the analysis would be worse."""
        result = store.save_thread_analysis(
            thread_record(mailbox, latest=None), thread_source="manual")
        assert result["latest_email_id"] is None
        assert store.thread_analysis_for("<a@mail>")["latest_email_id"] is None

    def test_an_unknown_latest_message_does_not_raise(self, store, mailbox):
        result = store.save_thread_analysis(
            thread_record(mailbox, latest="<never-seen@mail>"), thread_source="subject")
        assert result["latest_email_id"] is None

    def test_resaving_replaces_decisions_rather_than_appending(self, store, mailbox):
        store.save_thread_analysis(thread_record(mailbox), thread_source="subject")
        store.save_thread_analysis(
            thread_record(mailbox, decisions=["Only one now."]), thread_source="subject")
        with connect(store.db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM thread_decisions").fetchone()[0] == 1
        assert store.thread_analysis_for("<a@mail>")["latest_decisions"] == ["Only one now."]

    def test_an_invented_thread_source_is_refused(self, store, mailbox):
        with pytest.raises(ValueError):
            store.save_thread_analysis(thread_record(mailbox), thread_source="guessed")

    def test_deleting_the_thread_analysis_cascades(self, store, mailbox):
        store.save_thread_analysis(thread_record(mailbox), thread_source="subject")
        with connect(store.db_path) as conn:
            conn.execute("DELETE FROM thread_analysis")
            conn.commit()
            assert conn.execute("SELECT COUNT(*) FROM thread_decisions").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM action_items").fetchone()[0] == 0

    def test_an_email_and_a_thread_action_coexist(self, store, mailbox):
        """The two parents share one table, so this is the case that proves the CHECK is
        not too tight."""
        store.save_email_analysis(email_record(mailbox))
        store.save_thread_analysis(thread_record(mailbox), thread_source="subject")
        with connect(store.db_path) as conn:
            counts = conn.execute(
                "SELECT COUNT(analysis_id) e, COUNT(thread_analysis_id) t FROM action_items"
            ).fetchone()
        assert (counts["e"], counts["t"]) == (1, 1)


# =====================================================================
# The constraints
# =====================================================================
class TestConstraints:
    def test_a_seventh_category_is_rejected(self, store, mailbox):
        with pytest.raises(sqlite3.IntegrityError):
            store.save_email_analysis(email_record(mailbox, category="Newsletter"))

    def test_validated_with_a_reason_is_rejected(self, store, mailbox):
        with pytest.raises(sqlite3.IntegrityError):
            store.save_email_analysis(email_record(
                mailbox, status="Validated", reason="evidence_quote_not_found"))

    def test_needs_review_without_a_reason_is_rejected(self, store, mailbox):
        """A result held back for a person has to say what was wrong with it, or the
        failures cannot be counted, which is the whole reason the column exists."""
        with pytest.raises(sqlite3.IntegrityError):
            store.save_email_analysis(email_record(mailbox, status="NeedsReview", reason=None))

    def test_a_reason_containing_a_space_is_rejected(self, store, mailbox):
        """A code can be grouped; a sentence drifts and cannot."""
        with pytest.raises(sqlite3.IntegrityError):
            store.save_email_analysis(email_record(
                mailbox, status="NeedsReview", reason="the quote was not found"))

    def test_a_higher_attempt_count_is_accepted(self, store, mailbox):
        """Deliberately not capped at 3. The cap lives in email_ai.MAX_EVIDENCE_ATTEMPTS,
        and repeating it here would mean raising that constant needs a migration."""
        store.save_email_analysis(email_record(
            mailbox, status="NeedsReview", reason="evidence_quote_not_found", attempts=5))
        assert store.email_analysis_for("<a@mail>")["attempt_count"] == 5

    def test_zero_attempts_is_rejected(self, store, mailbox):
        with pytest.raises(sqlite3.IntegrityError):
            store.save_email_analysis(email_record(mailbox, attempts=0))

    def test_an_action_item_with_both_parents_is_rejected(self, store, mailbox):
        with connect(store.db_path) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO action_items (analysis_id, thread_analysis_id, item_no, "
                    "task, evidence_quote) VALUES (1, 1, 1, 't', 'q')")

    def test_an_action_item_with_neither_parent_is_rejected(self, store, mailbox):
        with connect(store.db_path) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO action_items (item_no, task, evidence_quote) "
                    "VALUES (1, 't', 'q')")

    def test_a_normalised_date_without_wording_is_rejected(self, store, mailbox):
        """Storage may not produce a deadline with nothing behind it."""
        store.save_email_analysis(email_record(mailbox))
        with connect(store.db_path) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE action_items SET deadline_text = NULL, "
                    "deadline_date = '2026-09-30'")

    def test_two_items_cannot_share_an_ordinal_under_one_parent(self, store, mailbox):
        store.save_email_analysis(email_record(mailbox))
        with connect(store.db_path) as conn:
            analysis_id = conn.execute("SELECT analysis_id FROM email_analysis").fetchone()[0]
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO action_items (analysis_id, item_no, task, evidence_quote) "
                    "VALUES (?, 1, 't', 'q')", (analysis_id,))


# =====================================================================
# Deadline normalisation
# =====================================================================
class TestDeadlineNormalisation:
    @pytest.mark.parametrize("wording,expected", [
        ("2026-09-30", "2026-09-30"),
        ("by 30 September 2026", "2026-09-30"),
        ("before 30 Sep 2026", "2026-09-30"),
        ("September 30, 2026", "2026-09-30"),
        ("due by 30 September 2026", "2026-09-30"),
        ("no later than 30 September 2026", "2026-09-30"),
        ("by 30th September 2026", "2026-09-30"),
        ("on 30 September 2026.", "2026-09-30"),
    ])
    def test_unambiguous_wording_is_normalised(self, wording, expected):
        assert coerce_deadline_date(wording) == expected

    @pytest.mark.parametrize("wording", [
        "30/09/2026",       # day-month order is not knowable
        "09/30/2026",
        "end of month",
        "by Friday",
        "next week",
        "ASAP",
        "EOD",
        "",
        None,
    ])
    def test_ambiguous_wording_stays_null(self, wording):
        assert coerce_deadline_date(wording) is None

    def test_an_iso_date_is_never_reparsed(self):
        """dateutil.parser.parse('2026-03-12', dayfirst=True) returns 3 December, because
        dayfirst is applied to the last two components whatever the shape of the string.
        Any date handling in this project returns an already-ISO value untouched."""
        assert coerce_deadline_date("2026-03-12") == "2026-03-12"
        assert coerce_deadline_date("by 2026-03-12") == "2026-03-12"

    def test_the_wording_survives_when_the_date_cannot_be_read(self, store, mailbox):
        store.save_email_analysis(email_record(mailbox, actions=[{
            "task": "Circulate the revised budget",
            "owner": None,
            "deadline_text": "end of month",
            "evidence_quote": "Please circulate it by end of month.",
        }]))
        item = store.email_analysis_for("<a@mail>")["action_items"][0]
        assert item["deadline_text"] == "end of month"
        assert item["deadline_date"] is None

    def test_the_undated_count_is_reported(self, store, mailbox):
        """How often a deadline cannot be normalised is a number worth having, which is why
        it is returned rather than left to be counted later."""
        result = store.save_email_analysis(email_record(mailbox, actions=[
            {"task": "a", "owner": None, "deadline_text": "by 30 September 2026",
             "evidence_quote": "q"},
            {"task": "b", "owner": None, "deadline_text": "end of month",
             "evidence_quote": "q"},
            {"task": "c", "owner": None, "deadline_text": None, "evidence_quote": "q"},
        ]))
        assert (result["dated_count"], result["undated_count"]) == (1, 1)


# =====================================================================
# The contract with JJ's module
# =====================================================================
class TestAgainstTheRealRecords:
    """Builds real EmailAnalysisRecord and ThreadAnalysisRecord objects and stores them.

    Every other test in this file transcribes the record shape by hand, which cannot catch a
    rename on JJ's side. This one can: it constructs his Pydantic models and feeds
    model_dump() straight into storage, so a renamed field fails here rather than in the
    group's integration.

    No model is called. email_ai imports ollama at module scope, so the whole class skips
    where that package is absent, which is the reason storage itself must never import it.
    """

    @pytest.fixture
    def ai(self):
        pytest.importorskip("ollama")
        import email_ai
        return email_ai

    def test_an_email_record_round_trips(self, ai, store, mailbox):
        outcome = ai.EmailAnalysisOutcome(
            analysis=ai.EmailAnalysis(
                category="Project update",
                summary="The client asked for the revised budget.",
                action_items=[ai.ActionItem(
                    task="Send the revised budget",
                    owner="Neo",
                    deadline_text="by 30 September 2026",
                    evidence_quote="Please send the revised budget by 30 September 2026.",
                )],
            ),
            validation_status="NeedsReview",
            validation_reason="evidence_quote_not_found",
            attempt_count=3,
        )
        message = ai.EmailMessageInput(
            message_id="<a@mail>", sent_at=None, subject="Budget",
            sender="pm@client.com", body="body", attachments=[])
        record = ai.create_email_analysis_record(message, outcome, run_id=mailbox)

        store.save_email_analysis(record.model_dump(mode="json"))

        stored = store.email_analysis_for("<a@mail>")
        assert stored["validation_status"] == "NeedsReview"
        assert stored["validation_reason"] == "evidence_quote_not_found"
        assert stored["attempt_count"] == 3
        assert stored["action_items"][0]["deadline_date"] == "2026-09-30"

    def test_a_thread_record_round_trips(self, ai, store, mailbox):
        outcome = ai.ThreadAnalysisOutcome(
            analysis=ai.ThreadSummary(
                summary="The budget was agreed and the revision is outstanding.",
                latest_decisions=["The ceiling stays at 40,000.", "Due before the board."],
                outstanding_actions=[ai.ActionItem(
                    task="Circulate the revised budget",
                    owner=None,
                    deadline_text="end of month",
                    evidence_quote="Please circulate it by end of month.",
                )],
            ),
            validation_status="Validated",
            validation_reason=None,
            attempt_count=1,
        )
        message = ai.EmailMessageInput(
            message_id="<a@mail>", sent_at=None, subject="Budget",
            sender="pm@client.com", body="body", attachments=[])
        thread = ai.EmailThreadInput(thread_id="<a@mail>", messages=[message])
        record = ai.create_thread_analysis_record(thread, outcome, run_id=mailbox)

        store.save_thread_analysis(record.model_dump(mode="json"), thread_source="subject")

        stored = store.thread_analysis_for("<a@mail>")
        assert stored["latest_decisions"] == [
            "The ceiling stays at 40,000.", "Due before the board."]
        assert stored["outstanding_actions"][0]["deadline_text"] == "end of month"
        assert stored["outstanding_actions"][0]["deadline_date"] is None

    def test_every_category_he_declares_is_accepted(self, ai, store, mailbox):
        """The CHECK constraint and his Literal have to hold the same six values. If he adds
        a seventh, this fails and the migration that adds it is the fix."""
        from typing import get_args
        declared = set(get_args(ai.EmailAnalysis.model_fields["category"].annotation))
        assert declared == set(storage_categories())

        for index, category in enumerate(sorted(declared)):
            run_id = store.start_email_run(f"model-{index}")
            store.save_email_analysis(email_record(run_id, category=category))


def storage_categories():
    from storage import VALID_EMAIL_CATEGORIES
    return VALID_EMAIL_CATEGORIES
