"""Summarise the three-run extended baseline/RAG/selective-RAG experiment."""

import json
import statistics
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
MODES = {
    "baseline": "baseline",
    "always_rag": "rag",
    "selective_rag": "selective",
}


def load_runs(mode):
    stem = MODES[mode]
    return [
        json.loads((EVAL_DIR / f"results_extended_{stem}_run{run}.json").read_text())
        for run in range(1, 4)
    ]


def percent(numerator, denominator):
    return round(numerator / denominator * 100, 3)


def summarise_mode(runs):
    field_accuracy = [percent(r["overall"]["correct"], r["overall"]["total"]) for r in runs]
    exact_match = [percent(r["documents_correct"], r["documents"]) for r in runs]
    validation = [percent(r["validated"], r["documents"]) for r in runs]
    latency = [r["average_latency_seconds"] for r in runs]
    return {
        "runs": len(runs),
        "documents_per_run": runs[0]["documents"],
        "fields_per_run": runs[0]["overall"]["total"],
        "field_accuracy_percent": field_accuracy,
        "mean_field_accuracy_percent": round(statistics.mean(field_accuracy), 3),
        "document_exact_match_percent": exact_match,
        "mean_document_exact_match_percent": round(statistics.mean(exact_match), 3),
        "validation_rate_percent": validation,
        "mean_validation_rate_percent": round(statistics.mean(validation), 3),
        "latency_seconds": latency,
        "mean_latency_seconds": round(statistics.mean(latency), 3),
        "latency_range_seconds": [min(latency), max(latency)],
        "selective_retries": [r.get("selective_retries", 0) for r in runs],
        "selective_adoptions": [r.get("selective_adoptions", 0) for r in runs],
        "per_field_correct": {
            field: [r["per_field"][field]["correct"] for r in runs]
            for field in runs[0]["per_field"]
        },
    }


def compare_documents(baseline_runs, candidate_runs):
    comparisons = []
    for baseline, candidate in zip(baseline_runs, candidate_runs):
        base_rows = {row["file"]: row for row in baseline["rows"]}
        candidate_rows = {row["file"]: row for row in candidate["rows"]}
        better = worse = same = 0
        for file_name, base_row in base_rows.items():
            base_correct = sum(v["correct"] for v in base_row["fields"].values())
            candidate_correct = sum(
                v["correct"] for v in candidate_rows[file_name]["fields"].values()
            )
            if candidate_correct > base_correct:
                better += 1
            elif candidate_correct < base_correct:
                worse += 1
            else:
                same += 1
        comparisons.append({"better": better, "worse": worse, "same": same})
    return comparisons


def main():
    runs = {mode: load_runs(mode) for mode in MODES}
    payload = {
        "dataset": "extended",
        "documents_per_run": 30,
        "runs_per_mode": 3,
        "fallbacks": "disabled",
        "model": runs["baseline"][0]["model"],
        "modes": {mode: summarise_mode(values) for mode, values in runs.items()},
        "always_rag_vs_baseline_documents": compare_documents(
            runs["baseline"], runs["always_rag"]
        ),
        "selective_rag_vs_baseline_documents": compare_documents(
            runs["baseline"], runs["selective_rag"]
        ),
        "limitations": [
            "Synthetic text-based PDFs; this does not evaluate OCR.",
            "Thirty documents improve evidence but do not represent every production vendor.",
            "Latency depends on local model warm-up and machine load.",
        ],
    }
    output = EVAL_DIR / "results_extended_summary.json"
    output.write_text(json.dumps(payload, indent=2) + "\n")
    print(output)


if __name__ == "__main__":
    main()
