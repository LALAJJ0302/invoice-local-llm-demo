"""Integrity checks for the 30-document extended evaluation fixture."""

import json
from pathlib import Path

from pypdf import PdfReader

from evaluation.extended_samples_fixture import CASES, ensure_extended_samples

ROOT = Path(__file__).resolve().parents[1]


def test_extended_ground_truth_matches_all_30_cases():
    truth = json.loads((ROOT / "evaluation/extended_ground_truth.json").read_text())["samples"]
    assert len(CASES) == 30
    assert set(truth) == {case[0] for case in CASES}


def test_generated_pdfs_contain_the_frozen_ground_truth(tmp_path):
    truth = json.loads((ROOT / "evaluation/extended_ground_truth.json").read_text())["samples"]
    sample_dir = Path(ensure_extended_samples(tmp_path, quiet=True))
    assert len(list(sample_dir.glob("*.pdf"))) == 30
    for file_name, expected in truth.items():
        text = "\n".join(
            page.extract_text() or "" for page in PdfReader(sample_dir / file_name).pages
        )
        assert expected["vendor_name"] in text
        assert expected["invoice_number"] in text
        assert expected["date"] in text
        assert expected["currency"] in text
        assert f"{expected['total_amount']:,.2f}" in text


def test_extended_vendors_are_not_rag_example_vendors():
    truth = json.loads((ROOT / "evaluation/extended_ground_truth.json").read_text())["samples"]
    examples = json.loads((ROOT / "evaluation/rag_examples.json").read_text())["examples"]
    example_vendors = {
        example["expected_output"]["vendor_name"].casefold() for example in examples
    }
    assert not ({row["vendor_name"].casefold() for row in truth.values()} & example_vendors)
