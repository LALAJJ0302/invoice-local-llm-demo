# Runtime image for the pipeline (main.py / email_listener.py) and the dashboard (app.py).
# Does NOT bundle Ollama -- that runs as its own service (docker-compose.yml), because it
# needs a multi-gigabyte model pulled once and cached in its own volume, not baked into an
# image every teammate rebuilds.

FROM python:3.12-slim

WORKDIR /app

# Installed in their own layer so `docker compose build` only re-runs pip when
# requirements.txt actually changes, not on every source edit.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# docker-compose.yml bind-mounts the whole project over this at runtime (so the container
# always sees current source and the same inbox/ archive/ workflow_platform.db files a
# bare-metal run would use), but these need to exist for a first `docker compose run`
# before anything has been generated yet.
RUN mkdir -p inbox archive

ENV PYTHONUNBUFFERED=1
# main.py prints box-drawing characters ("??") in its progress log. Debian's base locale
# is not UTF-8, and without this a print crashes with UnicodeEncodeError the same way it
# does in a plain cp1252 Windows console.
ENV PYTHONIOENCODING=utf-8

# No default CMD or ENTRYPOINT: docker-compose.yml sets one per service (pipeline /
# listener / app). Running this image directly with no command is not meaningful.
