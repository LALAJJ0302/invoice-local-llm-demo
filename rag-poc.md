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
-> baseline local Ollama structured extraction
-> Pydantic schema validation
-> deterministic evidence and amount validation
-> if weak: retrieve an example and retry local Ollama
-> Pydantic validation and deterministic validation of the retry
-> compare both verdicts and keep only a strict evidence-based improvement
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
| `tests/test_selective_rag.py` | Verifies retry triggers, evidence ranking, tie handling, and per-call RAG control. |

## Usage

Run the normal baseline pipeline:

```bash
./.venv/bin/python main.py
```

Run the pipeline with validation-triggered RAG retry (maximum one example):

```bash
./.venv/bin/python main.py --rag --rag-limit 1
```

Run the controlled baseline evaluation:

```bash
./.venv/bin/python evaluation/run_eval.py \
  --model llama3.2:latest \
  --save evaluation/results_rag_baseline.json
```

Run the controlled always-RAG evaluation:

```bash
./.venv/bin/python evaluation/run_eval.py \
  --model llama3.2:latest \
  --rag \
  --rag-limit 1 \
  --save evaluation/results_rag_enabled.json
```

Run the application-style selective RAG evaluation:

```bash
./.venv/bin/python evaluation/run_eval.py \
  --model llama3.2:latest \
  --selective-rag \
  --rag-limit 1 \
  --save evaluation/results_rag_selective.json
```

Fallback extraction methods are disabled by default in both evaluation commands so the
comparison measures model behaviour rather than regex or filename repairs.

## Accuracy Improvement Design

The production-style pipeline now uses RAG selectively. It first extracts without an
example and calculates the normal deterministic verdict. A retry is triggered when the
result needs review, has an empty field, lacks a verified total, or has unknown/short line-
item reconciliation. The retry receives the most relevant local example.

The model does not decide which answer wins. Python ranks both verdicts by validation
status, amount evidence, line-item reconciliation, validation score, and completeness. The
RAG result replaces the baseline only when that tuple is strictly better; a tie or weaker
result keeps the baseline. This guards against retrieval making an already-correct result
worse and avoids a second model call for complete, fully validated invoices.

`evaluation/run_eval.py --rag` intentionally remains always-RAG. That command measures the
effect of prompt augmentation under a controlled A/B setup, while `main.py --rag` exercises
the safer selective workflow used by the application.

## Selective Workflow Verification

An isolated end-to-end run on 2026-10-08 processed all four mock invoices without touching
the working database. Three complete invoices scored 1.00 on the baseline pass and skipped
RAG. The ambiguous Harbour Review Supplies invoice triggered one RAG retry because its
amount was present but not beside a grand-total label. The retry did not improve the
evidence rank, so the baseline result was retained and the invoice remained in human
review. This is the intended safe behaviour.

A fresh controlled run after the prompt change produced 15/15 correct fields for baseline,
always-RAG, and selective-RAG. Baseline latency averaged 3.796 seconds per document,
always-RAG averaged 4.650 seconds, and selective-RAG averaged 3.745 seconds. Selective-RAG
triggered zero retries because all three baseline results were complete and fully validated,
so it avoided three unnecessary RAG calls. Because the evaluation invoices were already
correct, this confirms no accuracy regression and lower avoidable cost rather than proving
an accuracy gain.

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

The example repository contains synthetic data. Retrieved examples include an instruction that their values must not be copied into the
current document. The extraction prompt also states that every output value must come from
the current document and missing values must not be guessed. Existing deterministic
validation checks both attempts against the current source text, and the baseline is
preserved unless RAG produces stronger evidence.

## Limitations and Future Work

- The evaluation contains only three held-out invoices.
- One run cannot establish model stability.
- The retriever uses exact phrases and token overlap rather than semantic embeddings.
- Incorrect retrieval may reduce output quality.
- Image-only PDFs still require OCR before either baseline or RAG extraction can operate.
- Future evaluation should add more layouts, repeat each configuration, and report mean and
  variance across runs.
