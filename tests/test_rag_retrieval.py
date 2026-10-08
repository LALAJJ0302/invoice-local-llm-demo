"""Tests for deterministic local RAG example retrieval."""

import json
import os
import sys

import pytest


REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)
sys.path.insert(0, REPO_ROOT)

from rag_retrieval import (  # noqa: E402
    format_examples,
    load_examples,
    retrieve_examples,
)


CLOUD_DOCUMENT = """
TAX INVOICE
A cloud infrastructure provider supplied compute capacity,
managed database service, and high performance storage.
"""

HARDWARE_DOCUMENT = """
COMMERCIAL INVOICE
The order contains network hardware, wireless devices,
equipment, quantities, and unit prices.
"""

CONSULTING_DOCUMENT = """
PROFESSIONAL SERVICES INVOICE
Technical consulting and advisory services were billed
using professional service hours.
"""

UNRELATED_DOCUMENT = """
Restaurant meal receipt for lunch and coffee.
"""


def test_repository_contains_five_anonymised_examples():
    examples = load_examples()

    assert len(examples) == 5
    assert len({item["example_id"] for item in examples}) == 5


@pytest.mark.parametrize(
    ("document_text", "expected_id"),
    [
        (CLOUD_DOCUMENT, "cloud-services-001"),
        (HARDWARE_DOCUMENT, "hardware-001"),
        (CONSULTING_DOCUMENT, "consulting-001"),
    ],
)
def test_retrieves_the_relevant_example(
    document_text,
    expected_id,
):
    hits = retrieve_examples(document_text, limit=1)

    assert len(hits) == 1
    assert hits[0]["example_id"] == expected_id
    assert hits[0]["retrieval_score"] > 0
    assert hits[0]["retrieval_reason"]


def test_unrelated_document_returns_no_examples():
    assert retrieve_examples(UNRELATED_DOCUMENT) == []


def test_limit_is_respected():
    document = """
    Cloud software platform subscription with compute,
    storage, managed services, and user licences.
    """

    hits = retrieve_examples(document, limit=2)

    assert len(hits) <= 2
    assert hits == sorted(
        hits,
        key=lambda item: (
            -item["retrieval_score"],
            item["example_id"],
        ),
    )


def test_formatted_context_contains_safety_instruction():
    hits = retrieve_examples(CLOUD_DOCUMENT)
    context = format_examples(hits)

    assert "Never copy" in context
    assert "cloud-services-001" in context
    assert "Expected JSON" in context
    assert "NIS-1042" in context


def test_empty_results_create_empty_context():
    assert format_examples([]) == ""


def test_invalid_limit_is_rejected():
    with pytest.raises(ValueError, match="at least 1"):
        retrieve_examples(CLOUD_DOCUMENT, limit=0)


def test_missing_required_field_is_rejected(tmp_path):
    invalid_repository = {
        "schema_version": 1,
        "examples": [
            {
                "example_id": "invalid-example",
                "document_type": "invoice"
            }
        ]
    }

    path = tmp_path / "invalid_examples.json"
    path.write_text(
        json.dumps(invalid_repository),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing"):
        load_examples(str(path))