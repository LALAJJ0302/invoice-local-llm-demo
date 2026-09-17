# 1. Introduction

## 1.1 Industry context

*To be written.* Manual invoice handling in mid-sized organisations is estimated internally
at three to five minutes per document, with data-entry error as the main quality risk.

> **Gap.** That figure comes from our own briefing document and has no source. It needs a
> real citation on document-handling cost and error rates, or it must be removed. This is
> the one place in the report that requires outside literature about the problem rather
> than about the technology.

## 1.2 Objectives

Four objectives, each stated with how it would be judged. From `requirements-spec.md` §2.

1. Ingest invoices from email and process them without manual re-keying.
2. Extract the fields an accounts-payable process needs, into a queryable store.
3. **Produce evidence rather than assertions** about how well it works.
4. Keep a person in the approval path rather than automating the decision away.

## 1.3 Scope

In scope: text-layer PDF invoices, local inference, a normalised store, a review dashboard,
and since September, reading the email around the invoice rather than only its attachment:
classifying a message, summarising it, and extracting the actions people committed to in it.

Out of scope: OCR for scanned documents, payment execution, and integration with Teams,
Jira or Planner beyond a recorded queue.

**A note on the objectives above.** All four were written when this project handled invoice
attachments and nothing else. The email work either falls inside objective 1, read broadly, or
deserves an objective of its own. This report does not settle that, because objectives belong to
the group rather than to one member, and §5.9 is explicit that the email half has no measured
result yet to claim against any objective.
