# What the AI Did, What It Got Wrong, and How Each Error Was Caught

Working notes for writing the report, not report text. Written 2026-08-30.

This project was built with an AI assistant. The report has to be written by Neo, and written by
someone who understands what the assistant actually did. This file is the honest account, kept
separate from the specs so it can be read as one thing.

There is a coherent argument to make from it, and it is not a defence. This is a project about
whether an AI system can be trusted with document work. It was built with an AI assistant that made
**seven identifiable errors**, every one of which was caught by a verification mechanism. That is
the project's own thesis applied to itself, with data.

---

## 1. The errors, in order

Each one is a real mistake by the assistant, not a design trade-off.

### 1.1 A deduplication key that would have deduplicated nothing

The first schema design keyed uniqueness on a SHA-256 of the PDF file bytes. It reads correctly and
survived a spec review.

ReportLab writes a random `/ID` into every PDF it generates. Regenerating the mock invoices produces
byte-different files with identical content, so a byte-hash unique index would have admitted three
new rows on every run, which was the exact problem the design existed to solve.

**Caught by:** measuring the assumption before writing the code. Hashing two generated copies of the
same invoice took about a minute.

**Fix:** key on a hash of the extracted text, which is stable. `source_sha256` is kept as
non-unique provenance.

### 1.2 A CHECK constraint that rejected every valid date

Written as `CHECK (invoice_date GLOB '____-__-__')`. GLOB has no single-character wildcard: `_` is a
literal underscore, and the `_` wildcard belongs to `LIKE`.

```
SELECT '2026-08-10' GLOB '____-__-__'   ->  0
SELECT '____-__-__' GLOB '____-__-__'   ->  1
```

It shipped in migration 001 and never fired, because the extractor returns `NULL` for every date so
no row ever carried one. It would have fired on the first insert after the extraction fix, and
would have looked like that fix breaking the database.

**Caught by:** the test suite, on its first run, by a test that saves a normal invoice.

**Fix:** migration 003, character classes that also verify the characters are digits.

### 1.3 A float comparison inside the money check

The validation gate compared an extracted total against numbers in the document using
`abs(value - amount) < 0.01`.

```
abs(1500.0 - 1500.01) == 0.009999999999990905    which is less than 0.01
```

A near miss compared equal. This was written **in the same project that had moved money to integer
cents two days earlier for exactly this reason**, and in the function that decides whether a stated
amount is real.

**Caught by:** a test asserting that 1500.01 is not 1500.00.

**Fix:** compare in integer cents through `storage.to_cents`.

### 1.4 A claim repeated from documentation as though measured

"The same model and prompt with required fields returns 5/5" appears in `CLAUDE.md` and in the
vault, and the assistant repeated it across several conversations as established fact.

Nothing in the repository could reproduce it. `evaluation/run_eval.py` only toggles the vendor
fallback and has no way to vary the schema at all. The project's central causal claim was an
assertion that everyone, the assistant included, had stopped questioning.

**Caught by:** Neo asking for the reasoning again, which forced the assistant to look for the
script that produced the number and find that there wasn't one.

**Fix:** `evaluation/schema_comparison.py`. The real figure is 3/15 against 15/15.

### 1.5 A recommendation from a single observation

After one test on one document, the assistant reported that a sentinel value (instructing the model
to answer `NOT_FOUND` when a field is absent) stopped the model inventing data, and recommended it
as the best available mitigation.

Measured properly across five documents and nine absent fields, the sentinel produced results
**identical to plain required fields**, down to the same three invented values.

```
variant    | extracted right | admitted when absent
required   |     12/13       |         6/9
sentinel   |     12/13       |         6/9
```

**Caught by:** Neo asking for it to be measured before being adopted.

**Note:** this is the same failure as 1.4 in a different form. Both are a confident claim built on
evidence too thin to carry it.

### 1.6 Migrations that reported failure when re-run

Migrations 002 to 004 returned a non-zero exit code when run against a database that had already
moved past them. The documented instruction to teammates is a loop that runs every migration in
order, so running it twice would have looked broken to whoever pulled next.

**Caught by:** a test that re-runs the whole sequence.

**Fix:** treat "already applied and moved on" as success.

### 1.7 Column order drift between a fresh database and a migrated one

`ALTER TABLE ADD COLUMN` can only append. The assistant declared three new columns mid-table in the
DDL, so a database created fresh and one built by replaying the migrations ended up with the same
columns in different orders, which changes what `SELECT *` returns.

**Caught by:** `test_migrated_matches_fresh`, a test written specifically because this class of
drift is invisible until it appears on someone else's machine.

---

## 2. What caught them

| Mechanism | Errors caught |
|---|---|
| Automated tests | 1.2, 1.3, 1.6, 1.7 |
| Measuring an assumption before building on it | 1.1 |
| A person asking for the reasoning again | 1.4, 1.5 |

Two observations worth writing up.

**The tests caught four errors and three of them on the first run.** The test suite was written
after the code, as regression protection, and immediately found defects that had been sitting in
the schema for days. One of them (1.2) was scheduled to detonate under someone else's change.

**The two the tests did not catch are the two that matter most.** 1.4 and 1.5 are not code defects.
They are confident claims resting on evidence too thin to support them, and no test can catch that,
because the code was working correctly. They were caught by a person asking "how do you know".

---

## 3. The parallel worth putting in the Discussion

The project's central technical finding is that the language model, when the schema permitted it,
**silently omitted fields and let default values stand in for answers it had not given.** The
failure was invisible because `None` and `0.0` look like data.

The assistant's own two most serious errors were the same shape. It repeated a number it had not
verified (1.4) and generalised from one observation (1.5). In both cases the output looked like a
finding and was not, and the gap was invisible because a confident sentence looks like a verified
one.

**Same failure mode, different layer.** The model filled a gap with a default; the assistant filled
a gap with an assertion. In both cases the fix is the same: make the system state what it actually
did, and check it against something independent.

This is worth several paragraphs of Discussion, and it is honest. It also directly supports the
project's design decisions: `total_source`, `vendor_source` and `reconciliation` all exist to stop a
derived value being mistaken for an observed one.

---

## 4. Division of labour, stated plainly

Useful for understanding the project, and for any disclosure the Subject Outline requires.

| | |
|---|---|
| **Assistant produced** | Schema design, migration scripts, test suite, the evaluation scripts, first drafts of every spec, this file |
| **Neo decided** | Every schema decision put to him (dedup key, naming, summary-row rule, legacy table, task routing); the order of work; what to defer and why; not to cross lane boundaries into JJ's and Luke's files |
| **Neo caught** | The two errors no test could catch, by asking for reasoning rather than accepting output |
| **Measurement decided** | The dedup key, the sentinel rejection, the extraction cause, the reconciliation rule. None of these were settled by opinion |
| **Neo will write** | The report |

The third row is the one that matters for understanding how this project was actually run. The
assistant was not left to produce unchecked work: the two failures it could not detect itself were
both found by being asked to justify a claim.

---

## 5. Why the tooling question is not the interesting one

Using an AI assistant to build a project about AI document automation is not a contradiction. It is
the same thesis applied to itself, and the results are usable data rather than an awkwardness to
manage.

The interesting question is not "was AI used" but **"what checked it"**. This project can answer
that specifically: seven errors, seven mechanisms that caught them, and every claim reproducible by
running a script.

```
./.venv/bin/python evaluation/schema_comparison.py      3/15 vs 15/15
./.venv/bin/python evaluation/sentinel_comparison.py    the cost of the fix
./.venv/bin/python -m pytest tests/ -q                  194 tests
for m in migrations/00*.py; do ...; done                before/after reports
```

Nothing in this project's evidence rests on trusting the assistant, or on trusting Neo. It rests on
things a marker could run.
