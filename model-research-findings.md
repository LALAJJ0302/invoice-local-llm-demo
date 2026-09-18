# Research: Model Selection and the Literature Behind the Schema Finding

**Neo, 2026-09-08.** Item 1 of JJ's assignment, plus the literature the report's §2 needs.

**Provenance note.** Everything below came from searching on 2026-09-08. Every Ollama model name
was checked against `registry.ollama.ai` before being listed. Sources are marked **peer-reviewed**
or **blog / vendor**, because half of what is written about local models is marketing and the
report cannot cite it as though it were evidence.

---

## 1. The literature that supports our schema finding

This is the biggest result of the search. Our finding was previously ours alone, with nothing
behind it. It now sits inside a named research area.

### JSONSchemaBench (peer-reviewed, the anchor citation)

Geng, S., Cooper, H., Moskal, M., Jenkins, S., Berman, J., Ranchin, N., West, R., Horvitz, E., &
Nori, H. (2025). *JSONSchemaBench: A Rigorous Benchmark of Structured Outputs for Language
Models.* arXiv:2501.10868.

Why it matters to us:

- It evaluates six constrained-decoding frameworks including **XGrammar**, which is **what Ollama
  uses**. So it is about our exact stack, not a neighbouring one.
- It measures three dimensions: efficiency, **coverage of constraint types**, and output quality.
  Coverage is the dimension our defect lives in.
- 10,000 real-world JSON schemas. Its whole premise is that **schema design is a variable that
  changes outcomes**, which is the claim our 2x2 makes on n=3.
- Author list includes Eric Horvitz and Robert West, so it is citable without hedging.

**How to use it in the report:** our controlled comparison stops being an anecdote and becomes a
single-case instance of a documented phenomenon. That is worth real marks in §2, which is currently
empty.

### How the mechanism actually works, for §4

Ollama exposes a `format` field taking a JSON schema, and applies constrained decoding during
inference using XGrammar. It operates at token level: the grammar determines which tokens are
valid next, and **invalid tokens are masked during sampling**.

That is the missing sentence in our own explanation. We wrote that the decoder "legally omits
fields" when `required` is empty. The mechanism is that nothing masks the closing brace, so
stopping early is a valid path through the grammar. Available since Ollama 0.3.0.

Source: [Ollama structured outputs documentation](https://docs.ollama.com/capabilities/structured-outputs) (vendor, but it is the authoritative vendor).

### Independent confirmation of our exact failure mode

**blog, treat with care, but worth chasing:** a 2026 comparison reports Qwen-3.5 27B as
incompatible with constrained output, **returning empty JSON content with 100% parsing failures.**

That is our defect, on a different model, found by someone else. It supports a claim we could not
otherwise make: **constrained-decoding compatibility varies by model and can fail silently by
returning nothing rather than erroring.** Worth verifying ourselves rather than citing a blog.

### And a caution about our own prompt result

The same source reports Gemma-4 scoring **F1 0.039 raw, rising to 0.702 with prompt optimisation.**

That is the same shape as our 3/15 to 10/15 prompt result. It supports our revised position that
**both prompt and schema matter**, and it undercuts the older, stronger line in our notes that
prompt engineering is not the issue. Good that we already softened it.

---

## 2. Document extraction benchmarks, for §2 and for our limitations

Our evaluation is n=3, synthetic, self-generated. Naming the real benchmarks lets us say precisely
how small that is, which is stronger than hoping nobody asks.

| Benchmark | What it is |
|---|---|
| **SROIE** | Scanned receipts, 626 train / 347 test. Labels: **Company Name, Date, Total Amount, Address** |
| **CORD** | 1,000 Indonesian receipts, 30 hierarchical entities under menu / subtotal / total |
| **WildReceipt**, **DocILE** | Also standard in this literature |

**SROIE's label set is almost exactly ours.** Company name, date, total. That is a direct
comparison to make in §5: the same task, four fields against our five, on 973 real scanned
documents against our 3 generated ones.

Papers worth reading before writing §2:

- **ExStrucTiny: A Benchmark for Schema-Variable Structured Information Extraction from Document
  Images** (arXiv:2602.12203). Schema variability is our finding's subject.
- **VAREX: A Benchmark for Multi-Modal Structured Extraction from Documents**
  (arXiv:2603.15118). Built on a "Reverse Annotation" principle, explicitly to address
  **fixed-schema benchmarks rewarding memorisation**.
- **Tabular PDF Information Extraction with Local LLMs and Layout-Aware Parsing: A Reliability
  Evaluation** (arXiv:2604.00003). Local models, PDFs, tables, reliability. Closest paper to what
  we built.
- **Information Redundancy and Biases in Public Document Information Extraction Benchmarks**
  (arXiv:2304.14936). Useful for our own limitations section.

---

## 3. The privacy argument for local inference, for §2 and §6

Our pivot to local is currently justified as "we got blocked by UTS tenant permissions". That is
true and it is weak. There is a principled argument available.

- **OnPrem.LLM: A Privacy-Conscious Document Intelligence Toolkit** (arXiv:2505.07672).
  **The closest existing solution to what we built**, and §2 currently lists no existing solutions
  beyond the Microsoft stack we tried. This belongs there.
- **Private LLM Inference on Consumer GPUs: A Practical Guide for Cost-Effective Local Deployment
  in SMEs** (arXiv:2601.09527). Supports the hardware-constrained framing of our model selection.
- GDPR enforcement exceeds **€5.88 billion cumulative since 2018**. Invoices carry vendor bank
  details, addresses and trading relationships, so the argument that they should not leave the
  machine is concrete rather than abstract.

**Reframing for the report:** the pivot was forced by circumstance, and it landed on a position
with an independent defence. Say both. Saying only the second would be dishonest, and saying only
the first undersells it.

---

## 4. Revised model shortlist

Mistral dropped per Neo. Every name verified against the Ollama registry today.

Machine: **Apple M4, 10 cores, 24 GB unified, 187 GB free.** Unified memory is shared with the OS
and Streamlit, so the usable budget is roughly **16 to 18 GB**, not 24.

| Model | Family | Approx size | Role in the comparison |
|---|---|---|---|
| `llama3.2:3b` | Meta | 2.0 GB | Baseline, already pulled. The thing to beat |
| `gemma3:4b` | Google | ~3.3 GB | Different family, near-identical size. Isolates family from size |
| `qwen2.5:7b` | Alibaba | ~4.7 GB | Repeatedly reported strongest at structured output |
| `llama3.1:8b` | Meta | ~4.7 GB | Same family as baseline, larger. Isolates size within family |
| `phi4:14b` | Microsoft | ~9 GB | At the practical ceiling |
| `qwen3:14b` | Alibaba | ~9 GB | Newer generation at the ceiling |

Also verified as existing, if we want swaps: `qwen3:8b`, `gemma3:12b`, `granite3.3:8b` (IBM),
`olmo2:7b` (fully open training data, a nice angle), `phi3.5:3.8b`, `deepseek-r1:8b`.

**`qwen3.6:27b` and `qwen3.5:27b` exist but do not fit.** At roughly 17 to 20 GB they exceed the
usable budget once the OS is counted. `gemma4:12b` exists; `gemma4:27b` does not resolve.

**The design is deliberate.** Two axes, so the comparison answers two questions rather than
producing a ranking:

- **Size within a family:** `llama3.2:3b` against `llama3.1:8b`
- **Family at fixed size:** `gemma3:4b` against `llama3.2:3b`

A ranking of six models is a table. Two controlled axes is a finding.

### The compatibility test that matters

Not size, not benchmark scores. **Does the model honour Ollama's `format` parameter, and does it
respect `required` when the schema declares it.**

The Qwen-3.5 report above says that some models return empty JSON under constrained decoding.
**That is indistinguishable from our own defect unless tested separately**, so each candidate is
checked for valid-JSON rate and `required` compliance before any accuracy number is recorded.

---

## 5. What this changes

| Report section | Before | After |
|---|---|---|
| §2 Literature | **Empty.** The thinnest section | Anchor paper, 4 benchmarks, 4 further papers, an existing solution |
| §4 Design | "The decoder legally omits fields" | Token-level masking via XGrammar, named and explained |
| §5 Evaluation | n=3, no context | Positioned against SROIE's 973 real documents |
| §6 Discussion | "Prompt is not the issue" | Independent evidence that both prompt and schema matter |
| Pivot rationale | "We were blocked" | Blocked, and landed on a defensible position |

## 6. What still has to be verified ourselves

Nothing above is a measurement of **our** system. Specifically:

- The Qwen-3.5 empty-JSON report is a blog. **Reproduce it or drop it**, do not cite it.
- The Gemma-4 F1 figures are a blog. Same rule.
- Every model in §4 needs its own valid-JSON rate, `required` compliance, resident memory and
  seconds per document measured on this machine.

The rule that got us here holds: a number in a document is not a measurement.

---

## Sources

Peer-reviewed or preprint:

- [JSONSchemaBench (arXiv:2501.10868)](https://arxiv.org/abs/2501.10868)
- [ExStrucTiny (arXiv:2602.12203)](https://arxiv.org/pdf/2602.12203)
- [VAREX (arXiv:2603.15118)](https://arxiv.org/html/2603.15118v1)
- [Tabular PDF Extraction with Local LLMs (arXiv:2604.00003)](https://arxiv.org/pdf/2604.00003)
- [Information Redundancy and Biases in DIE Benchmarks (arXiv:2304.14936)](https://arxiv.org/pdf/2304.14936)
- [OnPrem.LLM (arXiv:2505.07672)](https://arxiv.org/pdf/2505.07672)
- [Private LLM Inference on Consumer GPUs (arXiv:2601.09527)](https://arxiv.org/pdf/2601.09527)
- [KIEval (arXiv:2503.05488)](https://arxiv.org/pdf/2503.05488)

Vendor documentation:

- [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs)

Blog and vendor, **not citable as evidence**, listed so the claims can be traced and tested:

- [Best Local LLMs for Structured Output](https://insiderllm.com/guides/structured-output-local-llms/)
- [Invoice OCR Benchmark: LLMs vs OCRs](https://aimultiple.com/invoice-ocr)
- [SLM Enterprise Edge AI 2026](https://www.meta-intelligence.tech/en/insight-slm-enterprise)
