# What `luke/poll-and-process` changes

**From:** Luke (`zethio44`). **Base:** `main` at `0062042`. **Schema version:** 17.

This pull request is the Gmail listener becoming a continuous worker, plus the pipeline changes that showed up once real mail and a scanned bill went through it. The longer design notes are:

| Document | What it covers |
|---|---|
| `email-listener-polling-spec.md` | The poll loop, claiming a batch, retries, and quarantine |
| `codeql-fixes-spec.md` | The two sender-controlled issues and the measurements |
| `database-spec.md` §4.11 | The `ingestion_failures` table |
| this file | The whole pull request, how to run it, and what to review |

`main.py`, `app.py`, and `storage.py` are Neo + JJ's lane. The regex rewrite, the scanned-PDF read, the stated-total repair, and the dashboard blank-cell fix all need one of them to review before merge. `email_listener.py` is Luke's lane.

## After you pull

An existing database is still at schema version 16 until migration 017 runs. A fresh clone creates version 17 on first use and should not run the migrations.

```bash
python -m pip install -r requirements.txt
python migrations/017_ingestion_failures.py    # existing database only; backs up first
```

On Windows, with the project virtualenv:

```powershell
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python migrations\017_ingestion_failures.py
```

Scanned pages also need the Tesseract program on `PATH`. The new Python packages (`pypdfium2`, `pytesseract`) only call it.

- Windows: https://github.com/UB-Mannheim/tesseract/wiki
- macOS: `brew install tesseract`
- Debian/Ubuntu: `sudo apt-get install tesseract-ocr`

The Docker image installs Tesseract in the Dockerfile. Ollama still has to be running before any extraction.

Watch the mailbox, or process a folder by hand:

```bash
python email_listener.py          # poll until Ctrl+C; processes each batch in the background
python email_listener.py --once   # download only
python main.py                    # one pass over the top of inbox/
python generate_fresh_invoices.py # three new sample PDFs in inbox/
python -m streamlit run app.py
```

`EMAIL_POLL_INTERVAL` in `.env` is the wait between checks, in seconds. The default is 10. Values below 1 are raised to 1.

## 1. The listener keeps polling and processes in the background

`python email_listener.py` used to download matching mail and exit. It now loops until Ctrl+C.

Each check searches the Inbox for **unread** messages whose subject contains `invoice`, records the message, saves qualifying attachments, and marks the message read only after that record succeeds. A message that could not be fetched stays unread.

It then moves the files at the top of `inbox/` into `inbox/.processing/<timestamp>/` and starts `WorkflowOrchestrator` on that folder in a background thread. The next check does not wait for Ollama, and it does not take files already claimed. A failed Gmail check still claims files that are already in the inbox. Ctrl+C stops the loop; in-flight threads are non-daemon, so the process waits for them to finish.

`--once` is the old download-only pass. `python main.py` is unchanged in shape: one pass over the top of `./inbox`, then exit. Both paths share the per-file result below, so a file `main.py` cannot store is quarantined too.

## 2. A file that cannot be stored is recorded, then left alone

Each file comes back as `stored`, `retryable_failure`, `needs_ocr`, or `unsupported`.

| Result | What happens |
|---|---|
| `stored` | Archived, as before. If the archive move itself fails, the file returns to `inbox/` for a later upsert. |
| `retryable_failure` | Returned to `inbox/` while the attempt count is under 3. An Ollama timeout, a database lock, or a missing Tesseract program is this case. After 3 attempts the file moves to `inbox/.failed/retry_exhausted/`. |
| `needs_ocr` | OCR ran and found no text. One attempt. Moved to `inbox/.failed/needs_ocr/`. Ollama is not called. |
| `unsupported` | Encrypted, unreadable, or a non-PDF with no text. One attempt. Moved to `inbox/.failed/unsupported/`. |

`claim_inbox` only lists files in the top of `inbox/` and skips names that start with `.`, so `.processing/` and `.failed/` are not claimed again. A killed process puts unfinished batches in `.processing/` back on startup. It does not put `.failed/` back.

Migration 017 adds `ingestion_failures`, one row per file-byte hash, with the reason, the attempt count, and the first and last attempt times. The same hash updates that row. A later successful store sets `resolved_at`. No invoice row is written until a run actually stores one. The key is the file bytes, because a page with no text would make every scan share one empty text hash.

## 3. Scanned PDFs are read locally

A PDF that already has a text layer is read with `pypdf` and OCR is not called. A page with none is rendered with `pypdfium2` and read with Tesseract. That text then follows the same extraction path as an embedded text layer. Nothing is uploaded.

The review dialog does the same read. It used to say the pipeline could not read scans. Opening the dialog again uses the file's modification time as the cache key, so a replaced archive file is read again and an unchanged one is not rendered twice.

A missing Tesseract program is a retry, not a dead file: the page was never read.

## 4. Three fresh sample invoices

`generate_fresh_invoices.py` writes three PDFs into `inbox/`. Two use a Grand Total the gate can verify. The third states the payable amount as "Amount payable" and does not use "total", "amount due", or "balance due", so the gate cannot mark the amount verified or score it 1.00. `generate_mock_invoices.py` is unchanged.

## 5. Two sender-controlled issues from the CodeQL pass

Measured detail is in `codeql-fixes-spec.md`.

**Fallback regexes.** `TOTAL_AMOUNT_PATTERN` was cubic on "Amount Due" followed by spaces and no amount: 2,000 spaces took 25.6 seconds, and that pattern runs whenever the model returns no total. `INVOICE_NUMBER_PATTERN` and `DATE_LABEL_PATTERN` were quadratic. Each optional separator now carries its own trailing whitespace, so two `\s*` runs are never adjacent. 200,000 characters of that input take under 0.03 seconds. Captures on the label variants in `tests/test_fallback_regex_timing.py` are unchanged.

**Attachment names on Windows.** `safe_filename` used to remove only `/` and `\`. `C:evil.pdf` is drive-relative, so the file landed outside `inbox/`. A colon later in the name wrote an NTFS alternate data stream. Names now keep the last path component, replace the characters Windows forbids, strip trailing dots and spaces, rename reserved device names, and keep a usable extension. Unicode letters stay. `download_attachments` also refuses a path that resolves outside the attachment folder.

## 6. Four defects found by running the pipeline

### A missing invoice number took Overview down

Pandas reads SQL NULL as NaN, and NaN is truthy, so `value or "-"` kept the float. `' · '.join` then raised `TypeError` on the first card. That happened on 2026-10-12: the newest pending document has no invoice number.

`shown()` returns `"-"` for None, NaN, and `""`. Card meta, the review dialog, History, the outbox, and Overview all go through it.

### A re-sent document looked like it had vanished

`save_invoice` never overwrites a decision a person already made. A rejected water bill was sent again on 2026-10-13 to be reviewed afresh. The upsert kept the rejection, no review task opened, and the console said only `Updated invoice_id=51`.

The rule is unchanged. The save result now includes `kept_decision` when a human decision was left in force (`approval_status` not `Pending`, and `reviewed_at` set). An automatic approval, which is Approved with no reviewer, is not reported. The pipeline prints, in red on a terminal:

```text
[Kept] invoice_id=51 was already Rejected by a person, so it stays Rejected and is not back in the approval queue. To review it again, open History in the dashboard and press Reopen.
```

Plain text when output is redirected, and when `NO_COLOR` is set.

### The previous bill was stored as the total

A scanned Sydney Water bill lays its summary out as labels on one line and amounts on the next:

```text
Last bill  Payments  Balance  This bill  Total amount due
$268.47    $268.47   $0.00    $279.40    $279.40
```

The model returned 268.47, the previous bill. Further down, the page says `Total amount due $279.40` on one line. The gate called 268.47 verified because it sits within two lines of a line containing "total". The regex fallback would have chosen the same number, because its whitespace can cross the line break under the label.

`stated_total_cents` reads an amount only when it sits on the same line as a grand-total label, and only when that amount has two decimal places. One such amount replaces a different extracted total (`_clean_total_amount`, so `evaluation/run_eval.py` still switches it off when measuring the model alone) and fills a missing total before the older regex. Two different stated totals are left for a person. Generated invoices put the amount on the next line, so they state nothing this way and keep the previous behaviour.

When the document states a total and the extracted amount is not one of those figures, the gate explains that and does not mark the amount verified.

### The review dialog can show the scan

Covered in section 3. The panel text is the same read the pipeline gave the model.

## What a reviewer should look at

1. `main.py` extraction: the three linear fallback patterns, `stated_total_cents`, `_clean_total_amount`, and `inspect_pdf` / `ocr_pdf`. This is the lane boundary.
2. `app.py`: `shown`, `document_text`, and the copy that used to say the pipeline cannot read scans.
3. `storage.py`: `kept_decision` on the upsert result, and `ingestion_failures`. Confirm a human decision is still never overwritten.
4. `email_listener.py`: claim, background thread, unread-only search, and the return-or-quarantine step.
5. `migrations/017_ingestion_failures.py` on a copy of a real database. It is additive and refuses to run out of order.

Suggested checks, from the project virtualenv:

```bash
python -m pytest tests/test_email_listener.py tests/test_ingestion_failures.py tests/test_ocr.py tests/test_attachment_names.py tests/test_fallback_regex_timing.py tests/test_generate_fresh_invoices.py tests/test_stated_total.py tests/test_document_text.py tests/test_kept_decision_notice.py tests/test_storage.py tests/test_app_tabs.py -q
python -m pytest tests/ -q
```

The tests that call the real Tesseract program skip when it is not installed. The rest replace `pytesseract` and run anywhere.

Run on this machine, 2026-10-14, Windows, with that first command: **334 passed, 2 failed**. Neither failure is from the behaviour above.

- `tests/test_storage.py::TestUpsert::test_archive_path_is_stored_absolute` expects the stored path to start with `/`. `os.path.abspath` on Windows returns `C:\...`, which is absolute. The assertion was already written that way; this change does not touch how archive paths are stored.
- `tests/test_app_tabs.py::test_the_card_carries_everything_the_approved_design_carries` looks for a "Covering email" chip in the working database. The app rendered with no exception. That chip appears only when a drawn card has a sender, which needs `seed_mock_emails.py` (or a real covering email) in the database this test reads.

## What this pull request leaves as it was

- The search is still the Inbox, and still `SUBJECT "invoice"`. Read mail, other folders, and subjects that do not contain `invoice` are not fetched.
- `save_invoice` still keeps a person's Approve or Reject. The new line only says so.
- A total the document never prints on the label's own line is still extracted and gated as before.
- Jira issue-key validation (SA-04) was dropped at review. Keys today come from Jira's own responses or the operator's command line.
- The dashboard still updates on the next page load. Refresh the browser, or click in the app, to see a row the background pipeline just stored.
