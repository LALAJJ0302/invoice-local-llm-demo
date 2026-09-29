# 4. Security and Privacy

Section 2.5 argues that local inference has a privacy case independent of why this project
ended up there. This section is narrower and answers a question about this system rather than
about the approach: what, exactly, leaves the machine, and what protects the things that stay
on it.

The answers below are read off the code rather than off the design. Where the honest answer is
that something is not protected, it is stated rather than deferred to future work.

## 4.1 Two outbound calls in the whole codebase

Requirement NFR-1 says no document content leaves the machine. That is a claim about every line
of the system, so it was checked against every line rather than argued from the architecture
diagram. Searching the source for any outbound network primitive returns two results, both
outside the extraction path:

| Call | File | What it is |
|---|---|---|
| `imaplib.IMAP4_SSL` | `email_listener.py:99` | Reads mail from Gmail over IMAP |
| `urllib.request.urlopen` | `jira_client.py:227` | Creates and transitions a Jira issue |

Nothing else opens a socket. The model runs against an Ollama server on `localhost:11434`, the
database is a SQLite file on disk, and the dashboard is a local Streamlit process. **No invoice
PDF, no extracted text and no email body is sent anywhere by any part of this system.**

The two calls that do exist are inbound and outbound in opposite directions, and they deserve
separate treatment. IMAP brings content in. Jira is the only path by which anything derived
from a document goes out.

## 4.2 What Jira receives, field by field

The Jira integration is optional and off unless `JIRA_ENABLED` is set. When it is on, the issue
it creates carries exactly the following, assembled in `task_dispatch._build_summary` and
`task_dispatch._build_description`:

| Field | Example | Source |
|---|---|---|
| Summary | `Approval: invoice_001.pdf - Apex Cloud Solutions Pty Ltd` | Task type, file name, vendor name |
| Task type | `Approval` | The dispatch reason |
| Invoice ID | `3` | The database primary key |
| Data quality | `Validated (score 0.85)` | The gate's verdict and score |
| Document type | `Invoice` | The classifier |
| Total | `1,500.00` | The stored total, in dollars |
| Reason | One sentence naming the specific problem | The gate |
| Reviewed by | `Neo Pitayasiri` | The signed-in reviewer, when a human approved it |

**What is absent from that list is the point of it.** The line items do not go. The invoice date,
the invoice number and the currency do not go. The extracted text does not go, the PDF is never
uploaded as an attachment, and no part of the covering email, including its body and its sender,
is included. A person with access to the Jira project learns that a document from a named vendor
for a named amount needs a decision. They do not learn what was bought.

That is a narrower disclosure than the original Microsoft design would have made, where the
document itself was to live in SharePoint and the approval card was to render its contents in
Teams. It is narrower by consequence rather than by intent: the local design had no document
store to point at, so the task had to carry a reference instead of a copy.

**The vendor name and the amount are still commercially sensitive**, and this report does not
claim otherwise. The claim is bounded: what reaches a third-party service is eight fields, they
are enumerable, and the enumeration is in one function that a reviewer can read in a minute.

## 4.3 Credentials

Four secrets exist, and all four are read from the environment rather than from the source.

| Secret | Used by | Kind |
|---|---|---|
| `EMAIL_PASSWORD` | `email_listener.py` | A Gmail App Password, not the account password |
| `JIRA_API_TOKEN` | `jira_client.py` | An Atlassian API token |
| `JIRA_EMAIL` | `jira_client.py` | Identifies the API user |
| Dashboard passwords | `auth.py` | Hashed, see §4.4 |

`.env` is gitignored and `.env.example` is tracked in its place, carrying the variable names and
no values. The Gmail credential is an App Password specifically so that it can be revoked
without changing the account password and so that it cannot be used to sign in interactively.

`workflow_platform.db`, `inbox/` and `archive/` are all gitignored, so no invoice and no
extracted content has ever been committed. This was verified by searching the working tree
rather than assumed from the file: a document that reaches the repository is a permanent
disclosure, because removing it from a later commit does not remove it from the history.

## 4.4 Authentication, and a deliberate weakness

The dashboard requires a sign-in. Passwords are stored as `pbkdf2_sha256` with a 16-byte random
salt per user and 200,000 iterations, and verified in constant time. That is a reasonable
construction rather than a placeholder.

**The default password is `changeme`, hardcoded in `auth.DEFAULT_PASSWORD`.** Three accounts are
created with it on first run, and each is forced to change it at first sign-in. This is
documented here rather than quietly fixed, because the reasoning is the point.

It is acceptable in this system for two reasons that are both properties of the deployment
rather than of the code. The dashboard binds to localhost on a single machine with no network
exposure, and the accounts exist to attribute a decision to a person rather than to keep anyone
out. It would be unacceptable in any deployment where the second of those stopped being true,
and nothing in the code detects when that happens. **A control whose safety depends on a
property the code cannot check is a control that will fail silently when the property changes**,
which is the same failure shape §7.2 describes for the extraction heuristics.

## 4.5 What is not protected

Stated plainly, because a security section that lists only its controls is misleading.

- **The database is not encrypted at rest.** `workflow_platform.db` is a plain SQLite file.
  Anyone with the machine or a backup of it has every invoice the system has processed.
- **The archive is not encrypted either.** `archive/` holds the original PDFs.
- **Streamlit runs over plain HTTP.** On localhost that is not a finding. Over any network it
  would be, and nothing prevents someone starting it with `--server.address 0.0.0.0`.
- **There is no audit of reads.** The system records who approved what, and does not record who
  looked at what. An approver and a person browsing the queue are indistinguishable afterwards.
- **There is no data retention policy.** Nothing deletes an invoice, ever.
- **Under Docker the whole project directory is bind-mounted into every container**, so
  containerisation provides no isolation of the database or the archive. That is deliberate, and
  §8.3 explains why, but it means the container boundary is not a security boundary.

None of these is a defect in the sense of something that was meant to work and does not. They
are the scope of a proof of concept, and they are listed together so that nobody reads
"local-first" as a synonym for "secure".
