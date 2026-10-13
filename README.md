# Invoice Local LLM Demo

Local-first invoice and receipt automation prototype using Gmail IMAP, Ollama, SQLite, and Streamlit.

## Overview

This project demonstrates an end-to-end document workflow that replaces the original Microsoft/SharePoint/Copilot-heavy design with a local prototype.

The current workflow is:

```text
Gmail invoice email
-> email_listener.py downloads attachments
-> local inbox folder
-> main.py extracts invoice data using Ollama
-> workflow_platform.db stores structured results
-> app.py displays results in a Streamlit dashboard
```

The system can also be tested without Gmail by manually placing PDF invoices into the `inbox/` folder.

## Why This Local Version Exists

The original plan used Microsoft tools such as SharePoint, OneDrive, Outlook/Graph API, Azure Entra, Azure AI Document Intelligence, and Copilot Studio. During implementation, we hit account and permission limits with UTS/student accounts, especially around Graph API access and Copilot Studio credits.

Following supervisor feedback, the project was de-scoped into a local-first workflow using open-source tools. This lets the team demonstrate the core automation logic without waiting for enterprise permissions or paid cloud access.

## Features

- Gmail attachment ingestion using IMAP, with continuous polling and background processing
- Secure local credential handling through `.env`
- Local inbox/archive workflow
- Mock invoice generation for testing
- PDF text extraction with `pypdf`, and local OCR for scanned pages when Tesseract is installed
- Local LLM extraction through Ollama
- Optional local RAG using anonymised invoice examples
- Structured invoice fields stored in SQLite
- Confidence scoring and `Validated` / `NeedsReview` status
- Streamlit dashboard with KPIs, tables, filters, charts, and review actions

## Project Structure

| File / Folder | Purpose |
|---|---|
| `generate_mock_invoices.py` | Generates sample invoice PDFs for testing. |
| `email_listener.py` | Connects to Gmail via IMAP, downloads invoice-related attachments into `inbox/`, and while polling processes claimed batches in the background. |
| `.env.example` | Template for local email configuration. Copy this to `.env`. |
| `inbox/` | Local folder for incoming invoice files. Ignored by Git. |
| `main.py` | Core processing pipeline: reads files, extracts text, calls Ollama, validates confidence, saves records, and archives files. |
| `rag_retrieval.py` | Selects relevant anonymised invoice examples for optional prompt augmentation. |
| `rag-poc.md` | Describes the RAG design, evaluation method, results, and limitations. |
| `workflow_platform.db` | Local SQLite database. Ignored by Git. |
| `query_db.py` | Utility script for inspecting SQLite records. |
| `app.py` | Streamlit dashboard for viewing processed invoice results. |
| `archive/` | Stores processed files. Ignored by Git. |
| `test_ollama.py` | Debug script for testing Ollama extraction on a single PDF. |
| `Enterprise_AI_Workflow_Briefing.docx` | Project briefing document for team/report use. |

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/LALAJJ0302/invoice-local-llm-demo.git
cd invoice-local-llm-demo
```

### 2. Install Python dependencies

```bash
pip3 install -r requirements.txt
```

### 3. Install and prepare Ollama

Install Ollama from:

```text
https://ollama.com
```

Then pull the model:

```bash
ollama pull llama3.2
```

Make sure Ollama is running before using `main.py`.

Scanned PDFs need Tesseract as well. The Python packages only call that program, the same way `ollama` calls the Ollama server. Install it and leave it on `PATH`. Without it, a page with no text layer is retried and then moved to `inbox/.failed/retry_exhausted/`. The Docker image installs Tesseract itself.

- Windows: https://github.com/UB-Mannheim/tesseract/wiki
- macOS: `brew install tesseract`
- Debian/Ubuntu: `sudo apt-get install tesseract-ocr`

### 4. Configure Gmail credentials

`email_listener.py` reads mail settings from `.env`. Copy the example and fill in the Gmail lines:

```bash
cp .env.example .env
```

```env
EMAIL_HOST=imap.gmail.com
EMAIL_PORT=993
EMAIL_USER=your_email@gmail.com
EMAIL_PASSWORD=your_gmail_app_password
ATTACHMENT_DIR=./inbox
```

| Variable | Required | What to put |
|---|---|---|
| `EMAIL_HOST` | No | `imap.gmail.com`. This is the default if the line is missing. |
| `EMAIL_PORT` | No | `993`, the IMAP SSL port. This is the default if the line is missing. |
| `EMAIL_USER` | Yes | The full Gmail address, such as `name@gmail.com`. |
| `EMAIL_PASSWORD` | Yes | A Gmail App Password. The normal Gmail password is rejected by IMAP. |
| `ATTACHMENT_DIR` | No | Folder for saved files. Defaults to `./inbox`, which is also where `main.py` reads from. |

The Jira lines further down `.env.example` are optional and are not used by the listener.

Create the App Password:

1. Turn on 2-Step Verification: https://myaccount.google.com/signinoptions/two-step-verification
2. Open https://myaccount.google.com/apppasswords and create one named `invoice demo`.
3. Copy the 16-character code into `EMAIL_PASSWORD`. Spaces in the code do not matter.
4. Google shows that code once. If it is lost, create a new App Password on the same page and replace `EMAIL_PASSWORD`. The old one can be revoked there after the new code works.

In Gmail, turn IMAP on: Settings, See all settings, Forwarding and POP/IMAP, Enable IMAP.

Do not commit `.env`, and do not share a screenshot that shows it. A school or work Google account will hide the App Passwords page when an admin has turned them off.

## How to Run

### Option A: Test with mock invoices

This is the path to use on a new machine. It needs no mail credentials and no Jira account,
and it was verified end to end on 2026-09-24 against a clean clone: it finishes with the
full test suite passing.

Generate sample invoices:

```bash
python3 generate_mock_invoices.py
```

Load the mock mailbox. **Do not skip this.** Without it every invoice is stored with no
covering email, the sender never appears on the dashboard, and one test fails. It is
idempotent and reads `evaluation/mock_mailbox.json`, which is in the repository:

```bash
python3 seed_mock_emails.py
```

Process the invoices. This is the step that creates `workflow_platform.db`, so it must run
before the dashboard shows anything. About 30 seconds for the four samples:

```bash
python3 main.py
```

Process the invoices with local RAG enabled if you want to test the RAG PoC:

```bash
python3 main.py --rag --rag-limit 1
```

RAG is optional. The standard command above runs the normal extraction pipeline. With
`--rag`, the pipeline first runs the normal extraction and deterministic validation. It
retrieves an anonymised example and retries only when fields or supporting evidence are
weak, and it keeps the retry only when its evidence-based validation rank is strictly
better. The evaluation command's `--rag` flag remains an always-RAG mode so controlled
baseline comparisons stay reproducible.

Run the larger 30-document extraction benchmark:

```bash
python3 evaluation/run_eval.py --dataset extended --model llama3.2:latest
python3 evaluation/run_eval.py --dataset extended --model llama3.2:latest --rag
python3 evaluation/run_eval.py --dataset extended --model llama3.2:latest --selective-rag
```

Check the setup worked:

```bash
python3 -m pytest tests/ -q
```

**The dashboard asks you to sign in.** The accounts are created automatically on first run.
Use `neo`, `luke` or `jj` with the password `changeme`; the first login makes you choose a new
one. It is a local prototype with no network exposure, which is the only reason a default
password like that is acceptable.

**The database is not in the repository**, because it is generated and git cannot merge it.
Before `main.py` has run, the dashboard opens and says "No documents have been processed
yet", which is correct rather than broken. There is no migration to run on a new machine
either: the schema is created on first use, and the scripts in `migrations/` only upgrade a
database that already exists.


Open the dashboard:

```bash
python3 -m streamlit run app.py
```

Then open:

```text
http://localhost:8501
```

### Option B: Run from Gmail intake

Finish the `.env` setup above first. The listener keeps polling the inbox until you stop it with Ctrl+C. It fetches unread matches, writes files, marks those messages read, and starts processing whatever is in `inbox/` in the background (the same pipeline as `python3 main.py`). It does not wait for that processing to finish before the next check. Files already claimed are not part of the next batch. `EMAIL_POLL_INTERVAL` is the wait in seconds (default 10). A single pass that only downloads is `python3 email_listener.py --once`.

1. Put a message in the Gmail Inbox with `invoice` in the subject and a document attached, or use one that is already there.
2. With Ollama already running, watch the inbox. New files are claimed and extracted in the background after each check:

```bash
python3 email_listener.py
```

3. Open the dashboard, or refresh it if it is already open. It reads the database on the next page load:

```bash
python3 -m streamlit run app.py
```

What `email_listener.py` does on that run, in order:

1. Logs in over IMAP with `EMAIL_USER` and `EMAIL_PASSWORD`.
2. Searches the Inbox for unread messages whose subject contains `invoice`. Mail that is already read is not fetched. A subject such as `Invoice 1042` or `Re: invoice for March` matches. `Receipt` or `Bill` does not.
3. Stores each match in SQLite: Message-ID, sender, subject, date, body, and attachment filenames.
4. Saves qualifying attachments into `ATTACHMENT_DIR` (`./inbox` by default).
5. Marks each successfully handled message as read, including one whose attachments were skipped. A message that could not be fetched or recorded stays unread so the next poll can try again.
6. Claims the files in `inbox/` (moves them to `inbox/.processing/`) and processes that batch in the background with the same pipeline as `python3 main.py`, then waits `EMAIL_POLL_INTERVAL` seconds (default 10) and checks again. A later check does not take files already claimed. A check that could not reach Gmail still claims files already saved. A file the pipeline does not archive is returned to `inbox/` when that batch finishes, so a later check can retry it.

`python3 email_listener.py --once` only downloads. `python3 main.py` is still how you process files dropped straight into `inbox/` without the listener. Ollama must be running either way. The dashboard does not update by itself: refresh the browser, or click in the app, to see the new rows.

A message or file is skipped when:

- It is outside the Inbox. Other labels and folders are not searched.
- The subject does not contain `invoice`.
- That Message-ID is already in the database. The listener records it once, skips its attachments on later runs, and still marks it read.
- The message has no file attachment. Inline images and signature files are ignored.
- The attachment is not `.pdf`, `.doc`, `.docx`, `.xls`, `.xlsx`, `.csv`, or `.txt`. A photo of an invoice is skipped here.
- The same file bytes were already saved for this email.
- The attachment has no content.

### Option C: Run everything in Docker

Requires Docker Desktop (or the Docker Engine + Compose plugin on Linux). No local Python,
Ollama, or `pip install` needed — everything is still local, just containerised.

```bash
cp .env.example .env        # fill in Gmail credentials if you want Option B inside Docker too
docker compose up -d ollama # starts Ollama, pulls llama3.2 (first run downloads ~2GB)
docker compose run --rm pipeline   # generates nothing by itself: drop PDFs into inbox/ first,
                                    # or run `docker compose run --rm listener` to fetch from Gmail
docker compose up app       # dashboard on http://localhost:8501
```

The whole project directory is bind-mounted into each container, so `workflow_platform.db`,
`inbox/`, and `archive/` are the exact same files a non-Docker run would use — nothing
Docker-specific to clean up afterwards. See `deployment-spec.md` for why it is built this
way, and `docker-compose.yml`'s header comment for the full command list.

## Useful Commands

| Task | Command |
|---|---|
| Generate mock invoices | `python3 generate_mock_invoices.py` |
| Load the mock mailbox | `python3 seed_mock_emails.py` |
| Watch Gmail and process new files | `python3 email_listener.py` (`--once` downloads once and does not process) |
| Process inbox files without the listener | `python3 main.py` |
| Inspect SQLite records | `python3 query_db.py` |
| Start dashboard | `python3 -m streamlit run app.py` |
| Run the tests | `python3 -m pytest tests/ -q` |

## Current Limitations

- A scanned PDF is read only when Tesseract is installed. A page that OCR cannot read is quarantined under `inbox/.failed/` and recorded in `ingestion_failures`.
- Some fields, especially `vendor_name` and `currency`, may need prompt refinement.
- SQLite currently appends new records each time the pipeline runs, so repeated tests may create duplicate records.
- Real email attachments may contain private information, so demo data should be cleaned before presentation.

## Planned Improvements

- Add duplicate detection for repeated attachments or invoice numbers.
- Add a `run_id` or `batch_id` field to separate test runs.
- Improve extraction prompt and fallback rules for vendor, currency, date, and total amount.
- Use mock or anonymised invoices for final presentation.

## Privacy Notes

Do not commit or share:

- `.env`
- Gmail App Password
- Real invoices or receipts
- `inbox/`
- `archive/`
- `workflow_platform.db`

These files are intentionally ignored by `.gitignore`.
