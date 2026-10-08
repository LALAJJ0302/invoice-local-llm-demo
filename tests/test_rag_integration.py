"""Integration tests for RAG prompt augmentation in DocumentExtractor."""

import json
import os
import sys


REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)
sys.path.insert(0, REPO_ROOT)

import main  # noqa: E402
from main import DocumentExtractor  # noqa: E402


CURRENT_DOCUMENT = """
TAX INVOICE
Vendor: Current Cloud Company
Invoice Number: CURRENT-001
Date of Issue: 2026-09-28
Currency: USD
Cloud Compute 1 USD 100.00
Grand Total USD 100.00
"""


def successful_response():
    return {
        "message": {
            "content": json.dumps({
                "invoice_number": "CURRENT-001",
                "vendor_name": "Current Cloud Company",
                "date": "2026-09-28",
                "total_amount": 100.0,
                "currency": "USD",
                "items": [],
            })
        }
    }


def test_baseline_does_not_add_retrieved_examples(monkeypatch):
    captured = {}

    def fake_chat(**kwargs):
        captured.update(kwargs)
        return successful_response()

    monkeypatch.setattr(main.ollama, "chat", fake_chat)

    extractor = DocumentExtractor(use_rag=False)
    result = extractor.extract_invoice_data(
        CURRENT_DOCUMENT,
        "current.pdf",
    )

    prompt = captured["messages"][0]["content"]
    assert result.invoice_number == "CURRENT-001"
    assert extractor.last_retrieval == []
    assert "cloud-services-001" not in prompt


def test_rag_adds_the_retrieved_example_to_the_prompt(monkeypatch):
    captured = {}

    def fake_chat(**kwargs):
        captured.update(kwargs)
        return successful_response()

    monkeypatch.setattr(main.ollama, "chat", fake_chat)

    extractor = DocumentExtractor(use_rag=True)
    result = extractor.extract_invoice_data(
        CURRENT_DOCUMENT,
        "current.pdf",
    )

    prompt = captured["messages"][0]["content"]
    assert result.invoice_number == "CURRENT-001"
    assert extractor.last_retrieval[0]["example_id"] == "cloud-services-001"
    assert "cloud-services-001" in prompt
    assert "Never copy" in prompt
    assert CURRENT_DOCUMENT.strip() in prompt


def test_rag_values_are_not_used_as_the_current_result(monkeypatch):
    def fake_chat(**kwargs):
        return successful_response()

    monkeypatch.setattr(main.ollama, "chat", fake_chat)

    extractor = DocumentExtractor(use_rag=True)
    result = extractor.extract_invoice_data(
        CURRENT_DOCUMENT,
        "current.pdf",
    )

    assert result.invoice_number == "CURRENT-001"
    assert result.invoice_number != "NIS-1042"
    assert result.vendor_name == "Current Cloud Company"
    assert result.total_amount == 100.0
