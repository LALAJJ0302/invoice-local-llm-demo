# 2. Literature and Environmental Review

This review covers four areas: the commercial and open-source systems that already do this work,
the research on constrained decoding that the project's central finding belongs to, the
benchmarks that establish how small this evaluation is, and the privacy argument for running
inference locally.

A note on what is cited. Several widely circulated figures about local model performance come
from vendor blogs rather than peer review. Where those claims appear below they are marked as
such and are **not** treated as evidence, only as claims worth testing. Half of what is written
about local models is marketing, and a report on measurement discipline cannot cite marketing as
though it were a result.

## 2.1 Existing solutions

**The commercial path this project started on.** The original design used SharePoint for storage,
Power Automate for orchestration, Microsoft Copilot for extraction and Power BI for reporting.
This is the mainstream enterprise pattern and it works, on the condition that the organisation
controls its own tenant. That condition is what failed here, and §3 documents it.

**The closest published system to what was built instead** is OnPrem.LLM (arXiv:2505.07672), a
privacy-conscious document intelligence toolkit for running document workflows against local
models. It matters to this report for two reasons. It establishes that a local-first document
pipeline is a recognised design rather than an improvisation forced by circumstance, and it
occupies the same position this project's system does, which allows the contribution here to be
stated as a narrow one: not a new architecture, but a controlled measurement of how schema
specification changes extraction behaviour inside such an architecture.

> **Verify before submission.** Read OnPrem.LLM in full and confirm whether it addresses schema
> specification at all. If it does, this project's finding must be positioned against theirs
> rather than merely beside it. If it does not, that absence is itself worth one sentence, since
> it makes the gap concrete.

## 2.2 Constrained decoding, and the mechanism behind the central finding

The defect that this project spent most of its evaluation effort on is that a permissive schema
allows a language model to omit fields silently. That finding was, until this review, supported
only by this project's own measurements.

**JSONSchemaBench** (Geng et al., 2025, arXiv:2501.10868) places it inside a documented research
area. The benchmark evaluates six constrained-decoding frameworks against 10,000 real-world JSON
schemas along three dimensions: efficiency, **coverage of constraint types**, and output quality.

Two things make it the right anchor rather than an adjacent citation. It evaluates **XGrammar**,
which is the engine Ollama uses, so it concerns this project's exact stack. And its second
dimension, coverage of constraint types, is precisely where this project's defect sits: the
question is not whether a schema is enforced, but how different schemas behave under enforcement.

**The mechanism, which this project's own earlier explanation had wrong.** Ollama accepts a JSON
schema through its `format` parameter and applies constrained decoding at inference time. The
constraint operates at token level: the grammar determines which tokens are valid next, and
invalid tokens are masked during sampling.

This project's notes had described the decoder as "legally omitting fields" when the `required`
list is empty, which describes the outcome without the cause. The cause is that **nothing masks
the closing brace**, so terminating the object early is a valid path through the grammar. A
required field changes this by making the closing brace invalid until that key has been emitted.
Stated that way, the 2026-08-26 defect is not a model failure at all. It is the grammar doing
exactly what the schema asked for.

> **Verify before submission.** The token-masking description comes from Ollama's own
> documentation, which is the authoritative vendor but still a vendor. Confirm the mechanism
> against the XGrammar treatment in JSONSchemaBench §2 before stating it as fact in §4.

**Schema-variability as a subject in its own right.** Two recent benchmarks treat the schema as
the experimental variable rather than as fixed apparatus: ExStrucTiny (arXiv:2602.12203) on
schema-variable structured extraction from document images, and VAREX (arXiv:2603.15118), which
is built on a "Reverse Annotation" principle explicitly to address fixed-schema benchmarks
rewarding memorisation. The two-by-two in §5.4 is a very small instance of the same experimental
move.

> **Verify before submission.** Both were identified by search and read only at abstract level.
> Read them, and in particular check whether either reports the accuracy-against-invention trade
> measured in §5.5. If one does, this project's result is a replication and should say so. If
> none does, that is the stronger claim and also needs saying.

## 2.3 Document extraction benchmarks, and how small this evaluation is

Naming the standard benchmarks is what allows §5 to state the limits of its own evidence
precisely, rather than hoping the question is not asked.

| Benchmark | Content | Labels |
|---|---|---|
| SROIE | 626 train / 347 test scanned receipts | Company name, date, total amount, address |
| CORD | 1,000 receipts | 30 hierarchical entities under menu / subtotal / total |
| WildReceipt, DocILE | Also standard in this literature | |

**SROIE's label set is close to this project's.** Company name, date and total are three of the
five fields extracted here. That similarity makes the comparison direct and unflattering in a way
worth stating plainly: the same task, on **973 real scanned documents**, against this project's
**three generated ones**.

It also isolates the largest functional gap. SROIE is scanned receipts, and this pipeline has no
OCR path at all, so the documents in the closest matching benchmark are exactly the documents it
cannot process.

Two further papers are relevant to the limitations rather than the method. *Tabular PDF
Information Extraction with Local LLMs and Layout-Aware Parsing* (arXiv:2604.00003) is the closest
published work by setup, covering local models, PDFs, tables and reliability. *Information
Redundancy and Biases in Public Document Information Extraction Benchmarks* (arXiv:2304.14936)
argues that these benchmarks carry redundancy and bias, which is relevant because it means a good
score on SROIE would not have settled the question either.

> **Verify before submission.** Benchmark sizes above are from search results and must be checked
> against the source papers. A wrong figure here would be the exact error §6 lesson 2 describes,
> and it would sit in the section arguing for measurement discipline.

## 2.4 Local inference and the privacy argument

The move to a local stack was **forced**, by the tenant permissions documented in §3. That is the
honest account and §6.1 gives it. But the position it landed on has an independent defence, and
the report would undersell itself by offering only the first.

Invoices carry vendor bank details, trading relationships, addresses and pricing. These are
exactly the categories that make document processing a data-protection question rather than a
convenience question. Cumulative GDPR enforcement has exceeded €5.88 billion since 2018, which
makes "these documents should not leave the machine" a concrete position rather than an abstract
preference.

*Private LLM Inference on Consumer GPUs* (arXiv:2601.09527) supports the hardware-constrained
framing used in §5.6, where six models were shortlisted against the usable memory of a 24 GB
machine rather than against a leaderboard.

> **Verify before submission.** The GDPR figure needs a primary source, and it dates quickly.
> Either cite the enforcement tracker directly with an access date, or remove it. It is currently
> the least well-sourced number in this report.

## 2.5 Claims in circulation that this report does not cite

Two figures were found during the review that would have been convenient and are not used.

A 2026 vendor comparison reports Qwen-3.5 27B as incompatible with constrained output, returning
empty JSON with 100% parsing failures. If true this is this project's own defect reproduced
independently on another model, and it would support a claim that cannot otherwise be made: that
constrained-decoding compatibility varies by model and **fails silently by returning nothing
rather than erroring**.

The same source reports Gemma-4 improving from F1 0.039 to 0.702 with prompt optimisation, which
is the same shape as the prompt effect measured in §5.4.

**Both are blog sources and neither is cited as evidence.** They are recorded here because they
are testable, and because §5.6 tests the first one directly: every candidate model was measured
for valid-JSON rate and `required` compliance on this machine before any accuracy figure was
recorded. All five measured models handled constrained decoding correctly, so this project's
evidence does **not** reproduce the Qwen-3.5 report.

Listing a convenient claim and declining to use it is part of the argument of this report. A
figure that supports the conclusion is the one most in need of a source.

## 2.6 Where this project sits

The literature establishes that constrained decoding is well studied, that schema variability is
an active subject, that benchmarks for document extraction are large and real, and that
privacy-conscious local document pipelines exist as a recognised design.

What this project adds is narrow and, within its limits, not something the reviewed work reports:
a controlled comparison of four schema declarations against the same documents and model,
measuring not only accuracy but **what the system does when a field is genuinely absent**. The
benchmarks above score extraction against documents where the answer exists. The question of what
a model returns when the answer does not exist is the one that determines whether a wrong value
reaches a database, and §5.5 shows that schemas with identical accuracy differ sharply on it.

> **Verify before submission.** This gap statement is the load-bearing claim of §2 and rests on
> not having found contrary work. Before submitting, search specifically for prior work measuring
> hallucination or fabrication rates under constrained decoding on absent fields. If it exists,
> this paragraph becomes a positioning statement rather than a gap claim. Either is defensible;
> claiming a gap that does not exist is not.
