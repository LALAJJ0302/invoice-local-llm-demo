"""Tests for evidence-triggered RAG retry and safe result selection."""

import json

import main
from main import (
    DocumentExtractor,
    rag_result_is_better,
    should_retry_with_rag,
    validation_quality,
)


def verdict(
    *,
    score=1.0,
    status="Validated",
    amount_state="verified",
    reconciliation="exact",
    empty=None,
):
    return {
        "score": score,
        "status": status,
        "amount_state": amount_state,
        "reconciliation": reconciliation,
        "empty": [] if empty is None else empty,
        "reason": "test reason",
    }


def test_complete_validated_result_does_not_need_rag():
    assert not should_retry_with_rag(verdict())


def test_missing_or_weak_evidence_triggers_rag():
    assert should_retry_with_rag(verdict(empty=["items"], score=0.9))
    assert should_retry_with_rag(verdict(status="NeedsReview", score=0.87))
    assert should_retry_with_rag(verdict(amount_state="present", score=0.87))
    assert should_retry_with_rag(verdict(reconciliation="unknown", score=0.95))


def test_stronger_rag_result_is_adopted():
    baseline = verdict(score=0.85, empty=["items"], reconciliation="unknown")
    rag = verdict(score=1.0)
    assert validation_quality(rag) > validation_quality(baseline)
    assert rag_result_is_better(baseline, rag)


def test_equal_or_weaker_rag_result_keeps_baseline():
    baseline = verdict(score=0.9, empty=["items"], reconciliation="plausible")
    equal = dict(baseline)
    weaker = verdict(status="NeedsReview", score=0.95, amount_state="present")
    assert not rag_result_is_better(baseline, equal)
    assert not rag_result_is_better(baseline, weaker)


def test_per_call_override_can_force_baseline_on_rag_extractor(monkeypatch):
    captured = {}

    def fake_chat(**kwargs):
        captured.update(kwargs)
        return {"message": {"content": json.dumps({
            "invoice_number": "CURRENT-001",
            "vendor_name": "Current Cloud Company",
            "date": "2026-09-28",
            "total_amount": 100.0,
            "currency": "USD",
            "items": [],
        })}}

    monkeypatch.setattr(main.ollama, "chat", fake_chat)
    extractor = DocumentExtractor(use_rag=True)
    extractor.extract_invoice_data(
        "Vendor: Current Cloud Company\nGrand Total USD 100.00",
        "current.pdf",
        use_rag=False,
    )

    assert extractor.last_retrieval == []
    assert "cloud-services-001" not in captured["messages"][0]["content"]
