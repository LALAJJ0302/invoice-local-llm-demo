# Spec: Fix the Confirmed CodeQL Findings

**Owner:** Luke (`zethio44`). The `safe_filename` change is in Luke's lane; the regex change is in
`main.py` extraction, which is **Neo + JJ's lane** and needs one of them to review the PR.
**Branch:** `luke/codeql-fixes`, from `origin/main` at `0062042`.
**Status:** approved 2026-10-13 with SA-04 dropped. Implemented on `luke/codeql-fixes` and merged into `luke/poll-and-process`. The `main.py` regex change is Neo + JJ's lane and still needs one of them to review it.
**Evidence:** `static_analysis_codeql_1/Security_Report.docx` (CodeQL 2.27.2, run 2026-10-12).
That folder is local and untracked; the measurements this spec relies on are repeated below.

## Why now

A CodeQL scan with data extensions marking email, PDF text and model output as untrusted turned up
two issues that a sender of an invoice email can trigger. Both are fixed here. Every
number here was measured on this machine before writing the spec.

## 1. Fallback regexes backtrack polynomially (SA-01 High, SA-03 Low)

`TOTAL_AMOUNT_PATTERN` (`main.py:40`) has three `\s*` runs separated only by optional parts. On
text a PDF can contain, "Amount Due" followed by spaces and no amount, the search is cubic:

```
spaces   500    1,000   2,000    8,000
time     0.39s  3.13s   25.61s   ~27 min (extrapolated)
```

It runs in `_infer_missing_fields_fallback` whenever the model returns no total, which is the usual
case. `INVOICE_NUMBER_PATTERN` (`main.py:30`) has the same shape with two runs: quadratic, 0.10 s
at 4,000 spaces.

**SA-03b, added during implementation.** `DATE_LABEL_PATTERN` (`main.py:35`) was flagged too and
first dismissed, because it rejected 4,000 spaces in 0.000 s. That test was wrong: spaces satisfy
its `{6,25}` capture class, so the search matched immediately. Tabs and newlines are `\s` but not
in the class, and with them it is quadratic: 8,000 took 0.65 s and 32,000 took 9.8 s. CodeQL
re-flagged it on this branch once the other two were fixed. Same shape, same fix, Low severity.

### Change

Each optional token carries its own trailing whitespace, so no two `\s*` can be adjacent. The
labels and captured groups are unchanged.

```python
r"\s*(?:[:\-]\s*)?([A-Za-z0-9][A-Za-z0-9\-\/]{2,})"                    # invoice number
r"\s*(?:[:\-]\s*)?([A-Za-z0-9,\/\-\. ]{6,25})"                         # date label
r"\s*(?:[:\-]\s*)?(?:(?:[A-Z]{3}|\$|€|£)\s*)?([\d,]+\.\d{2})"           # total amount
```

Measured: 200,000 spaces in 0.028 s (total) and 0.017 s (invoice number); 200,000 tabs or
newlines in 0.015 s (date). Identical captures on 19 hand-written label variants and on all 19
PDFs in `archive/`.

### Tests (`tests/test_fallback_regex_timing.py`)

- Each pattern rejects "label + whitespace + `!`" in under 0.25 s: 1,500 spaces for the total
  (about 9 s before the fix), 20,000 spaces for the invoice number (about 2.4 s), and 32,000 tabs
  or newlines for the date (about 10 s). Large enough to fail before the fix, small enough not to
  hang the suite.
- A table of label formats (colon, dash, none, currency code, symbol, line break) for each
  pattern asserts the captured value, so the rewrite cannot quietly change what is extracted.

## 2. Attachment names can leave `inbox/` on Windows (SA-02 Medium)

`safe_filename` (`email_listener.py:82`) removes `/` and `\` only. On Windows:

```
"C:evil.pdf"               -> written to the working directory of drive C, not inbox/
"=?utf-8?b?Qzpldmlslpdf?=" -> decodes to "C:evil_", same escape
"invoice.pdf:hidden.pdf"   -> NTFS alternate data stream on invoice.pdf
".."                       -> resolves to the parent of inbox/
```

CodeQL did not report this one; it was found while reading the email path the scan highlighted.

### Change

1. `safe_filename` keeps only the last path component, replaces the characters Windows forbids
   (`<>:"/\|?*` and control characters) with `_`, strips leading and trailing dots and spaces,
   prefixes reserved device names (`CON`, `NUL`, `COM1`...), substitutes `attachment` for an empty
   result, and caps the length at 200 while keeping the extension.
   **Unicode letters are kept.** The report's draft used an ASCII allowlist, which would turn a
   Chinese filename into underscores; this version does not.
2. Before writing, `download_attachments` checks that the resolved path is inside
   `attachment_dir` and skips the attachment with a printed reason if it is not. This is the
   backstop if `safe_filename` ever misses a case.

Behaviour that changes: an attachment name containing any of `<>:"|?*` is stored with `_` in its
place, in `email_attachments.filename`, `email_messages.attachment_names` and on disk. No existing
record is rewritten.

### Tests (`tests/test_attachment_names.py`)

A new file rather than `test_email_listener.py`, which exists on `luke/poll-and-process` and would
conflict.

- Each crafted name above resolves inside the target directory.
- Ordinary names are unchanged: `Invoice 2024-001.pdf`, `發票-001.pdf`, `invoice.v2.pdf`.
- `CON.pdf`, an empty name and an over-long name produce usable, contained names that keep their
  extension.

## Out of scope

- Jira issue-key validation (SA-04, Low). Dropped at review: keys come only from Jira's own
  responses or the operator's command line today. Revisit if a key is ever taken from an email or
  the dashboard; the fix is a `^[A-Z][A-Z0-9_]+-\d+$` check where `jira_client.py` builds paths.
- The 110 path-injection alerts sourced from environment variables and CLI arguments (SA-05): the
  operator controls those values. No change.
- The dismissed SQL, timing and code-injection results. The report gives the reason for each.
- Capping `raw_text` length before the fallbacks. Worth discussing, but it changes extraction
  behaviour on long documents and needs its own measurement.

## Done means

- The full suite passes, with the new tests failing on `main` before the change and passing after.
- CodeQL re-run on the branch: no `py/polynomial-redos` alert remains on the three patterns.
- Included in the `luke/poll-and-process` pull request. Neo or JJ still reviews the `main.py` part.
