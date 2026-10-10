# 6. RAG Method

This section reports the project's local retrieval-augmented generation (RAG) experiment:
whether showing the model a relevant worked example before extraction improves the five target
invoice fields. The implementation was merged in pull request #12 on 8 October 2026 at commit
`d90edab`. The final evidence is the 30-document benchmark in
`evaluation/results_extended_summary.json`.

**The final result is a small, repeatable improvement on a synthetic benchmark.** Baseline
field accuracy was 91.3%; both always-on RAG and selective RAG reached 94.0%. The result supports
the narrower claim that the worked examples helped this test set. It is not evidence that the
same gain will hold for production invoices.

## 6.1 Two different retrieval problems, kept apart

The word retrieval appears twice in this project and means different things each time.

**Retrieval over the mailbox**, described in §5.1.10, finds a vendor's prior messages. It uses
sender and keyword matching to supply business context such as previous correspondence.

**Retrieval over worked invoice examples**, the subject of this section, finds an invoice
example similar to the current document and places that example in the extraction prompt. Its
corpus is five synthetic examples, not the mailbox.

The two paths solve different problems. Mailbox retrieval supplies business context; example
retrieval tries to improve structured field extraction.

## 6.2 What was built

`rag_retrieval.py` loads five anonymised examples from `evaluation/rag_examples.json`, covering
cloud services, hardware, consulting, office supplies and software subscriptions. Each example
contains invoice text and its expected structured output. For a new document, the retriever
scores exact phrases and token overlap, ranks the examples and formats the best match into the
prompt ahead of the current invoice.

The pipeline supports three modes:

- **Baseline:** extract without a worked example.
- **Always-on RAG:** retrieve an example before every extraction.
- **Selective RAG:** run the baseline first, retry with RAG only when the first result is weak,
  and keep the stronger evidence-supported result.

The retriever is deterministic and local. It does not use embeddings or a vector database. At
five examples, a transparent scorer is easier to audit and sufficient to test the PoC question.

The prompt tells the model not to copy values from the example. The evidence gate then checks
the extracted values against the current invoice text. This does not eliminate every model
error, but it prevents a retrieved example from bypassing the same checks applied to baseline
output.

## 6.3 Why the first comparison was inconclusive

The original controlled comparison used three held-out invoices, three repetitions per mode and
five fields per invoice. Repairs were disabled so that the model output, rather than a fallback,
was measured. Both baseline and RAG scored 15/15 fields in every run.

That equality was a ceiling effect. A prompt-label correction had already removed the errors in
those three documents, leaving no headroom for retrieval to improve the score. The result was
useful because it showed that the evaluation set was too easy, but it could not answer whether
RAG improved accuracy. The team therefore added a harder 30-document benchmark.

## 6.4 Extended 30-document benchmark

The extended evaluation contains 30 held-out synthetic text PDFs across six layout families.
Each mode was run three times with `llama3.2:latest`, repairs disabled and five fields scored per
document: vendor name, invoice number, date, total amount and currency. Each run therefore
contains 150 field decisions.

| Metric | Baseline | Always-on RAG | Selective RAG |
|---|---:|---:|---:|
| Mean field accuracy | 91.3% (137/150) | 94.0% (141/150) | 94.0% (141/150) |
| Exact-document accuracy | 73.3% | 80.0% | 80.0% |
| Validation rate | 36.7% | 33.3% | 43.3% |
| Mean latency per document | 5.230 s | 3.781 s | 8.606 s |

The accuracy result was identical in all three repetitions. Compared with baseline, each RAG
mode improved three documents per run, made no document worse and left 27 unchanged. At field
level, the change was:

| Field | Baseline correct | RAG correct | Change |
|---|---:|---:|---:|
| Vendor name | 26/30 | 27/30 | +1 |
| Invoice number | 24/30 | 25/30 | +1 |
| Date | 30/30 | 30/30 | 0 |
| Total amount | 27/30 | 29/30 | +2 |
| Currency | 30/30 | 30/30 | 0 |

The gain is four additional correct fields out of 150, or 2.7 percentage points. That is a
positive PoC result, but its practical size is modest and should not be overstated.

## 6.5 Why selective RAG is the preferred design

Always-on and selective RAG produced the same extraction accuracy, but selective RAG achieved
the highest validation rate: 43.3%, compared with 36.7% for baseline and 33.3% for always-on
RAG. Selective mode retried 19 of 30 documents and adopted the RAG result for 11.

Selective RAG also had the highest measured latency because some documents required two model
calls. The latency figures should be treated as operational observations rather than a clean
speed comparison: local model warm-up and load state affected the runs, which is why always-on
RAG happened to be faster than baseline in this small sample.

The design is still preferable for the PoC because it makes retrieval conditional and records
whether the second answer was adopted. That gives the team an auditable place to tune the retry
rule as more representative invoices become available.

## 6.6 What this section can and cannot claim

The evidence supports four claims:

1. local worked-example retrieval was implemented and integrated into the invoice pipeline;
2. on the 30-document synthetic benchmark, RAG improved mean field accuracy from 91.3% to
   94.0%;
3. the improvement repeated in all three runs and caused no regressions in the scored documents;
4. selective RAG matched always-on accuracy and produced the highest validation rate, at the cost
   of additional latency.

The evidence does not establish production accuracy. The benchmark uses generated, text-based
PDFs rather than a representative sample of real vendor invoices, and it does not test OCR,
scans, handwriting, stamps or the full distribution of invoice layouts. A production decision
would require a larger labelled set of real invoices, error analysis by layout and vendor, and
measurement under controlled hardware and model-temperature settings.
