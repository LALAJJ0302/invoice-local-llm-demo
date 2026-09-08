# Group Update: 2026-09-08 (evening)

**From:** Neo. **Read time:** 5 minutes. The asks are in section D.

Every status line was checked by running something today. Commands are included so anyone
can verify a claim without asking me.

---

## A. Progress against the work JJ assigned

Honest status. Two of five are finished, three are part-done, and none of the three are
part-done because of my time.

| # | Assigned | Status |
|---|---|---|
| 1 | Research and shortlist models, hardware and runtime compatibility | **Done** |
| 2 | Prepare test emails and manually labelled expected results | **Half.** Emails built, labels not |
| 3 | Run model comparisons, analyse classification / summarisation / extraction errors | **Third.** Extraction only |
| 4 | Build the historical email retrieval module | **Built, not measured** |
| 5 | Lead the model comparison and experimental results sections | **Done as evidence, see D3** |

### 1. Model shortlist: done

Six models pulled and running locally. Every name was checked against the Ollama registry
rather than recalled. Compatibility was measured before accuracy, because a model that
cannot return valid JSON, or that ignores `required`, fails in the same way as the schema
defect we already documented, and the two would be indistinguishable.

```
model             valid JSON   fields under required   sec/call
llama3.2:3b          3/3               5.0/5             2.1
gemma3:4b            3/3               5.0/5             4.0
llama3.1:8b          3/3               5.0/5             4.7
qwen2.5:7b           3/3               5.0/5             5.1
phi4:14b             3/3               5.0/5             9.2
```

**No model failed.** The variable that separates them is latency: `phi4:14b` is 4.4 times
slower than `llama3.2:3b` and no more accurate on this task. That is a measured reason to
keep running the smallest model, rather than a default we never examined.

```bash
./.venv/bin/python evaluation/model_compatibility.py
```

### 2. Test emails: built, unlabelled

21 messages across three vendors are generated and loaded, with a real history per vendor
including a query about a line item and the credit note that answers it, plus resends
carrying duplicate Message-IDs.

**There is no ground truth for them yet, and that is blocked on JJ.** Labels for extraction
are obvious. Labels for classification are not, because the class list does not exist, and
summarisation has no objective correct answer without either a reference summary or a
rubric. See D1.

### 3. Error analysis: extraction only

Extraction is measured. **Classification and summarisation cannot be, because neither
exists in the codebase.** Classification today is `storage.classify_document`, a marker
check on the opening lines returning Invoice, Receipt or Unknown. Summarisation appears
nowhere.

```bash
grep -rniE "summar" main.py storage.py app.py     # nothing but summary-row handling
```

I am not asking anyone to build them. I am asking which module is supposed to, so I know
whether to plan for it. See D1.

### 4. Retrieval: built, and I have not measured it

`retrieval.py` implements two strategies and is wired into the pipeline. `sender` returns a
vendor's prior messages newest first. `keyword` scores subject and body by inverse document
frequency. 16 tests cover ordering and relevance rather than merely that results come back.

**I have not measured which is better, and that is a gap I want to name rather than have
someone find.** We have built two approaches and reported neither, which is the thing we
would criticise in someone else's work. Embeddings are deliberately not implemented until
that measurement exists: "we used a vector database" is not a finding.

## B. What I built that was not on the list, and why

**The retrieval module had no data.** `email_messages` had 0 rows because
`email_listener.py` never calls `record_email`. Rather than wait, I built a mock mailbox
that goes in through the same API intake will use, so nothing has to be undone when Luke
wires the real thing. Luke's file was not touched.

**Two migrations.** 008 adds the email body, because retrieval on sender and subject alone
can answer "has this vendor written before" and nothing else. 009 adds attachment names,
which closes the piece the design left open: a PDF in `inbox/` can now be traced back to the
message that delivered it. **`invoices.email_id` is now populated 3 of 3, where it was 0 of 3.**

**A correction to something we have all been repeating.** See C.

## C. We have been overstating the schema finding

Our documents say the `Optional` schema causes the 20% extraction result, and that the
failure should not be blamed on the prompt. The full two-by-two says otherwise:

```
ORIGINAL prompt x Optional schema     6/15
ORIGINAL prompt x Required schema    15/15
IMPROVED prompt x Optional schema    15/15
IMPROVED prompt x Required schema    15/15
```

The schema effect is real. **The prompt effect is the same size.** They are not additive:
either fix alone reaches the ceiling. The failure required both at once.

**This is a stronger result than the one it replaces, not a weaker one.** A two-by-two that
identifies an interaction beats a single comparison reported with more confidence than its
design supported. But it does mean the line "it is not the prompt" has to come out of our
documents, and I have already removed it from mine.

A third factor turned up while reconciling two numbers that disagreed. Adding a nested list
field (`items`) to the schema costs three scalar field-values elsewhere, reproducibly across
three runs. So schema design has at least two dimensions, not one.

**All of this is measured on `llama3.2` only.** Confirming it across the other five models is
on my list and is not done.

```bash
./.venv/bin/python evaluation/prompt_schema_2x2.py
```

## D. What I need

**D1. JJ: which module classifies and summarises, and what are the labels?**
Neither exists today. Items 2 and 3 cannot be finished without a class list and a definition
of a correct summary. This is the single largest blocker on my half.

**D2. Luke: when can intake call `record_email`?**
The API is implemented and tested. My mock closes the gap for development, but every
retrieval number I produce is measured over generated text until real messages arrive, and
the report has to say so.

**D3. Everyone: confirm the report is individually marked.**
"Lead the model comparison and experimental results sections of the report" is not possible
as written, because each of us submits our own report worth 80% of the subject. What I can
do, and have done, is produce the shared **evidence pack**: the comparison tables, the
method, the figures. Everyone writes their own prose around it. Shared template and topics,
separate writing. Similarity detection applies.

**D4. JJ: may I open the PR for `neo/gate-completeness`?**
Five commits, 248 tests passing. It includes the gate fix, the retrieval module, both
migrations and three written report sections.

## E. For information

- The validation gate was scoring **1.00 on an extraction with a malformed vendor and zero
  line items**. A perfect score now means nothing is empty; that document scores 0.85 and
  names `items` as the gap.
- The end-to-end workflow runs: inbox, text extraction, retrieval, extraction, gate, SQLite,
  archive, dashboard, approval, task, notification queue.
- 248 automated tests, schema version 9.
- Report draft v1 is 5,631 words with sections 3, 4 and 5 written. Those are the three
  sections carrying 50 of the 80 marks.

## F. The thing I would raise if we only have two minutes

The draft is due in Week 8, which is somewhere between 6 and 13 days away, and **nobody has
confirmed the date**. The code is in good shape. The report is 3 sections out of 8, and it is
80% of the subject.

Could someone check Canvas today for the Week 8 supervisor meeting date?
