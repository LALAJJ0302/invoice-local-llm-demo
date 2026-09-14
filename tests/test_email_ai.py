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

    result = email_ai.analyse_email(message)

    assert result.category == "Project update"
    assert result.summary == (
        "The email requests an updated mobile layout."
    )
    assert len(result.action_items) == 1
    assert result.action_items[0].task == "update the mobile layout"
    assert result.action_items[0].deadline_text == "by Friday"

    assert mock_chat.call_count == 2