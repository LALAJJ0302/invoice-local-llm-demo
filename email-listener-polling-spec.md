# Email listener polling

**Status:** implemented in `email_listener.py`.
**What it is:** `python email_listener.py` keeps checking Gmail until Ctrl+C. After each check it claims the files in `inbox/` and processes that batch in the background, using the same pipeline as `python main.py`. The next check does not wait, and it does not take files already claimed.

`python main.py` still reads the top of `./inbox` once and exits. It uses the same per-file results as the listener, so a file it cannot retry is quarantined instead of being left in the inbox. The listener calls `WorkflowOrchestrator` with `inbox_dir` pointed at the claimed batch.

## Why

The listener used to be one pass: download matching mail, exit, then run `main.py` by hand. A poll that waits for extraction is also the wrong shape. Three invoices block the next Gmail check for as long as Ollama takes, so a 10-second interval never actually checks every 10 seconds.

Claiming the files is what stops a second poll from processing the same three. `WorkflowOrchestrator.run` lists only the top of the folder it was given. Once those files have moved to `inbox/.processing/<batch>/`, the next claim cannot see them.

## What a polling run does

Default wait is 10 seconds (`DEFAULT_POLL_INTERVAL_SECONDS`). `--interval` and `EMAIL_POLL_INTERVAL` still override it. Values below 1 second are raised to 1. An invalid `EMAIL_POLL_INTERVAL` falls back to 10.

1. On startup, move any batch left in `inbox/.processing/` by a killed process back to `inbox/`. A batch a live thread still owns is left alone.
2. Log in over IMAP and search the Inbox for unread messages whose subject contains `invoice`. Mail that is already read is not fetched. `Invoice 1042` and `Re: invoice for March` match. `Receipt` and `Bill` do not.
3. Record each match in SQLite (Message-ID, sender, subject, date, body, attachment names) and save qualifying attachments into `ATTACHMENT_DIR` (`./inbox` by default).
4. Mark a message read only after it has been recorded. A message that could not be fetched or recorded stays unread so the next poll can try again.
5. Move every real file still in the inbox into `inbox/.processing/<YYYYMMDD-HHMMSS>/`. Names starting with `.` and directories stay put. A second claim in the same second uses `<stamp>-1`, `<stamp>-2`, and so on. `os.replace` keeps each file either in the inbox or in the batch.
6. Start a non-daemon thread on that batch: `WorkflowOrchestrator(inbox_dir=<batch>, archive_dir="./archive", threshold=0.80)`. An empty inbox starts no thread.
7. Sleep, then check again.

A failed Gmail check still claims files already in the inbox. Two batches may run at once. SQLite is in WAL mode and each write opens its own connection, so the dashboard can keep reading. Ollama may still run one local inference at a time. The listener no longer waits on that.

Email linking is by file name. The move does not rename the file, so the message that delivered it still matches.

## When a file comes back

The pipeline returns one result per file: `stored`, `retryable_failure`, `needs_ocr`, or `unsupported`.

Archived files are already in `archive/` and are not returned. A `stored` file whose archive move failed stays in the batch and goes back to `inbox/` for a later upsert. That return is not counted as a failure.

Only `retryable_failure` is a retry, and only while the attempt count is under 3. An Ollama timeout or a database lock is this case. The file goes back to `inbox/` and a later poll claims it, after this batch has finished, not while it is in flight. A name collision on the way back becomes `name_1.pdf`, so a newer download is not overwritten.

Anything else is moved under `inbox/.failed/` before the leftovers are released. `claim_inbox` only lists files in the top of `inbox/` and skips names that start with `.`, so these are not claimed again. A killed process still recovers only `inbox/.processing/`.

- `needs_ocr` — OCR read a PDF that had no embedded text and found nothing. One attempt. Ollama is not called. Moved to `inbox/.failed/needs_ocr/`. A PDF that already has a text layer never reaches OCR. A missing Tesseract program is `retryable_failure` instead, because the page was never read, and after three attempts it goes to `inbox/.failed/retry_exhausted/`.
- `unsupported` — encrypted, unreadable, or a non-PDF with no text. One attempt. Moved to `inbox/.failed/unsupported/`.
- `retryable_failure` after 3 attempts — moved to `inbox/.failed/retry_exhausted/`.

Each failure is one row in `ingestion_failures`, keyed by the file's byte hash, with the reason, the attempt count, and the first and last attempt times. The same hash updates that row instead of inserting another. A later successful store sets `resolved_at`. No invoice row is written until a run actually stores one.

## Stopping

Ctrl+C stops the loop immediately. In-flight threads are non-daemon, so the process exits when they finish instead of leaving a batch half-written. The listener prints how many pipelines are still running.

`python email_listener.py --once` only downloads. It does not claim or process.

`python main.py` is still how you process files dropped straight into `inbox/` without the listener. Ollama must be running either way. The dashboard reads the database on the next page load. Refresh the browser, or click in the app, to see new rows.

## Docker

`docker compose run --rm listener` runs `python email_listener.py`, so it polls and processes in the background until the container is stopped. No compose command change was required. `pipeline` is still a one-shot `python main.py`.

## What this does not change

- `main.py` has no new flag. A bare `python main.py` still scans only the top of `./inbox`, so it will not see files under `inbox/.processing/` or `inbox/.failed/`.
- The search is still the Inbox, and still `SUBJECT "invoice"`. Other folders, and subjects that do not contain `invoice`, are not fetched.
- Two emails can still save the same attachment name if the first file has already left `inbox/`. That was already true after a file was archived. The link to the covering email is the newest matching file name.
