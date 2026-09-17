# 7. Recommendations and Future Work

The recommendations are ordered by what a later team would have to do first, not by how
interesting the work is. Each states what is already measured, so that the next team can tell
which items are decisions and which are open questions.

## 7.1 Work that is specified and measured but not shipped

Three changes exist as specifications with measured results and were deliberately not merged,
because each alters behaviour the whole group depends on and the group had not yet decided.
Listing them as future work rather than as omissions is the accurate framing.

**Adopt the nullable-required schema.** Measured in §5.5.1. It matches the best extraction
accuracy of any schema tested, produces the fewest classified errors of any arm, and converts
five undetectable placeholder strings into machine-detectable nulls. It should be adopted on
those grounds. It should **not** be adopted on the grounds originally argued for it, which was
that it would end invented values, because it does not.

**Extend the validation gate to verify the date and the currency.** Both fields are currently
checked for non-emptiness only, which means a wrong date and an invented currency both pass. The
technique already exists in the gate for the total amount, which is verified by proximity to a
labelled total. A preview run shows both fields moving to `verified` on true values and to
`absent` on invented ones, with no change to the three samples' pass rate.

**Fix the subtotal defect in the same change.** The gate's label matcher tests `"total"` as a
substring, and `"total"` occurs inside `"subtotal"`. A pre-tax subtotal is therefore accepted as
a grand total. This has never fired on the project's own documents, which contain no subtotal
line, and will fire on the first real invoice carrying GST. The fix is a word-boundary match,
with `"Sub Total"` written as a space-separated exclusion.

**Ship the hybrid retrieval strategy.** Measured in the retrieval evaluation. Neither shipped
strategy dominates: keyword search scores 1.00 on thread queries and 0.00 on vendor queries,
because earlier messages in a thread do not repeat the company name, while sender filtering is
the reverse. Hybrid, which filters by sender and then ranks by keyword, is the only strategy
that is never worst on either query type. It exists in the evaluation harness only.

## 7.2 The gap that matters most, and why no amount of extraction accuracy closes it

The group agreed that a validation score of 1.00 should skip the human approval step. That
decision should not be implemented in its current form, and this is the project's most important
recommendation.

**Every check the gate performs is internal to the document.** It asks whether the extracted
values match the text on the page. It cannot ask whether the invoice is a duplicate, whether the
vendor is one the organisation buys from, or whether the document is a well-formatted fraud. All
three score 1.00 today. The score measures reading accuracy, and reading accuracy is not payment
authority.

§5.5.1 produced the clearest demonstration of why extraction quality alone cannot close this gap.
Presented with a statement of account containing no total, every required-family schema returned
`2000.00`. The document shows an opening balance of `1,200.00` and payments received of `800.00`.
The model added them. It is not fabricating a value from nothing; it is performing arithmetic on
the page, and the arithmetic is wrong in kind, since a payment received should be subtracted. The
defensible answer is `400.00` and the correct answer is that no total exists.

This defeats the obvious safeguard. A fabricated value can be caught by asking whether it appears
anywhere in the document. **A computed value passes that test by construction**, and is
arithmetically consistent with the page, which is precisely what makes it convincing. The gate
does not catch it either: its reconciliation step awards a passing grade when the total exceeds
the line-item sum, and here the total equals the sum of two numbers that are not addends.

Before any auto-approval ships, the following are prerequisites rather than improvements:
duplicate detection on `(vendor, invoice_number)` rather than on content hash, a vendor allow-list
that leaves the document, and an audit trail in which an automatic approval is distinguishable
from a human one.

## 7.3 Functional gaps for a later team

**OCR is the largest functional gap.** Scanned and photographed invoices are skipped entirely,
because extraction depends on a PDF text layer. This excludes the most common real-world format.
It is listed first because no accuracy improvement matters to an organisation whose invoices
arrive as images.

**Real documents, with ground truth extended before they arrive.** Every heuristic in this
project is fitted to three files the team generated. The ground-truth file covers only those
three. Adding real invoices without extending it first would mean running experiments with no
way to determine whether they helped, which would undo the one methodological commitment this
project has kept. **The email half is already in that state**, which is the clearest argument
for the order: it was built and runs, and there is nothing to compare its output against.

**Embedding-based retrieval is deliberately unbuilt.** A third strategy was considered and not
implemented, on the reasoning that adding a strategy before measuring the two that already
existed would have produced a preference rather than a result. That measurement now exists, so
the work is justified where previously it was not.

**The notification outbox holds only `Pending` rows.** No Teams message is sent and no Jira task
is created. This is an honest boundary rather than an incomplete feature: the queue records what
would be sent, and the integration that would drain it was out of scope after the pivot. It
should be presented to a later team as a defined interface with one unimplemented consumer.

**Ground truth for the email half, and it belongs first.** Classification, summarisation and
action extraction have no labelled set, which means §5.9 can report what a run produced and
nothing about whether it was right. This is the single change that would move the newer half of
the project from demonstrable to evidenced, and it is a larger job than it sounds. A labelled
set of invoice-related emails is not enough: all 18 in the current mailbox are correctly
`Invoice`, so a classifier that ignores its input scores perfectly. The set has to contain
messages that genuinely are not invoices, which means writing them, which means deciding what a
correct summary is before anything can be marked wrong.

**Thread identity from headers rather than subjects.** Conversations are currently reconstructed
by matching subject lines, which merges three unrelated "Monthly statement" messages into one
thread in the project's own test data. The correct key is `In-Reply-To` and `References`, which
requires capturing two headers at intake. The database already carries a column recording which
method produced each grouping, so the change is additive and the existing rows do not become
wrong, only superseded.

**An email action item cannot reach the notification queue.** The outbox requires an invoice, so
work the model found in a message has nowhere to go. The argument for separating invoice tasks
from email actions in storage was never an argument for two separate queues, and notification
should have one path. It needs a migration and a decision about whether these items notify
anyone at all, which is why it is listed as future work rather than done.

## 7.4 Implications beyond this project

The transferable finding is not about invoices. It is that **a structured-output specification is
a safety control, and is currently treated as a formatting detail.**

Three of this project's four schema variants produce the same headline accuracy and materially
different failure behaviour. The permissive schema fails by omitting, so nothing false is ever
stored. The required schema fails by inventing, so a wrong value is stored with the same
confidence as a right one. An organisation reviewing an extraction system on accuracy alone
cannot distinguish these, because accuracy is identical across them. It would have to ask what
the system does when it cannot read a field, and that question is rarely part of a procurement
evaluation.

The second implication follows from §6.4. The failure mode that recurred at every level of this
project, in the model, in the assistant used to build it, and in the team's own documentation,
was a gap filled with something that resembled an answer. The controls that caught it were not
sophisticated: a test, a measurement taken before a claim was made, and a person asking how a
claim was known. Organisations deploying document AI should expect to invest in the third, because
it is the only one of the three that catches an error in reasoning rather than in code.
