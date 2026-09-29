# 9. Deployment and System Requirements

This section says what it takes to run the system, on what hardware it was measured, and what
the packaging does and does not provide. `requirements-spec.md` rules out cloud deployment, and
nothing here contradicts that: every route below runs entirely on one machine.

## 9.1 Three ways to run it

| Route | Needs | Who it is for |
|---|---|---|
| A: bare metal | Python 3.12+, a virtualenv, Ollama installed and serving | Development, and every measurement in this report |
| B: bare metal with mail | Route A plus Gmail credentials in `.env` | Reading real mail rather than the seeded mock mailbox |
| C: Docker Compose | Docker Desktop or Engine plus Compose | A teammate or a marker with no Python and no Ollama |

Route A is the one the report is measured on. It is documented in `CLAUDE.md` as a fixed
sequence, and the sequence is the deliverable rather than a suggestion: create the virtualenv,
install the pinned requirements, start Ollama, pull `llama3.2`, generate the mock invoices, seed
the mock mailbox, run `main.py`, then the test suite, then the dashboard.

**Two steps in that sequence are non-obvious and both have cost time.** The migrations must not
be run on a new machine: `StorageManager.__init__` creates the schema at the current version on
first use, and the migrations exist to upgrade a database that already exists. And
`seed_mock_emails.py` is not optional, despite looking like a convenience: without it every
invoice stores a null email reference, the covering-email chip never renders, and a screen test
fails. It was missing from the documented sequence until 24 September 2026.

Route C is four services from two images. `ollama/ollama` runs the model server unchanged, and
one `Dockerfile` built from `python:3.12-slim` is reused by `pipeline`, `listener` and `app`
with a different command each. `pipeline` and `listener` are one-shot `docker compose run`
invocations rather than daemons, which matches how `main.py` and `email_listener.py` behave
outside Docker: they process what is there and exit.

## 9.2 Measured system requirements

Every figure in this report was produced on one machine, and it is named here so that the
numbers can be read against something.

| | |
|---|---|
| Machine | Apple M4, 24 GB unified memory, arm64 |
| Python | 3.13.5 in the project virtualenv |
| Model | `llama3.2:latest`, 2.0 GB on disk, 3B class |
| Model server | Ollama on `localhost:11434` |
| Virtualenv | 499 MB installed |
| Database | 204 KB after three invoices and a seeded mailbox |
| Extraction latency | 4.088 s mean per document, measured across three runs in §7.3 |
| Full pipeline | Three documents processed and stored in about 30 seconds |

**The memory figure is the one that constrains the design and it is not the 24 GB.** A 3B model
at the quantisation Ollama ships needs roughly 2 to 3 GB resident, which is why §8.6 could
shortlist six models against this machine and measure five of them. A 14B model is 9 GB on disk
and still runs here; nothing in this project requires the larger ones, and no result in this
report depends on having 24 GB.

**The database size is worth stating because of how small it is.** 204 KB for the entire
processed corpus. Nothing in the storage design is under pressure at this scale, and no claim in
this report about the schema has been tested at a scale where it would be.

## 9.3 The container boundary is not a security boundary

`docker-compose.yml` bind-mounts the whole project directory into every service that runs
project code. The container therefore sees the same `workflow_platform.db`, the same `inbox/`
and the same `archive/` a bare-metal run would.

That is deliberate and it buys two things. A single-file bind mount for the SQLite database is
fragile, because Docker creates a directory where the file is missing; mounting the parent
sidesteps it. And source edits take effect on the next run without a rebuild, so
`docker compose build` only has to re-run when `requirements.txt` changes.

The cost is that the container isolates nothing that matters. §5.5 lists this among the things
that are not protected, and the two statements are the same fact read from two directions:
containerisation here is a packaging convenience, not a sandbox. Anyone reading "it runs in
Docker" as a security property would be wrong, and the compose file is where they would find
out.

## 9.4 One discrepancy between the two routes

**The Dockerfile pins `python:3.12-slim`. Every measurement in this report was taken on Python
3.13.5.** The routes are therefore not running the same interpreter, and no result in this
report has been reproduced under Route C.

Nothing in the codebase is known to be version-sensitive, and the pinned requirements are
identical across both, so the expected difference is none. Expected is not measured. The honest
statement is that Route C is verified to start and Route A is verified to produce the numbers,
and that reconciling them is one `docker compose run --rm pipeline` followed by the evaluation
harness that nobody has yet run.

## 9.5 What has actually been verified, and by what

| Claim | Evidence |
|---|---|
| A clean clone reaches a working system by the documented sequence | Run end to end on a clean copy on 24 and 28 September 2026 |
| The test suite passes from that clean state | 629 passing at the time of writing |
| The dashboard renders and a decision can be recorded | Screenshots in `report/screenshots/`, dated |
| Route C starts | `docker compose up -d ollama` and `docker compose up app` documented in the README as Option C |
| Route C reproduces the report's numbers | **Not verified** |
| Any deployment beyond one developer machine | **Not attempted.** Out of scope per `requirements-spec.md` |

## 9.6 What this is not

It is not a production deployment story and the project has never claimed one. There is no
orchestration, no health check, no restart policy, no backup of the database beyond the `.bak`
copy migration 001 takes, no log aggregation, and no way to run two instances against the same
store. The dashboard binds to localhost and the security consequences of changing that are in
§5.5.

What the packaging does provide is reproducibility by a second person, which is the thing a
proof of concept actually needs: a marker with Docker installed can run the same pipeline the
report describes, and a teammate with Python can reproduce its numbers.
