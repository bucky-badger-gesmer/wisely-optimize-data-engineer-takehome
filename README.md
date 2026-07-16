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

## Writeup

### 1. Schema
Seven tables in total: `players` and `season_stats` hold the resolved data,
`source_player_map` handles identity, `rejections` and `conflicts` are the
audit trail, and `games` plus `player_game_stats` back the live feed. Four
decisions drove the design:

- Every player gets an internal ID that I generate and own. The API has its own player IDs and RealGM only has names, so a `source_player_map` table translates each source's identifier to mine. That means sources can be added or dropped without touching the data anyone queries.
- `season_stats` is keyed on player and season, not team. The data forced this: the sources sometimes disagree on team for the same season. In 2022-23, the Wisely API says Dimitrios Agravanis played for Napoli Basket while RealGM says Panathinaikos. Keying on team would split one season into two rows. The trade-off is that a true multi-team season would need a "stint" column added later.
- Each `season_stats` row carries a `field_sources` column recording where every value came from and when. That makes it possible to swap the source of a single field without changing the schema.
- Two audit tables:
  - `rejections` stores any row that couldn't be loaded, with the raw payload and the reason.
  - `conflicts` stores every cross-source disagreement, both values, and which won.
  - CHECK constraints guard the basics: age between 14 and 60, percentages between 0 and 1, and FGM never greater than FGA.

### 2. How the source-swap seam works
Three layers, each swappable on its own:

- Adapters: each source has a translator. The API speaks JSON and RealGM is a CSV, so each has its own logic for converting raw files into one standard record shape (`PlayerSeasonRecord`). Adding a source means writing one new adapter; nothing else changes.
- Identity map: the `source_player_map` table says who's who. The API sends player ID 104461, RealGM sends "juani marcos", and the map ties both to the same internal player.
- Priority config: the "who wins" list (`FIELD_PRIORITY` in `src/bball/resolve.py`), settable per field. Right now RealGM owns most box score stats and the API owns the advanced metrics.

### 3. How I resolved conflicts
I split the fields into three categories. RealGM is the primary source for box score stats (shooting splits, assists, steals, blocks, turnovers) because the API doesn't provide those. The API is the primary source for advanced metrics (usage rate, TS%, PER, BPM) because RealGM doesn't include them. For fields both sources provide, like team, league, age, games played, and per-game minutes, points, and rebounds, the API wins by default and RealGM fills in only when the API is missing a season. Every disagreement lands in the `conflicts` table with both values and the winner, and the per-field outcome is stamped into `season_stats.field_sources`.

### 4. What I tested, and why
I focused on the places where a mistake would be easiest to miss:

- Name matching: three hard real-world cases, including the Æ character that standard Unicode accent stripping doesn't handle.
- Minute parsing: both decimal and MM:SS formats (one player's data actually uses MM:SS), with invalid values rejected instead of turning into wrong numbers.
- TS% math: divide-by-zero and missing-input edge cases.
- Conflict resolution: the right source wins and the disagreement gets logged to the `conflicts` table.
- Duplicate handling with full accounting: all 71 CSV rows are traced. 69 load, one exact duplicate is skipped, one near-duplicate is logged to `rejections`.
- Idempotency: snapshot every table, rerun the whole pipeline, confirm nothing changed.
- Live replay: repeated polling doesn't create duplicate rows in `games` or `player_game_stats`, and replaying a stale snapshot after a game is final does nothing because of the `updated_at` safeguard.

### 5. Which advanced stat next, and how I'd validate it
eFG% (effective field goal percentage): `(FGM + 0.5 × 3PM) / FGA`. Every input is already sitting in the RealGM box score columns of `season_stats`, so there's no new data to source, just one new column on that table. It credits the extra point a three is worth, which makes it a more honest read on shooting than raw FG%, and it pairs naturally with the TS% column that already exists.

To validate it, I'd reuse the pattern TS% proved out: compute the number myself, compare it against an independent reconstruction, and flag anything that differs by more than rounding can explain. For eFG%, the independent check is the CSV's own FG% and 3P% columns, recomputed from makes and attempts. If the two disagree about the same player-season, something is wrong with the underlying makes or attempts, and the report should say so.

### 6. Productionizing, and what I'd do next

- Orchestration: Each source becomes a scheduled job in Dagster or Airflow: the API nightly, RealGM on its scrape cadence, the live feed on game nights. The adapters are already the job bodies, so this is wiring, not a rewrite. CI runs pytest against a Postgres service container on every change.
- Freshness: Right now the only freshness signal is the `updated_at` stamp on the data itself. Nothing records whether each source actually ran and succeeded. I'd add a small watermark table that every run updates with the source, finish time, rows loaded, and rejects. The SLA alert then falls out of one query: has any source been silent longer than N hours?
- Monitoring: Watch row-count deltas per run, alert when the rejection rate spikes (the `rejections` table is already a dead-letter queue), and turn the TS% cross-check into a standing monitor instead of a one-off report. Adapters should also assert the exact columns they expect, so when RealGM renames one the job fails loudly instead of half-parsing the file.
- Scale: Land raw payloads in staging tables with batch IDs. I skipped this at 71 rows, where the files themselves were the staging layer. Bulk load with COPY, partition `player_game_stats` by game date, and move the resolved layer into dbt. Staging also makes backfills honest: when the merge policy changes, replay the stored history through the new rules instead of hoping the sources still serve old data.
- Schema evolution: `schema.sql` only creates objects. It can bootstrap a database but never alter a live one, so production needs real migrations (Alembic or sqitch).
- Next with more time: parse RealGM's HTML pages as a drop-in adapter, add fuzzy name matching for rosters where strict normalization falls short, model multi-team seasons as stints (the Agravanis case is the motivation), and add auth and pagination to the read API.
- Where it's weak:
  - Name-only matching could merge two different players who share a name. The fix is corroborating attributes (birth year, position) plus a review queue.
  - The Agravanis row blends two sources into a season neither of them claims. Stints or a `disputed` flag would fix that.
  - Keeping the last row for near-duplicates is arbitrary, though it's deterministic and both payloads are logged.
  - The conflict tolerances (0.05 and 0.02) are tuned to this CSV's one-decimal precision and would need re-deriving for a new source.
  - `field_sources` stores only the latest winner, not history. A proper provenance table would fix that if per-field history ever needs querying.
  - A source can never retract data, because the upserts keep an old value when a new run supplies NULL. Fixing that means diffing full snapshots and writing explicit tombstones.