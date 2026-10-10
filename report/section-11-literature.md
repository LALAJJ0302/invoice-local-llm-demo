# 11. Literature and Environmental Review

This review covers four areas: the research on constrained decoding that this project's central
finding belongs to, the benchmarks that establish how small this evaluation is, existing systems
that perform this task, and the argument for running inference locally.

Every work cited below was verified against its arXiv abstract page, ACL Anthology record or
publisher record on 16 September 2026. Where a claim comes from a vendor blog rather than peer
review it is marked as such and is **not** treated as evidence. Much of what circulates about
local model performance is marketing, and a report about measurement discipline cannot cite
marketing as though it were a result.

## 11.1 Constrained decoding, and the mechanism behind this project's central defect

The defect this project spent most of its evaluation effort on is that a permissive schema allows
a model to omit fields silently. That belongs to an established line of work.

**Grammar-constrained decoding** was set out as a general framework by Geng, Josifoski, Peyrard
and West (2023), who showed that formal grammars can describe the output space for a wide range
of structured tasks and that decoding can be constrained to that space without finetuning.
Willard and Louf (2023) reformulated the problem as indexing over a finite-state machine, making
guided generation cheap enough to apply per token.

**The engine this project actually runs on** is XGrammar (Dong et al., 2024), which Ollama uses.
It divides the vocabulary into context-independent tokens, which can be prechecked and cached,
and context-dependent tokens interpreted at runtime, reporting up to 100x speedup over prior
approaches.

That division matters here because it explains the failure this project measured. Constraint
operates by **masking invalid tokens during sampling**. When a schema's `required` list is empty,
nothing masks the closing brace, so terminating the object early is a valid path through the
grammar. Declaring a field required makes the closing brace invalid until that key is emitted.

Stated that way the 2026-08-26 defect is **not a model failure at all**. The grammar did exactly
what the schema asked for, and §7.4 measures the consequence of asking for the wrong thing.

**JSONSchemaBench** (Geng et al., 2025) is the closest benchmark to this project's question. It
evaluates six constrained-decoding frameworks, XGrammar among them, against 10,000 real-world
JSON schemas along three dimensions: efficiency, **coverage of constraint types**, and output
quality. The second dimension is where this project's defect sits, and it frames the issue
correctly: not whether a schema is enforced, but how different schemas behave under enforcement.

## 11.2 The cost of constraining output, which this project measured independently

Two findings in the literature bear directly on §7.5, and they were found after that measurement
was taken rather than before.

**Tam et al. (2024)** report that format restrictions cause a significant decline in reasoning
ability, and that stricter constraints produce greater degradation. This sits in tension with
§7.4, where moving from a permissive to a required schema took extraction from 3/15 to 15/15.

The tension is resolvable and worth stating, because resolving it sharpens the claim. Tam et al.
measure reasoning tasks, where the model must derive an answer. Extraction asks the model to
locate a value that is already present. A constraint that obstructs derivation can still help
transcription, and this project's evidence is only about the latter.

**Reddy et al. (2026)** is the more important of the two, because it describes this project's own
worst result in general terms. They find that constrained decoding can push models toward
**"locally valid yet semantically incorrect trajectories"**, and propose generating an
unconstrained draft first, then applying constraints to it.

That is precisely the failure documented in §7.5.1 and §9.4. Given a statement of account with no
total, every required-family schema in this project returned `2000.00`, which is the sum of the
two unrelated numbers printed on the page. The value is locally valid: it satisfies the schema, it
is a number, and it is arithmetically derived from the document. It is semantically wrong, and it
is produced **because** the constraint left no legal way to answer "there is no total".

This project did not know of Reddy et al. when that measurement was taken. Finding it afterwards
strengthens the result rather than diminishing it: an independent group working on general
benchmarks describes the same mechanism this project observed on one invoice.

**Schema variability as an experimental variable** is now an active subject. ExStrucTiny (Sibue
et al., 2026) benchmarks schema-variable extraction from document images, and VAREX (Barzelay et
al., 2026) is built on a "Reverse Annotation" principle explicitly to stop fixed-schema benchmarks
rewarding memorisation. The two-by-two in §7.4 is a very small instance of the same move.

## 11.3 Document extraction benchmarks, and how small this evaluation is

Naming the standard benchmarks lets §7 state the limits of its evidence precisely rather than
hoping the question is not asked.

| Benchmark | Content | Labels |
|---|---|---|
| SROIE (Huang et al., 2019) | 1,000 scanned receipt images | Company, date, total, address |
| CORD (Park et al., 2019) | Indonesian receipts | 30 semantic labels in 5 superclasses |
| DocILE, WildReceipt | Also standard in this literature | |

**SROIE's label set is close to this project's.** Company name, date and total are three of the
five fields extracted here, which makes the comparison direct and unflattering: the same task, on
**1,000 real scanned documents**, against this project's **three generated ones**.

The figure quoted is the one the competition paper states. A 626/347 train-test split for the key
information extraction task is widely repeated in secondary sources and is **not** used here,
because it could not be confirmed against the paper itself.

It also isolates the largest functional gap. SROIE is scanned receipts, and this pipeline has no
OCR path, so the documents in the closest matching benchmark are exactly the ones it cannot
process.

**A good score on these benchmarks would not have settled the question either.** Laatiri et al.
(2023) analysed redundancy in public document extraction benchmarks and report **75% template
replication in the SROIE official test set**, against 16% for FUNSD. A model can therefore score
well by recognising a layout it has effectively already seen. This is the same criticism VAREX
makes, and it is the reason §7.2 describes this evaluation's synthetic documents as a limitation
of scale rather than of kind.

Rombach and Fettke (2024) provide a systematic literature review of deep-learning key information
extraction from business documents, and Khang et al. (2025) argue that standard metrics
misreport performance on grouped and hierarchical fields, which is relevant to the treatment of
`line_items` in §3.

## 11.4 Existing systems for this task

**The commercial path this project started on** was SharePoint, Power Automate, Copilot and
Power BI. This is the mainstream enterprise pattern and it works, on the condition that the
organisation controls its tenant. That condition failed here, and §1.5 documents it.

**Published systems of the kind built instead.** OnPrem.LLM (Maiya, 2025) is a privacy-conscious
document intelligence toolkit for running document workflows against local models, and is the
closest published system to this one. Al Hilmi et al. (2026) is closer still in setup, covering
local LLMs, PDFs, layout-aware parsing and reliability specifically.

**Invoice extraction with general-purpose models** is now reported in the literature. Gómez and
Sánchez (2026) extract structured fields from Spanish electricity invoices with general-purpose
models and no task-specific finetuning, reporting F1 of 97.61% with Gemini and 96.11% with
Mistral-small, and concluding that **prompt quality dominates hyperparameter tuning**.

That conclusion is independent support for §7.4.2, which found the prompt to be as sufficient a
cause of this project's 20% result as the schema was. It should be read carefully, though: their
result is on a hosted frontier model and a single invoice type, so it corroborates the direction
of this project's finding rather than its magnitude.

Khanchandani et al. (2025) combine OCR, LLMs and graph analytics for invoice extraction, and
report the same motivating problem this project has deferred: variant layouts, handwriting and
low-quality scans.

**Hallucination in document extraction specifically** is treated by Sarmah et al. (2023), who
combine retrieval-augmented generation with metadata to reduce fabricated values when extracting
from financial reports. Their framing is the same one §9.4 arrives at, that the defence against a
fabricated value has to come from outside the document rather than from the extractor.

## 11.5 Local inference and the privacy argument

The move to a local stack was **forced**, by the tenant permissions documented in §1.5. That is the
honest account, and §12.1 gives it. But the position it landed on has an independent defence, and
the report would undersell itself by offering only the first.

Invoices carry vendor bank details, trading relationships, addresses and pricing, which makes
document processing a data-protection question rather than a convenience one. Maiya (2025) builds
an entire toolkit on that premise, and Knoop and Holtmann (2026) give a practical account of
cost-effective local deployment for small and medium enterprises, which supports the
hardware-constrained framing of §7.6 where six models were shortlisted against a 24 GB machine's
usable memory rather than against a leaderboard.

> **Verify before submission.** §1.1 and this section both need a citable figure for
> document-handling cost, and §11.5 previously carried a cumulative GDPR enforcement total taken
> from a secondary source. The figure has been removed rather than cited loosely. If a primary
> source with an access date is found, it can be restored.

## 11.6 Where this project sits

The reviewed work establishes four things. Constrained decoding is well studied and the mechanism
is documented. Constraining output has measurable costs, including semantically wrong but locally
valid values. Document extraction benchmarks are large, real, and carry known redundancy.
Privacy-conscious local document pipelines exist as a recognised design.

**This project's contribution is correspondingly narrow, and narrower than an earlier draft of
this section claimed.** Reddy et al. (2026) already identify the locally-valid-but-wrong failure
mode in general, so this report does not claim to have found it.

What this project adds is a controlled comparison of **four schema declarations against the same
documents, the same model and the same temperature, scored on two axes at once**: accuracy on
fields that are present, and behaviour on fields that are genuinely absent. The benchmarks in
§11.3 score extraction against documents where the answer exists. JSONSchemaBench scores schema
compliance rather than the truth of what is emitted. Reddy et al. address the problem with a
decoding strategy rather than by characterising how schema declaration changes it.

The specific result offered here is that **schemas with identical accuracy differ sharply in what
they do when the answer is not there**, that the safest of the four still fabricates, and that the
value it fabricates is computed from the page rather than invented, which defeats the obvious
check. Whether that holds beyond one model and eight documents is not established by this work,
and §7.2 says so.
