# 7. RAG Method

This section reports a retrieval-augmented generation experiment: whether showing the model a
relevant worked example before it extracts improves what it extracts. It was built by JJ and
sits in pull request #12, which is open and not merged at the time of writing. The figures
below are read from the six result files in that branch rather than re-derived, and the branch
they come from is named wherever a number is quoted, because it is not the branch the rest of
this report was measured on.

**The headline result is null, and the reason it is null is the finding.** The comparison was
run against a baseline that had already reached the ceiling, so it could not have shown an
improvement whatever retrieval did. That is set out in §7.4 rather than buried under the table.

## 7.1 Two different retrieval problems, kept apart

The word retrieval appears twice in this project and means different things each time, so they
are separated before either is discussed.

**Retrieval over the mailbox**, described in §6.1.10, finds a vendor's prior messages. It has
two implemented strategies, `sender` and `keyword`, and its measured result is that keyword
scores 1.00 on threads and 0.00 on vendors.

**Retrieval over worked examples**, the subject of this section, finds an invoice similar to the
one being extracted and puts it in the prompt as a demonstration. The corpus it searches is five
synthetic examples written for the purpose, not the mailbox.

They share a module name and nothing else. A reader who conflates them will attribute the null
result below to the mailbox work, which it has no bearing on.

## 7.2 What was built

`rag_retrieval.py` loads five anonymised examples from `evaluation/rag_examples.json`, one each
for cloud services, hardware, consulting, office supplies and software subscriptions. Each
carries an invoice text and its expected structured output. Given a new document, the module
scores the examples on exact phrases and token overlap, ranks them, and formats the top one into
the prompt ahead of the document being extracted.

The path is optional. `main.py` without `--rag` is the behaviour every other measurement in this
report was taken against; `main.py --rag --rag-limit 1` adds one retrieved example.

**No embeddings and no vector database**, for the reason §6.1.10 gives for the other retrieval
problem: whether embeddings help at this corpus size is a measurement nobody has taken, and
implementing them first would mean never taking it. Five examples is also a corpus at which a
deterministic scorer is defensible on its own terms.

The retrieved example is accompanied by an instruction that its values must not be copied into
the current document, and the existing evidence check still validates the model's output against
the current source text. Retrieval therefore cannot introduce a value that the gate would not
otherwise catch, which matters because a demonstration containing plausible invoice numbers is
exactly the kind of context that invites copying.

## 7.3 How the comparison was run

Three runs of each arm, three held-out documents per run, five fields per document, so 15
field-values per run and 45 per arm. Held out means the three evaluation documents are not among
the five examples. **Repairs were disabled in both arms**, the same switch §8.1 describes, so
the comparison measures what the model does rather than what the regex fallback does for it.

| Metric | Baseline | With retrieval |
|---|---:|---:|
| Runs | 3 | 3 |
| Mean field accuracy | 100.0% | 100.0% |
| Mean validation rate | 100.0% | 100.0% |
| Mean latency | 4.088 s | 4.313 s |
| Latency range | 3.957 to 4.236 s | 3.623 to 4.688 s |

Retrieval selected the domain-appropriate example every time: cloud services for the cloud
invoice, hardware for the hardware invoice, consulting for the consulting invoice. **The
retriever works.** It costs 0.225 seconds on average, which is 5.5%, and the two latency ranges
overlap heavily enough that three runs cannot separate them with confidence.

## 7.4 The null result is a ceiling effect, and says nothing about retrieval

Both arms scored 100%. Read at face value that says retrieval does not help. It does not say
that, and the reason is in this report rather than in the experiment.

**The baseline in this comparison is not the baseline the rest of the report uses.** §8.12
documents a one-word correction to the extraction prompt that takes the model alone from 66.7%
to 100% on this sample. That correction shipped in the same branch as the retrieval work. So the
baseline arm here is the post-fix model, which already answers every field correctly on all
three documents.

**An arm at 100% cannot be improved on.** Whatever retrieval contributed, there was no headroom
in which it could appear. The experiment measured retrieval against a ceiling and reported the
ceiling. JJ's own write-up reaches the same conclusion and records that an earlier exploratory
run scored 10/15 before the controlled comparison, which is the pre-fix figure and is the
clearest evidence that the two arms were compared after the ceiling was already reached.

This is worth stating carefully, because the shape of the error is one the report warns about
elsewhere. A null result and an uninformative experiment look identical in a results table. The
only thing separating them is knowing what the baseline was capable of before the treatment was
applied, and that knowledge lived in a different section of a different branch.

## 7.5 What would make the experiment informative

Three changes, in order of how much they would buy.

**A harder evaluation set.** Three documents that the model already extracts perfectly cannot
discriminate between any two methods. The set has to contain documents the current pipeline
fails on, which means real invoices or synthetic ones written to be difficult, and it means
extending `evaluation/ground_truth.json` before rather than after.

**More runs, with variance reported.** Three runs establish that a difference of 0.225 seconds
exists; they do not establish that it is stable. Language models are not deterministic, and the
latency ranges here already overlap.

**A comparison against embeddings.** The deterministic scorer selected correctly on three
documents where the categories are distinct and the vocabulary does not overlap. That is the
easiest possible case for keyword matching, and it is the case least likely to distinguish it
from a semantic method.

## 7.6 What this section can and cannot claim

It can claim that retrieval-augmented prompting was implemented locally, that it selects
relevant examples deterministically, that it costs about 5.5% in latency, and that it introduces
no new failure the validation gate does not already check.

It cannot claim that retrieval improves extraction accuracy, and it cannot claim that retrieval
fails to. The experiment as run is incapable of supporting either, and reporting the null result
without that qualification would be the more serious error of the two available.
