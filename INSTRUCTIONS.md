# Run From Scratch — Nuke Postgres → Ingest → Serve via Django API

A clean, end-to-end run: destroy the existing Postgres container (and its
data), bring up a fresh one, load both sources, and expose the resolved data
over the Django read endpoints.

**Run everything from the repo root:**
`/Users/aarongesmer/Documents/wisely-optimize-data-engineer-takehome`
(The CLI defaults `--data-dir` to `./data` and finds `schema.sql` at the root.)

---

## Option A: One-command Docker run

The compose file also defines a `web` service that containerizes the Django
app. It waits for Postgres to be healthy, applies the schema, ingests both
sources (both steps are idempotent), and serves the API:

```bash
docker compose up --build          # postgres + schema + ingest + Django on :8000
curl localhost:8000/players/       # in another terminal
```

Two connection contexts to be aware of: the `web` container reaches Postgres
at `db:5432` on the compose network (via `DATABASE_URL`), while host-run tools
(`uv run bball ...`, pytest) still use `localhost:5455`. Both work at the same
time.

The steps below are the host-run alternative (Option B) — the original
workflow, still fully supported.

---

## 1. Nuke the existing container + data

```bash
docker compose down -v
```

There is no named volume in `docker-compose.yml`, so the DB is throwaway —
`docker compose down` alone already wipes all data. The `-v` is
belt-and-suspenders.

## 2. Start a fresh Postgres and wait for it to be healthy

```bash
docker compose up -d db    # just the database — the app runs on the host below
docker compose ps          # wait until the db service shows "healthy"
```

Postgres publishes on **host port 5455**
(`postgresql://postgres:postgres@localhost:5455/bball`). The compose
healthcheck (`pg_isready`) takes a few seconds — don't continue until
`docker compose ps` reports healthy.

## 3. Install deps + create the schema

```bash
uv sync                    # install psycopg, pydantic, django, pytest from uv.lock
uv run bball init-db       # apply schema.sql (idempotent; every object is IF NOT EXISTS)
```

## 4. Ingest both sources

```bash
uv run bball ingest all    # extract -> resolve -> merge -> upsert
uv run bball ingest all    # OPTIONAL: run again to prove idempotency (zero changes)
```

Expected: `players=15`, one row per player-season, conflicts + rejections
logged. Optionally load the live game too:

```bash
uv run bball ingest live --replay data/live/
```

## 5. Sanity-check the data (optional but recommended)

```bash
uv run bball report ts         # TS% verification — expect exactly 4 flags
uv run bball report conflicts  # the reconciliation log
```

## 6. Serve the Django read API

```bash
uv run python manage.py runserver     # serves on http://localhost:8000
```

In another terminal:

```bash
curl localhost:8000/players/                 # roster: 15 players + season counts
curl localhost:8000/players/3/seasons/       # del Cerro: resolved seasons + provenance + both TS% columns
```

---

## Verification checklist

- `docker compose ps` shows the `db` service **healthy** on port 5455.
- `uv run bball ingest all` prints `players=15`.
- A second `ingest all` produces the same counts (idempotent).
- `curl localhost:8000/players/` returns a JSON array of 15 players.
- `curl localhost:8000/players/3/seasons/` returns resolved rows including
  `field_sources`, `ts_pct_api`, and `ts_pct_computed`.
- An unknown id (e.g. `/players/99999/seasons/`) returns HTTP 404.

---

## Notes / alternatives

- **Faster reset without Docker:** if the container is already up and you just
  want clean data, `uv run bball init-db --reset` drops and recreates the
  schema in place — no `docker compose down/up` needed.
- **Port already in use:** if `docker compose up` fails on port 5455, an old
  container or a local Postgres is holding it — run `docker compose down`
  first, or check `docker ps`.
- Run all `bball` / `manage.py` commands from the repo root so `./data` and
  `schema.sql` resolve correctly.
