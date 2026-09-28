"""Deterministic retrieval for local invoice examples.

This module selects relevant anonymised invoice examples before the
local Ollama model performs structured extraction. It does not call
the language model and does not modify the source document.
"""

import json
import os
import re
from typing import Any


DEFAULT_EXAMPLES_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "evaluation",
    "rag_examples.json",
)

TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{1,}")

NOISE_WORDS = {
    "and",
    "the",
    "for",
    "from",
    "invoice",
    "currency",
    "total",
    "amount",
    "date",
    "supplier",
    "vendor",
}


def tokenise(text: str) -> set[str]:
    """Return meaningful lowercase tokens for deterministic matching."""
    return {
        token
        for token in TOKEN_PATTERN.findall((text or "").lower())
        if token not in NOISE_WORDS
    }


def load_examples(
    examples_path: str = DEFAULT_EXAMPLES_PATH,
) -> list[dict[str, Any]]:
    """Load and validate the local RAG example repository."""
    with open(examples_path, encoding="utf-8") as file:
        payload = json.load(file)

    if payload.get("schema_version") != 1:
        raise ValueError("Unsupported RAG example schema version.")

    examples = payload.get("examples")
    if not isinstance(examples, list):
        raise ValueError("RAG examples must be stored in a list.")

    required_fields = {
        "example_id",
        "document_type",
        "vendor_family",
        "keywords",
        "document_text",
        "expected_output",
    }

    for example in examples:
        missing = required_fields - set(example)
        if missing:
            missing_names = ", ".join(sorted(missing))
            raise ValueError(
                f"RAG example {example.get('example_id', '<unknown>')} "
                f"is missing: {missing_names}"
            )

    return examples


def score_example(
    document_text: str,
    example: dict[str, Any],
) -> tuple[float, list[str]]:
    """Score one example using vendor-family and keyword overlap."""
    source_lower = (document_text or "").lower()
    source_tokens = tokenise(document_text)

    score = 0.0
    reasons: list[str] = []

    family_tokens = tokenise(example["vendor_family"])
    family_matches = sorted(source_tokens & family_tokens)

    if family_matches:
        family_score = 2.0 * len(family_matches)
        score += family_score
        reasons.append(
            "vendor family: " + ", ".join(family_matches)
        )

    for keyword in example["keywords"]:
        keyword_lower = keyword.lower()
        keyword_tokens = tokenise(keyword)

        if keyword_lower in source_lower:
            score += 3.0
            reasons.append(f"phrase: {keyword}")
            continue

        matched_tokens = sorted(source_tokens & keyword_tokens)
        if matched_tokens:
            score += float(len(matched_tokens))
            reasons.append(
                f"keyword tokens: {', '.join(matched_tokens)}"
            )

    return round(score, 3), reasons


def retrieve_examples(
    document_text: str,
    limit: int = 1,
    examples_path: str = DEFAULT_EXAMPLES_PATH,
) -> list[dict[str, Any]]:
    """Return the highest-scoring relevant examples."""
    if limit < 1:
        raise ValueError("Retrieval limit must be at least 1.")

    ranked: list[dict[str, Any]] = []

    for example in load_examples(examples_path):
        score, reasons = score_example(document_text, example)

        if score <= 0:
            continue

        result = dict(example)
        result["retrieval_score"] = score
        result["retrieval_reason"] = reasons
        ranked.append(result)

    ranked.sort(
        key=lambda item: (
            -item["retrieval_score"],
            item["example_id"],
        )
    )

    return ranked[:limit]


def format_examples(examples: list[dict[str, Any]]) -> str:
    """Format retrieved examples for safe insertion into a model prompt."""
    if not examples:
        return ""

    sections = [
        (
            "Relevant anonymised examples follow. Use them only to "
            "understand field mappings and output structure. Never copy "
            "invoice numbers, vendor names, dates, amounts, currencies, "
            "or line items from an example into the current document."
        )
    ]

    for index, example in enumerate(examples, start=1):
        expected_json = json.dumps(
            example["expected_output"],
            indent=2,
            ensure_ascii=False,
        )

        sections.append(
            f"Example {index} ({example['example_id']}):\n"
            f"Example document:\n{example['document_text']}\n\n"
            f"Expected JSON:\n{expected_json}"
        )

    return "\n\n---\n\n".join(sections)