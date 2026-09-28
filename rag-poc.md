# Local RAG Proof of Concept

## Purpose

This Proof of Concept adds retrieval-assisted few-shot prompting to the local invoice
extraction pipeline. Before Ollama extracts structured fields, the system can retrieve one
relevant anonymised example and add it to the prompt. The model, example repository,
retrieval logic, validation, and database remain local.

## Scope

The first version deliberately uses deterministic metadata and keyword matching rather than
embeddings or a vector database. Five synthetic examples represent cloud services, hardware,
consulting, office supplies, and software subscriptions. Evaluation documents are kept
separate from these examples.

The feature is optional. Running `main.py` without `--rag` preserves the original baseline
behaviour.

## Processing Flow

```text
Invoice PDF
-> pypdf text extraction
-> deterministic example retrieval
-> prompt augmentation
-> local Ollama structured extraction
-> Pydantic schema validation
-> deterministic evidence and amount validation
-> SQLite storage
-> Streamlit human review
```

## Files

| File | Purpose |
|---|---|
| `evaluation/rag_examples.json` | Five anonymised invoice examples and expected outputs. |
| `rag_retrieval.py` | Loads, scores, ranks, and formats relevant examples. |
| `main.py` | Adds optional RAG context before the Ollama request. |
| `evaluation/run_eval.py` | Runs controlled baseline and RAG comparisons. |
| `tests/test_rag_retrieval.py` | Verifies selection, ordering, validation, and safe formatting. |
| `tests/test_rag_integration.py` | Verifies that retrieved context reaches the Ollama prompt. |

## Usage

Run the normal baseline pipeline:

```bash
./.venv/bin/python main.py
```

Run the pipeline with one retrieved example per document:

```bash
./.venv/bin/python main.py --rag --rag-limit 1
```

Run the controlled baseline evaluation:

```bash
./.venv/bin/python evaluation/run_eval.py \
  --model llama3.2:latest \
  --save evaluation/results_rag_baseline.json
```

Run the controlled RAG evaluation:

```bash
./.venv/bin/python evaluation/run_eval.py \
  --model llama3.2:latest \
  --rag \
  --rag-limit 1 \
  --save evaluation/results_rag_enabled.json
```

Fallback extraction methods are disabled by default in both evaluation commands so the
comparison measures model behaviour rather than regex or filename repairs.

## Three Run Evaluation Result

| Metric | Baseline | RAG |
|---|---:|---:|
| Runs | 3 | 3 |
| Evaluated fields per run | 15 | 15 |
| Mean field accuracy | 100.0% | 100.0% |
| Mean validation rate | 100.0% | 100.0% |
| Mean latency | 4.088 seconds | 4.313 seconds |
| Latency range | 3.957-4.236 seconds | 3.623-4.688 seconds |

RAG matched the baseline accuracy in all three controlled runs and added an average of
approximately 0.225 seconds, or 5.5%, to processing time. It selected the cloud-services
example for the cloud invoice, the hardware example for the hardware invoice, and the
consulting example for the consulting invoice.

The result does not demonstrate an accuracy improvement because the baseline already reached
100% on this small dataset. An earlier exploratory baseline run scored 10/15 before the
controlled three-run comparison, showing that a larger held-out dataset is still necessary
before making a general reliability claim.

## Measurement Definitions

- Retrieval score is a deterministic ranking score. It is not a probability or percentage.
- Field accuracy compares extraction output with hand-transcribed ground truth.
- Validation score is calculated by Python evidence and business rules. It is not model
  confidence.
- Latency measures one local model request and can be affected by model warm-up and machine
  load.

## Privacy and Safety

The example repository contains synthetic data. Retrieved examples include an instruction
that their values must not be copied into the current document. Existing deterministic
validation still checks the model output against the current source text.

## Limitations and Future Work

- The evaluation contains only three held-out invoices.
- One run cannot establish model stability.
- The retriever uses exact phrases and token overlap rather than semantic embeddings.
- Incorrect retrieval may reduce output quality.
- Image-only PDFs still require OCR before either baseline or RAG extraction can operate.
- Future evaluation should add more layouts, repeat each configuration, and report mean and
  variance across runs.
