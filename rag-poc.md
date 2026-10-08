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

Revalidated on 2026-10-08 after merging `main` at commit `b9abb46`.

| Metric | Baseline | RAG |
|---|---:|---:|
| Runs | 3 | 3 |
| Evaluated fields per run | 15 | 15 |
| Mean field accuracy | 100.0% | 100.0% |
| Mean validation rate | 100.0% | 100.0% |
| Mean latency | 4.997 seconds | 5.936 seconds |
| Median latency | 5.024 seconds | 5.222 seconds |
| Latency range | 4.922-5.044 seconds | 4.580-8.005 seconds |

RAG matched the baseline accuracy and validation rate in all three post-merge runs. The mean
latency was approximately 0.939 seconds, or 18.8%, higher with RAG. The first RAG run took
8.005 seconds per document, while the next two took 5.222 and 4.580 seconds, so local model
warm-up or machine load materially affected the mean. The median difference was only 0.198
seconds.

The result still does not demonstrate an accuracy improvement because the baseline already
reached 100% on this three-document dataset. It proves that the retrieval and prompt-
augmentation mechanism still works after integration with the latest approval, History,
authentication, task, and dashboard code. A larger held-out dataset with difficult layouts is
required before making a reliability claim.

The end-to-end pipeline also exposed a pre-existing test-design limitation: three UI tests
assume that the local model always omits line items from one specific mock invoice. On the
post-merge run the model correctly extracted those rows, changing the workflow state and
causing those state-dependent assertions to fail. The RAG unit and integration tests passed
13/13, and the remaining suite passed 653 tests. These three failures are not caused by RAG;
the fixtures should be made deterministic instead of depending on a live model outcome.

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
