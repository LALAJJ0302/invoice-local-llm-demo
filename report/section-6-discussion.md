# 6. Discussion

## 6.1 Whether the pivot was the right decision

The project began as a Microsoft cloud design: SharePoint for storage, Power Automate for
orchestration, Copilot for extraction and Power BI for reporting. It moved to a local
open-source stack after the team could not obtain the UTS tenant permissions the design
required, and on the supervisor's advice.

Judged only on features delivered, the pivot lost ground. There is no Teams notification, no
Jira task, and no Power BI report. Judged on what the project was for, it gained more than it
lost, and the reason is visible in section 5. **Every experiment in this report required
changing one variable and holding the rest constant.** The two-by-two in §5.4.2 needed the
prompt and the schema separated. The schema arms in §5.5 needed four different schemas run
against the same documents at the same temperature. None of that is possible against a hosted
service whose prompt, model version and decoding constraints are not under the team's control
and can change without notice.

The pivot therefore converted a permissions problem into a methodological advantage that was
not anticipated at the time. **This should not be presented as foresight.** The decision was
forced, and the benefit was noticed afterwards. Reporting it as a deliberate design choice
would be the same category of error this report criticises elsewhere: a confident account
constructed after the fact to fit a result.

One consequence does count as a loss and should be stated plainly. A local 3-billion-parameter
model is not a fair proxy for a frontier hosted model. Nothing here establishes that Copilot
would have performed worse, and §5.6 shows that every local model tested handled constrained
decoding correctly, which means the constraint was never the model's capability.

## 6.2 What the 20% result actually means

The headline figure that opened this project was that extraction scored 3 of 15 field-values,
or 20%. It is the most quoted number in the team's documents and the most misread.

It is not a measure of what a local language model can do. It is a measure of **one prompt and
one schema declaration**, and §5.4.2 shows that repairing either one alone reaches 15 of 15.
The model was capable of the task throughout. The failure was in how the task was specified to
it, which is a software defect rather than a limitation of local inference.

This distinction changes the recommendation a reader should draw. "Local models are not accurate
enough for invoice extraction" does not follow from anything measured here. "An under-specified
schema silently produces empty fields, and the resulting output looks like a working system"
does follow, and is the more useful finding because it is a failure mode that survives any
change of model.

## 6.3 Seven lessons, each attached to the evidence that produced it

These are recorded as lessons because each one cost the project time before it was understood,
and each was established by measurement rather than by review.

**1. A design can pass review and still fail on contact with data.** The deduplication key was
specified as a hash of the file bytes, was reviewed, and was agreed. It would have deduplicated
nothing, because ReportLab writes a random `/ID` into every generated PDF, so an identical
invoice produces a different byte hash on every run. The defect was invisible in the design
document and obvious the moment real files were hashed. The key is now a hash of the extracted
text.

**2. A claim repeated in documentation is not a measurement.** "Required fields fix the
extraction problem" appeared in the team's notes for several days before any script in the
repository could reproduce it. Once written down and cited internally, it acquired the
appearance of an established result without ever having been one.

**3. A constraint that is written is not thereby tested.** The database carried a `CHECK`
constraint intended to reject malformed dates, written as `GLOB '____-__-__'`. In `GLOB` the
underscore is a literal character, not a wildcard; that is `LIKE`'s syntax. The constraint
therefore rejected every valid date. It never fired, because every date in the table was `NULL`,
so a constraint that was both wrong and useless sat in the schema undetected until a test suite
was written for it.

**4. A fix can move a failure rather than remove it.** Requiring fields took extraction from 20%
to 100% and made the model invent values for three of nine genuinely absent fields, where the
permissive schema invented none. The improvement and the harm were produced by the same change.
A report that quoted only the first number would describe a system that had become less safe as
though it had become more reliable.

**5. A single-cause explanation is fragile in proportion to how convenient it is.** The
permissive schema was reported as the cause of the 20% result for two weeks, on the evidence of
one comparison. A fuller design showed the original prompt was an equally sufficient cause. The
first comparison was not wrong. It was incomplete, and it was reported with more confidence than
its design supported, which is the harder error to notice because nothing in it is false.

**6. A measurement can be wrong in a way that hides exactly what it was built to find.** A check
was written to count how often subject matching merges unrelated messages into one conversation.
The first version counted threads containing more than one sender, which seemed obvious and was
useless: a genuine reply chain almost always spans several senders, because a reply comes from a
different address than the message it answers. It would have reported every real conversation as
a defect, and the real collisions would have been indistinguishable inside that number. The
version that works counts threads where no message announces itself as a reply, which is several
originals sharing a subject. **It was caught because a test fixture contained one real
conversation and one real collision, and the metric could not tell them apart.** A fixture with
only collisions in it would have agreed with the broken metric.

**7. Tests written by the people who wrote the code share its blind spots.** A field that may be
absent was covered by twenty-eight tests. The model then returned the literal string `"null"`,
which is text and not an absent value, and every one of those tests still passed, because nobody
writing a test invents that input. It was found by running two real messages through the live
model. Constructed inputs test what the author imagined; the system's actual output tests what
it does. **This is an argument for running the real thing early and often, not for writing more
tests of the same kind**, and it is the second time in this project that running something found
what reading it had not, the first being lesson 1.

## 6.4 An eighth lesson, about how this report was produced

This project asks whether an AI system can be trusted with document work. It was itself built
with an AI assistant, and the honest answer emerged from that collaboration rather than from the
pipeline.

The assistant made several identifiable errors over the project. Every one was caught, and the
record of what caught each is in [ai-collaboration-account.md](ai-collaboration-account.md).
Four were caught by tests, one by measuring an assumption before building on it, and **two by a
person asking how a claim was known.** No test could have caught the last two, because the code
was working correctly and the error was in the surrounding explanation.

The pattern worth reporting is that **the assistant's worst errors had the same shape as the
model's**: a gap filled with something that looked like an answer. The model filled an unreadable
field with a default value. The assistant filled an unverified claim with a confident sentence.
Both were invisible for the same reason, which is that `None` and a fluent assertion are both
indistinguishable from data until something checks them.

An instance of this occurred while writing section 5 and is worth quoting because it was caught
in advance. The schema proposed in §5.5.1 was specified with its predicted results written down
before the measurement was run. The central prediction, that a nullable field would eliminate
invented values, **was wrong**. Had the prediction not been recorded first, the favourable
columns either side of it would have supported a claim the evidence does not make.

## 6.5 What this evaluation cannot support

The limits below are restated here rather than left in §5.2, because a discussion section that
draws conclusions without re-stating its own boundaries invites the reader to over-read them.

**The sample is three synthetic documents, plus five written to be missing specific fields.**
Every accuracy figure in this report rests on that. The documents were generated by the team,
which means they contain the layouts the team thought of. SROIE, by comparison, provides 347
real scanned receipts in its test split alone.

**No scanned document has ever passed through the pipeline.** Extraction requires a text layer,
and there is no OCR path. The most common real-world invoice format is therefore untested.

**One model carries every accuracy claim.** `llama3.2` produced all of section 5's field-level
results. The six-model comparison in §5.6 establishes that the others accept constrained
decoding, not that they extract equally well.

**The validation gate has never been measured against fraud, duplication, or an invoice from a
vendor the organisation does not use.** Every check it performs is internal to the document, so
a well-formatted document that should not be paid scores exactly as well as one that should.
§7.2 treats this as the most important open risk rather than a limitation of measurement.

**The email half has no accuracy figure of any kind.** §5.9 reports what one run produced and
says explicitly that none of it is a score. The classifier used one of its six labels on all 18
messages, and this report does not know whether that is correct, because every message in the
mailbox concerns an invoice. Nothing in section 6 draws a conclusion about how well that half
works, and any sentence that appeared to would be unsupported.

**One detail is worth separating from the rest.** The first four limitations are about
insufficient or unrepresentative data, and more data would reduce them. The email limitation is
different in kind: the data exists and the pipeline runs over it, but there is no ground truth
to compare against, so the quantity of data does not help. The two need different remedies, and
conflating them would make the second look closer to solved than it is.
