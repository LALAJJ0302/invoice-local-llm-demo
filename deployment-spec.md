# Deployment Packaging — Spec

**Author:** Luke, leading this per the team's task split. Shared infrastructure, not any
one person's schema/extraction/dashboard lane, so no approval gate the way the other three
specs on this branch have — flagged here for visibility, not blocking.

## Goal

A teammate (or a marker) with Docker installed and nothing else can run the whole stack
without installing Python, Ollama, or any pinned dependency by hand. Still **local-only** —
`requirements-spec.md` rules out cloud deployment, and nothing here contradicts that: every
container runs on the same machine, `ollama` included.

## Why one Dockerfile, four services

One image (`Dockerfile`), reused by three of the four `docker-compose.yml` services
(`pipeline`, `listener`, `app`) with a different `command:`. The fourth (`ollama`) is the
official `ollama/ollama` image, not built here — bundling a multi-gigabyte model into an
image every teammate rebuilds on every `pip install` change would be its own kind of
disaster.

## Why the whole project is bind-mounted, not copied

`docker-compose.yml`'s `volumes: - .:/app` on every service that runs project code means
the container sees the exact same `workflow_platform.db`, `inbox/`, `archive/`, and source
files a bare-metal `python main.py` would. Two consequences, both intended:

1. **No named volume gymnastics for the SQLite file.** A single-file bind mount is fragile
   (Docker creates a directory instead of a file if the host path does not exist yet); bind
   mounting the whole directory sidesteps that entirely.
2. **Editing code does not require a rebuild.** `docker compose build` only has to re-run
   when `requirements.txt` changes; source edits take effect on the next `docker compose
   run` immediately, because it is reading the host's files.

The trade-off: this is a development/demo convenience, not an isolated production
artifact — the container is not hermetic, by design, matching the project's own "local
prototype" framing.

## Why `pipeline` and `listener` are one-shot, not daemons

`main.py` and `email_listener.py` already process what is there and exit — that is how they
behave outside Docker too. Making them `docker compose run --rm` invocations rather than
long-running services matches existing behaviour instead of inventing a new one (a polling
loop, a cron container) that the project has never had and this task did not ask for.

## Why `env_file: .env` and not variables inline in the compose file

Credentials (`EMAIL_PASSWORD`) must never be committed. `docker-compose.yml` is tracked;
`.env` is gitignored (unchanged) and `.env.example` is now tracked (previously it was
listed in `.gitignore` by mistake alongside `.env` itself — fixed as part of this change,
since the README already told people to `cp .env.example .env` for a file that could not
actually be committed).

## What was verified

- `docker-compose.yml` is valid YAML and matches Compose Spec syntax for `depends_on`
  health conditions, `env_file`, and named volumes (checked by inspection against the
  Compose Spec; Docker itself is not installed in this environment, so `docker compose
  config` could not be run here — see the "verify on a Docker-enabled machine" note below).
- The non-Docker path (`.venv`, `pip install -r requirements.txt`, `python main.py`) was
  run end-to-end on this machine as part of the other three tasks on this branch, and is
  unaffected by anything in this one: no file any other service depends on was moved or
  renamed.

**Not verified here, and should be before this is trusted:** an actual `docker compose up`
on a machine with Docker installed. Flagging honestly rather than claiming a test that did
not happen — this is the first thing to do in the end-to-end integration pass with Neo.

## What this does not do

- Does not add a reverse proxy, TLS, or any multi-machine deployment story —
  `requirements-spec.md` rules production/cloud deployment out of scope, and nothing here
  argues otherwise.
- Does not change `main.py`, `email_listener.py`, or `app.py` to accept CLI-configurable
  paths. They still hardcode `./inbox`, `./archive`, `workflow_platform.db` relative to the
  working directory, which is why the bind mount targets `/app` (the container's
  `WORKDIR`, matching those relative paths exactly) rather than a differently-named data
  directory.
