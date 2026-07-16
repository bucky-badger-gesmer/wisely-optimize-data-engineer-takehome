# Wisely Optimize — Basketball Data Engineer — Take-Home (Round 2)

Multi-source ingestion of basketball player stats into a normalized Postgres
database, served by a small Django read API.

## Prerequisites

- **Docker** (with the `docker compose` plugin) — runs Postgres, and optionally
  the whole stack.
- **[uv](https://docs.astral.sh/uv/)** — manages Python and dependencies for
  the host-run path. You don't need to install Python yourself: uv picks up
  the pinned version (3.13, via `.python-version`) automatically on `uv sync`.

## Quick start — everything in Docker

```bash
docker compose up --build
```

That's it: Postgres starts, the schema is applied, both sources are ingested
(idempotent — safe to restart), and the Django API comes up on port 8000.
Then, in another terminal:

```bash
curl localhost:8000/players/
```

## Run on the host (database in Docker, app via uv)

```bash
docker compose up -d db              # Postgres only, on host port 5455
uv sync                              # install dependencies from uv.lock
uv run bball init-db                 # apply schema.sql (idempotent)
uv run bball ingest all              # load both sources
uv run python manage.py runserver    # Django API on http://localhost:8000
```

## Try it

```bash
curl localhost:8000/players/                 # roster: 15 players + season counts
curl localhost:8000/players/3/seasons/       # one player's resolved seasons + provenance

uv run bball report ts                       # TS% verification report
uv run bball report conflicts                # cross-source reconciliation log
uv run bball ingest live --replay data/live/ # bonus: replay the live game feed
```

## Tests

```bash
uv run pytest
```

Requires the `db` container to be up. Note: the test fixture resets the
database schema, so re-run `uv run bball ingest all` afterward if you want
the API demo data back.

## Teardown

```bash
docker compose down        # stop and remove the containers
```

There is no named volume — the database is throwaway, so `down` alone wipes
all data. Add `-v` for belt-and-suspenders. To stop just the web container
and keep Postgres running (e.g. to switch to the host-run workflow):

```bash
docker compose stop web    # frees port 8000 for a host-run `runserver`
```
