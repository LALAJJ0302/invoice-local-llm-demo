import json
from types import SimpleNamespace
from unittest.mock import Mock

import email_ai
from email_ai import ActionItem, EmailMessageInput, ParsedAttachment


def fake_ollama_response(payload: dict) -> SimpleNamespace:
    """Create an object shaped like an Ollama chat response."""
    return SimpleNamespace(
        message=SimpleNamespace(
            content=json.dumps(payload)
        )
    )


def test_unknown_owner_becomes_none():
    item = ActionItem(
        task="send the report",
        owner="Unknown",
        deadline_text=None,
        evidence_quote="Please send the report.",
    )

    assert item.owner is None


def test_build_analysis_text_includes_email_and_attachment():
    message = EmailMessageInput(
        message_id="message-001",
        subject="Design update",
        sender="Alex",
        body="Please update the mobile layout.",
        attachments=[
            ParsedAttachment(
                filename="notes.txt",
                content="Jamie owns the accessibility review.",
            )
        ],
    )

    result = email_ai.build_analysis_text(message)

    assert "SUBJECT: Design update" in result
    assert "SENDER: Alex" in result
    assert "Please update the mobile layout." in result
    assert "ATTACHMENT: notes.txt" in result
    assert "Jamie owns the accessibility review." in result


def test_analyse_email_without_calling_real_ollama(monkeypatch):
    overview_response = fake_ollama_response(
        {
            "category": "Project update",
            "summary": "The email requests an updated mobile layout.",
        }
    )

    action_response = fake_ollama_response(
        {
            "action_items": [
                {
                    "task": "update the mobile layout",
                    "owner": None,
                    "deadline_text": "by Friday",
                    "evidence_quote": (
                        "Please update the mobile layout by Friday."
                    ),
                }
            ]
        }
    )

    mock_chat = Mock(
        side_effect=[overview_response, action_response]
    )

    # email_ai.py imports chat directly, so patch email_ai.chat.
    monkeypatch.setattr(email_ai, "chat", mock_chat)

    message = EmailMessageInput(
        message_id="message-002",
        subject="Mobile layout",
        sender="Alex",
        body="Please update the mobile layout by Friday.",
    )

    outcome = email_ai.analyse_email(message)
    result = outcome.analysis

    assert outcome.validation_status == "Validated"
    assert outcome.validation_reason is None
    assert outcome.attempt_count == 1

    assert result.category == "Project update"
    assert result.summary == (
        "The email requests an updated mobile layout."
    )
    assert len(result.action_items) == 1
    assert result.action_items[0].task == "update the mobile layout"
    assert result.action_items[0].deadline_text == "by Friday"

    assert mock_chat.call_count == 2


def test_analyse_email_keeps_result_after_three_evidence_failures(monkeypatch):
    overview_response = fake_ollama_response(
        {
            "category": "Project update",
            "summary": "The email requests a report.",
        }
    )

    action_response = fake_ollama_response(
        {
            "action_items": [
                {
                    "task": "send the financial report",
                    "owner": None,
                    "deadline_text": "by Friday",
                    "evidence_quote": (
                        "Please send the financial report by Friday."
                    ),
                }
            ]
        }
    )

    mock_chat = Mock(
        side_effect=[
            overview_response,
            action_response,
            action_response,
            action_response,
        ]
    )
    monkeypatch.setattr(email_ai, "chat", mock_chat)

    message = EmailMessageInput(
        message_id="message-003",
        subject="Weekly update",
        sender="Alex",
        body="Please send the design report by Monday.",
    )

    outcome = email_ai.analyse_email(message)

    assert outcome.validation_status == "NeedsReview"
    assert outcome.validation_reason == "evidence_quote_not_found"
    assert outcome.attempt_count == 3
    assert outcome.analysis.action_items[0].task == "send the financial report"
    assert mock_chat.call_count == 4


def test_analyse_email_recovers_on_second_evidence_attempt(monkeypatch):
    overview_response = fake_ollama_response(
        {
            "category": "Project update",
            "summary": "The email requests a design report.",
        }
    )

    invalid_action_response = fake_ollama_response(
        {
            "action_items": [
                {
                    "task": "send the financial report",
                    "owner": None,
                    "deadline_text": "by Friday",
                    "evidence_quote": (
                        "Please send the financial report by Friday."
                    ),
                }
            ]
        }
    )

    corrected_action_response = fake_ollama_response(
        {
            "action_items": [
                {
                    "task": "send the design report",
                    "owner": None,
                    "deadline_text": "by Monday",
                    "evidence_quote": (
                        "Please send the design report by Monday."
                    ),
                }
            ]
        }
    )

    mock_chat = Mock(
        side_effect=[
            overview_response,
            invalid_action_response,
            corrected_action_response,
        ]
    )
    monkeypatch.setattr(email_ai, "chat", mock_chat)

    message = EmailMessageInput(
        message_id="message-retry-001",
        subject="Weekly update",
        sender="Alex",
        body="Please send the design report by Monday.",
    )

    outcome = email_ai.analyse_email(message)

    assert outcome.validation_status == "Validated"
    assert outcome.validation_reason is None
    assert outcome.attempt_count == 2
    assert outcome.analysis.action_items[0].task == "send the design report"
    assert mock_chat.call_count == 3


def test_attachment_evidence_allows_different_line_wrapping():
    message = EmailMessageInput(
        message_id="message-004",
        subject="Accessibility review",
        sender="Alex",
        body="Please read the attached meeting notes.",
        attachments=[
            ParsedAttachment(
                filename="meeting_notes.txt",
                content=(
                    "Jamie, please prepare the accessibility\n"
                    "checklist by 12 September 2026."
                ),
            )
        ],
    )

    action = ActionItem(
        task="prepare the accessibility checklist",
        owner="Jamie",
        deadline_text="by 12 September 2026",
        evidence_quote=(
            "Jamie, please prepare the accessibility checklist "
            "by 12 September 2026."
        ),
    )

    email_ai.validate_action_evidence([message], [action])


def test_thread_summary_keeps_result_after_three_evidence_failures(monkeypatch):
    thread_response = fake_ollama_response(
        {
            "summary": "The team discussed the homepage design.",
            "latest_decisions": [],
            "outstanding_actions": [
                {
                    "task": "approve the production deployment",
                    "owner": "Jamie",
                    "deadline_text": None,
                    "evidence_quote": (
                        "Jamie, please approve the production deployment."
                    ),
                }
            ],
        }
    )

    mock_chat = Mock(return_value=thread_response)
    monkeypatch.setattr(email_ai, "chat", mock_chat)

    thread = email_ai.EmailThreadInput(
        thread_id="thread-001",
        messages=[
            EmailMessageInput(
                message_id="message-005",
                subject="Homepage design",
                sender="Alex",
                body="The homepage design is ready for review.",
            )
        ],
    )

    outcome = email_ai.summarise_thread(thread)

    assert outcome.validation_status == "NeedsReview"
    assert outcome.validation_reason == "evidence_quote_not_found"
    assert outcome.attempt_count == 3
    assert outcome.analysis.outstanding_actions[0].task == (
        "approve the production deployment"
    )
    assert mock_chat.call_count == 3


def test_thread_summary_recovers_on_second_evidence_attempt(monkeypatch):
    invalid_response = fake_ollama_response(
        {
            "summary": "The team discussed the homepage design.",
            "latest_decisions": [],
            "outstanding_actions": [
                {
                    "task": "approve deployment",
                    "owner": "Jamie",
                    "deadline_text": None,
                    "evidence_quote": "Jamie approved the deployment.",
                }
            ],
        }
    )
    corrected_response = fake_ollama_response(
        {
            "summary": "The team discussed the homepage design.",
            "latest_decisions": [],
            "outstanding_actions": [
                {
                    "task": "review the homepage design",
                    "owner": "Jamie",
                    "deadline_text": None,
                    "evidence_quote": (
                        "Jamie, please review the homepage design."
                    ),
                }
            ],
        }
    )
    mock_chat = Mock(side_effect=[invalid_response, corrected_response])
    monkeypatch.setattr(email_ai, "chat", mock_chat)

    thread = email_ai.EmailThreadInput(
        thread_id="thread-retry-001",
        messages=[
            EmailMessageInput(
                message_id="message-retry-002",
                subject="Homepage design",
                sender="Alex",
                body="Jamie, please review the homepage design.",
            )
        ],
    )

    outcome = email_ai.summarise_thread(thread)

    assert outcome.validation_status == "Validated"
    assert outcome.validation_reason is None
    assert outcome.attempt_count == 2
    assert outcome.analysis.outstanding_actions[0].task == (
        "review the homepage design"
    )
    assert mock_chat.call_count == 2


def test_analysis_records_use_run_id_and_keep_validation_metadata():
    message = EmailMessageInput(
        message_id="message-record-001",
        subject="Status update",
        sender="Alex",
        body="No action is required.",
    )
    email_outcome = email_ai.EmailAnalysisOutcome(
        analysis=email_ai.EmailAnalysis(
            category="Project update",
            summary="No action is required.",
            action_items=[],
        ),
        validation_status="Validated",
        validation_reason=None,
        attempt_count=1,
    )

    email_record = email_ai.create_email_analysis_record(
        message,
        email_outcome,
        run_id=42,
    )

    thread = email_ai.EmailThreadInput(
        thread_id="thread-record-001",
        messages=[message],
    )
    thread_outcome = email_ai.ThreadAnalysisOutcome(
        analysis=email_ai.ThreadSummary(
            summary="No action is required.",
            latest_decisions=[],
            outstanding_actions=[],
        ),
        validation_status="Validated",
        validation_reason=None,
        attempt_count=1,
    )

    thread_record = email_ai.create_thread_analysis_record(
        thread,
        thread_outcome,
        run_id=42,
    )

    assert email_record.run_id == 42
    assert email_record.validation_status == "Validated"
    assert email_record.attempt_count == 1
    assert not hasattr(email_record, "model_name")

    assert thread_record.run_id == 42
    assert thread_record.validation_status == "Validated"
    assert thread_record.attempt_count == 1
    assert not hasattr(thread_record, "model_name")
