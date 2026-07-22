# Implementation Plan — Wisely Basketball Take-Home

Companion to `research.md` (data findings + design rationale live there; this file
is the build order). Every data quirk referenced below was verified by script
against the actual files — see research.md §2.

## 0. Stack decisions (locked)

| Decision | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | standard for this role; fast to test |
| Packaging / env | **uv** (`uv init` project, `uv.lock` committed) | reproducible env for the reviewer in one command; no venv/pip instructions in the README |
| DB | Postgres 16 via docker-compose | graded on real upsert/type behavior |
| DB access | `psycopg` 3 + plain SQL | small scale; shows SQL fluency; no ORM ceremony |
| Record model | `pydantic` v2 | validation at the seam, typed intermediate model |
| Tests | `pytest` against the dockerized Postgres | SQLite would dodge `ON CONFLICT` semantics |
| Migrations | single `schema.sql`, applied idempotently (`CREATE TABLE IF NOT EXISTS` / drop-recreate flag) | Alembic is overkill; say so in writeup |

Repo layout (target):

```
├── docker-compose.yml
├── schema.sql
├── pyproject.toml            # uv-managed; deps: psycopg[binary], pydantic; dev: pytest
├── uv.lock                   # committed — reviewer runs `uv sync` and matches exactly
├── data/                     # committed AS GIVEN, never edited — the fixtures the
│   ├── wisely_api/           #   e2e tests assert against (planted conflicts, dupes,
│   ├── realgm/               #   MM:SS rows); cleaning in-place would hide the mess
│   └── live/                 #   the pipeline is graded on handling
├── src/bball/
│   ├── models.py             # PlayerSeasonRecord, LiveBoxRecord, enums
│   ├── normalize.py          # canonical_name(), parse_minutes(), coercions
│   ├── adapters/
│   │   ├── base.py           # SourceAdapter protocol
│   │   ├── wisely_api.py
│   │   ├── realgm.py
│   │   └── live.py
│   ├── resolve.py            # identity resolution + FIELD_PRIORITY policy
│   ├── load.py               # upserts, rejections, conflict log
│   ├── report.py             # TS% verification + conflict report
│   └── cli.py                # `uv run bball ingest|report ...`
├── src/bball_web/            # M8 bonus: minimal Django read-only endpoints
│   ├── settings.py           #   over v_player_seasons (no ORM, no migrations)
│   ├── urls.py
│   └── views.py
├── manage.py
├── tests/
│   ├── conftest.py           # db fixture (fresh schema per test)
│   ├── test_normalize.py
│   ├── test_adapters.py
│   ├── test_resolve.py
│   ├── test_pipeline.py      # end-to-end + idempotency
│   ├── test_ts.py
│   ├── test_live.py
│   └── test_web.py           # M8: endpoint smoke tests via Django test client
├── README.md                 # the graded writeup
├── research.md
└── implementation-plan.md
```

---

## Milestone 1 — Environment + schema (~1h)

1. Project bootstrap with uv:
   ```bash
   uv init --package bball          # src layout, pyproject.toml
   uv add "psycopg[binary]" pydantic
   uv add --dev pytest
   ```
   Commit `uv.lock`. Pin `requires-python = ">=3.11"`. Define a console entry point
   `bball = "bball.cli:main"` in pyproject so commands run as `uv run bball ...`.
   Commit `data/` unmodified — the CLI defaults to `./data` (`--data-dir` to
   override), and the e2e tests read it directly as fixtures. All cleaning happens
   in code at ingest time, never by editing the files: the mess *is* the test data,
   and the expected-counts tests (71 = 69+1+1, exactly 3 TS flags) would break —
   correctly — if anyone "fixed" the files.
2. `docker-compose.yml`: postgres:16, port 5455 (avoid clashing with local pg),
   volume-less (throwaway), healthcheck. `DATABASE_URL` via env with default.
3. `schema.sql` — full DDL:

```sql
CREATE TABLE players (
  player_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  full_name   text NOT NULL,
  position    text,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE source_player_map (
  source      text   NOT NULL,          -- 'wisely_api' | 'realgm' | 'live'
  source_key  text   NOT NULL,          -- api id / normalized name / live id
  player_id   bigint NOT NULL REFERENCES players(player_id),
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (source, source_key)
);

CREATE TABLE season_stats (
  season_stat_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  player_id   bigint NOT NULL REFERENCES players(player_id),
  season      text   NOT NULL,           -- '2020-21'
  team        text,                      -- resolved attribute, NOT part of key
  league      text,
  age         smallint      CHECK (age BETWEEN 14 AND 60),
  gp          smallint      CHECK (gp >= 0),
  min_pg      numeric(5,2)  CHECK (min_pg >= 0),
  pts_pg      numeric(5,2), reb_pg numeric(5,2), ast_pg numeric(5,2),
  fgm_pg numeric(5,2), fga_pg numeric(5,2),
  tpm_pg numeric(5,2), tpa_pg numeric(5,2),
  ftm_pg numeric(5,2), fta_pg numeric(5,2),
  stl_pg numeric(5,2), blk_pg numeric(5,2), tov_pg numeric(5,2),
  usage_pct   numeric(5,2),
  ts_pct_api  numeric(5,4)  CHECK (ts_pct_api BETWEEN 0 AND 1),
  ts_pct_computed numeric(5,4),
  reb_pct     numeric(5,2), per numeric(5,2), bpm numeric(6,2),
  field_sources jsonb NOT NULL DEFAULT '{}'::jsonb,  -- {"pts_pg":{"source":"realgm","updated_at":...}}
  updated_at  timestamptz NOT NULL DEFAULT now(),    -- row-level "last-updated" (README req 2)
  UNIQUE (player_id, season),
  CHECK (fgm_pg IS NULL OR fga_pg IS NULL OR fgm_pg <= fga_pg),
  CHECK (tpm_pg IS NULL OR tpa_pg IS NULL OR tpm_pg <= tpa_pg),
  CHECK (ftm_pg IS NULL OR fta_pg IS NULL OR ftm_pg <= fta_pg)
);

CREATE TABLE rejections (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source text NOT NULL, raw jsonb NOT NULL,
  reason text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE conflicts (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  player_id bigint NOT NULL, season text NOT NULL, field text NOT NULL,
  winner_source text NOT NULL, winner_value text,
  loser_source text NOT NULL,  loser_value text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (player_id, season, field, winner_source, loser_source)  -- idempotent re-runs
);

-- bonus
CREATE TABLE games (
  game_id text PRIMARY KEY, status text NOT NULL,
  period smallint, clock text,
  home_team text, away_team text, home_score int, away_score int,
  updated_at timestamptz NOT NULL
);
CREATE TABLE player_game_stats (
  game_id text NOT NULL REFERENCES games(game_id),
  player_id bigint NOT NULL REFERENCES players(player_id),
  team text, min smallint, pts smallint,
  fgm smallint, fga smallint, tpm smallint, tpa smallint,
  ftm smallint, fta smallint, reb smallint, ast smallint,
  stl smallint, blk smallint, tov smallint, pf smallint,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (game_id, player_id)
);

-- Indexes: every PK/UNIQUE above already creates the btree the access patterns
-- need (player lookup, per-source identity, player-season upsert, game upsert).
-- The one addition: by-player queries across games hit the non-leading PK column.
CREATE INDEX idx_pgs_player ON player_game_stats (player_id);
```

Notes: percentages stored as 0–1 fractions everywhere (API convention); RealGM's
`FG%`-style columns are *dropped* at normalize time and recomputed from makes/attempts
(this also fills the empty `3P%` cell for free). No raw/staging tables — at 71 rows +
13 files the source files *are* the staging layer; state this tradeoff in the writeup.

**Done when:** `docker compose up -d && uv run bball init-db` creates all tables;
re-running is a no-op.

## Milestone 2 — Models + normalize (~1.5h, test-first)

`models.py`:

```python
class PlayerSeasonRecord(BaseModel):      # one per (source, player, season)
    source: Source                        # enum
    source_key: str                       # api player_id or canonical name
    source_updated_at: datetime | None    # API has it; realgm -> file mtime or None
    full_name: str                        # display form
    canonical_name: str                   # matching form
    season: str                           # r'^\d{4}-\d{2}$'
    team: str | None; league: str | None; age: int | None
    gp: int | None; min_pg: float | None; ...
    # advanced block optional (API only)
```

`normalize.py` — the two functions the CSV mess lives in:

- `canonical_name(raw)`: comma-flip (`"Cerro, Adrian del"` → `"Adrian del Cerro"`),
  NFKD + strip combining marks, `Æ/æ → Ae/ae` (NFKD alone does NOT decompose Æ —
  verified), casefold, whitespace collapse.
- `parse_minutes(raw)`: `"20.5"` → 20.5; `"26:36"` → 26.6 (MM + SS/60, round 1);
  `""`/None → None; anything else raises → rejection.

Tests first (these encode the verified data traps):
`test_normalize.py` — the three hard names, Æ folding, MM:SS conversion, empty cells
→ None, garbage minutes → error.

**Done when:** `uv run pytest tests/test_normalize.py` green.

## Milestone 3 — Adapters (~1.5h)

`base.py`: `class SourceAdapter(Protocol): def extract(self, path: Path) -> Iterator[PlayerSeasonRecord | Rejection]`

- `wisely_api.py`: glob `*.json`; one record per season entry; `source_key=str(player_id)`;
  carry `updated_at`. Keep API's TS% as `ts_pct_api` (already 0–1).
- `realgm.py`: DictReader; `source_key=canonical_name`; per-row try/except →
  `Rejection(reason=...)` instead of raising; drop `%` columns; **intra-source dedup**
  here: bucket by (canonical_name, season, team); identical rows → keep one silently;
  differing rows (Steinarsson 2015-16) → keep the *last* row, emit a rejection-style
  log entry `reason='intra-source near-duplicate; kept last'` with both payloads.
- `live.py` (bonus, milestone 7): parses one snapshot into game + `LiveBoxRecord`s.

`test_adapters.py`: API adapter yields 13 players / correct season counts; RealGM
yields 69 records (71 − 1 exact dupe − 1 near-dupe) + logged dupes; the 3 empty cells
land as None/recomputed, not rejections.

**Done when:** both adapters run over `data/` with zero unexplained rejections.

## Milestone 4 — Identity + conflict resolution (~2h, the core)

`resolve.py`:

1. `resolve_player(conn, source, source_key, full_name) -> player_id`:
   look up `source_player_map`; miss → try match by canonical name against existing
   players' canonical names (covers realgm↔api); still miss → insert new player.
   Always insert the map row. Display-name preference: API > realgm.
2. Field policy — config, not code:

```python
FIELD_PRIORITY = {
    # box score: realgm is the system of record (API doesn't carry splits)
    "fgm_pg": ["realgm"], "fga_pg": ["realgm"], ... ,
    # advanced: API only
    "usage_pct": ["wisely_api"], "ts_pct_api": ["wisely_api"], ...,
    # overlapping: API preferred, realgm fallback
    "gp": ["wisely_api", "realgm"], "min_pg": [...], "pts_pg": [...],
    "team": ["wisely_api", "realgm"], "league": [...], "age": [...],
}

# per-player escape hatch — README's "swap a player's source" is one entry here,
# "swap a single field's source" is one line above; neither touches schema/queries
OVERRIDES: dict[tuple[str, str], list[str]] = {
    # ("Adrian del Cerro", "league"): ["realgm", "wisely_api"],  # e.g. the API's 'Valencia' bug
}
```

3. `merge(records_for_player_season) -> (resolved_dict, field_sources, conflict_rows)`:
   per field, first source in priority that has a value wins; if a lower-priority
   source has a *different* value (numeric tolerance 0.05 for per-game floats, exact
   for text/int), append a conflict row. Single-source seasons (del Cerro 2023-24
   CSV-only; API-only players) merge trivially — no conflicts.

`load.py`: `INSERT ... ON CONFLICT (player_id, season) DO UPDATE SET ...` writing
resolved values + `field_sources` jsonb; conflicts upserted via their UNIQUE key.

`test_resolve.py`: priority winner chosen + conflict logged; fill-in from lone
source; tolerance (11.4 vs 11.44 no conflict, 6.8 vs 5.5 conflict); new player
created once, map rows for both sources point at same player_id.

**Done when:** `uv run bball ingest all` loads both sources; spot-check SQL shows
Agravanis 2022-23 as ONE row (team=Napoli Basket, conflict logged for team/league/gp).

## Milestone 5 — TS% + verification report (~1h)

`report.py`:

- During realgm merge, compute `ts_pct_computed = pts / (2*(fga + 0.44*fta))`
  (per-game values are fine — same ratio); denom 0 or missing input → NULL.
- `uv run bball report ts` prints per player-season: computed, api, delta,
  flag when |delta| > 0.02. Expected output (verified): **4 flags** — Fotu 2020-21
  (+0.122), Arroyo 2024-25 (+0.128), del Cerro 2019-20 (−0.049) each a planted
  box-score conflict, plus **Agravanis 2022-23 (+0.107)** — a genuine team/stint
  disagreement (API: Napoli Basket/Lega A/5 GP vs RealGM: Panathinaikos/HEBA A1/
  14 GP), not corrupted data. See research.md §5 for the full story.
- `uv run bball report conflicts` dumps the conflicts table grouped by player.

`test_ts.py`: exact formula on known inputs; zero-attempts → NULL; the tolerance
boundary; end-to-end assertion that exactly those 4 player-seasons flag on real data
(pins both the planted conflicts and the Agravanis stint conflict as a regression test).

**Done when:** both reports run and the TS story matches research.md §5.

## Milestone 6 — Idempotency + end-to-end tests (~1h)

`test_pipeline.py`:
1. Ingest all → assert player count (15: 13 API + Atic + Lukashov), season row
   count, no dupes.
2. **Ingest all twice** → identical table contents (compare row snapshots), conflicts
   table not double-populated (its UNIQUE key guarantees this).
3. Rejections/near-dupe log populated exactly as expected — nothing silently dropped:
   every CSV row is accounted for as loaded, deduped, or rejected (69 + 1 + 1 = 71).

**Done when:** full `uv run pytest` green; `ingest all` twice produces a zero diff.

## Milestone 7 — Bonus: live incremental ingest (~1.5h)

`live.py` adapter + `load.upsert_live(snapshot)`:

- Upsert `games` on `game_id` guarded by
  `WHERE excluded.updated_at >= games.updated_at` (stale/out-of-order poll = no-op).
- Per boxscore row: `resolve_player(source='live', source_key=str(player_id))` —
  home players map to existing API players (same id namespace, verified),
  away players (900011–900015) get created + mapped, proving the seam absorbs a
  third source with zero schema change.
- Upsert `player_game_stats` on (game_id, player_id) with the same monotonic
  `updated_at` guard.
- CLI: `uv run bball ingest live data/live/snapshot_02.json` (one poll) and
  `--replay data/live/` (all five in order).

`test_live.py`: replay 0→4 → 10 rows, final totals match snapshot_04; replaying
snapshot_02 *after* 04 changes nothing; unknown player creates exactly one
players row across repeated polls.

**Done when:** replay works, stale-poll test green. `CREATE VIEW v_player_seasons`
ships here (the unified read surface the M8 endpoint sits on).

## Milestone 8 — Bonus add-on: Django read endpoint (~45min)

The README offers "a tiny Django read endpoint or view" — the view exists (M7);
this adds the endpoint on top of it. Keep it deliberately tiny: read-only, no
ORM models, no migrations — Postgres already owns the schema.

1. `uv add django`. New package `src/bball_web/`: minimal `settings.py`
   (no DB config needed — reuse `bball.db.connect()`; `INSTALLED_APPS` empty
   beyond contenttypes-free basics), `urls.py`, `views.py`, thin `manage.py`
   at repo root or `uv run django-admin runserver --settings bball_web.settings`.
2. Two `JsonResponse` views, raw SQL against the view (dict rows):
   - `GET /players/` → id, name, position, season count
   - `GET /players/<player_id>/seasons/` → full `v_player_seasons` rows incl.
     `field_sources` provenance and both TS% columns (the whole point: the
     unified record, queryable)
   404 on unknown player; no writes, no auth, no pagination (say so in writeup).
3. `tests/test_web.py`: Django test client against the ingested DB — list
   returns 15 players, del Cerro seasons include `ts_pct_computed` +
   provenance keys, unknown id → 404.

**Done when:** `uv run bball ingest all` then
`uv run python manage.py runserver` serves both endpoints; `curl
localhost:8000/players/` lists 15 players with our internal surrogate
`player_id` (not a source's raw key — e.g. del Cerro is `3`, not the API's
`104461`); `curl localhost:8000/players/3/seasons/` shows resolved stats +
`field_sources`; `test_web.py` green.

## Milestone 9 — Writeup (README.md) (~1.5h)

Sections mapping 1:1 to what they grade: schema + why (key choice: (player, season),
team as attribute — cite Agravanis; types/CHECKs; index rationale: PK/UNIQUE btrees
cover the access patterns, one extra on player_game_stats(player_id), more waits for
real query patterns) · the seam (adapter protocol + FIELD_PRIORITY +
source_player_map; "swap one field's source" = edit one config line, provenance
already field-level) · reconciliation (paste the actual conflicts report; note the
API's own `league='Valencia'` error as why logging is symmetric) · TS% verification
(4 flags caught by tolerance check: 3 planted box-score conflicts + 1 genuine
team/stint disagreement) · what I tested and why · next stat:
**eFG%** `(FGM + 0.5*3PM)/FGA` — same inputs, validate against FG%/3P% recomputation;
or PER validated against API `per` · productionize: per-source scheduled DAGs
(Dagster/Airflow), freshness SLA on `source_updated_at`, monitors = row-count deltas
+ cross-source tolerance breaches (the TS pattern generalized) + rejection-rate
alarms, dead-letter = rejections table, scale = partition player_game_stats by
game date, COPY-based bulk loads, dbt for the resolved layer · scope cuts (HTML
scraper as future adapter, no orchestrator; Django endpoint kept read-only —
no auth/pagination/ORM, and why; runs host-side via `uv run python manage.py
runserver` rather than in docker-compose — the whole project is host-uv against
one dockerized Postgres, ingest is already a host-side prerequisite, and adding
a `web` compose service just to run a tiny read endpoint would be over-building
per the README; containerizing it is a one-line productionize note, not a
build) · known limitation: partial single-source re-ingest (`ingest realgm`
alone) is last-writer-wins for overlap fields — provenance tracks it, `ingest
all` restores priority-correct values; document rather than fix.

## Sequencing & budget (~10.75h total, fits day 1–2 with day 3 slack)

```
M1 env+schema ──► M2 normalize ──► M3 adapters ──► M4 resolve/load ──► M5 TS report
                                                        │
                                                        ├─► M6 idempotency tests
                                                        └─► M7 live bonus ──► M8 Django endpoint
M9 writeup last (pulls real report output into the doc)
```

Cut order if time runs short: M8 Django endpoint (the view already satisfies
"endpoint or view") → M7 entirely (README says bonus) → shrink M9 prose, never
M4/M6 (idempotency and reconciliation are graded core).

## Acceptance checklist (final pass before zipping)

- [ ] `data/` committed byte-identical to what was provided (diff against the original zip)
- [ ] `docker compose up -d && uv sync && uv run bball init-db && uv run bball ingest all` from clean checkout works
- [ ] second `ingest all` → zero row changes
- [ ] 15 players, one row per (player, season), Agravanis 2022-23 single row
- [ ] 71 CSV rows accounted for: 69 loaded + 1 exact dupe + 1 near-dupe logged
- [ ] `report ts` flags exactly Fotu 20-21 / Arroyo 24-25 / del Cerro 19-20 / Agravanis 22-23
- [ ] conflicts table populated incl. team/league/age conflicts
- [ ] live replay: 10 player-game rows, 5 new players, stale poll is a no-op
- [ ] Django endpoint: `/players/` lists 15, `/players/<id>/seasons/` returns
      resolved rows with `field_sources`, unknown id → 404
- [ ] `uv run pytest` green from clean checkout
- [ ] README writeup covers all six graded bullets + productionization
- [ ] notes/tradeoffs included in the repo (research.md + implementation-plan.md —
      README asks for them explicitly)
- [ ] repo zipped / pushed, emailed to alex@wiselyoptimize.com

## Requirements traceability (README → where it's covered)

| README requirement | Covered by |
|---|---|
| 1. Schema you design — keys, types, normalization, indexes | M1 DDL: surrogate key + `(player_id, season)` natural key, numeric/CHECK types, 0–1 pct normalization, index note + `idx_pgs_player` |
| 2. Source-agnostic + provenance (`source`, last-updated); swap a player's or a field's source without schema change | `source_player_map` + adapter protocol (M3); `field_sources` jsonb per field + `updated_at` per row (M1); `FIELD_PRIORITY` one-liner per field + `OVERRIDES` per player (M4) |
| 3. Idempotent — re-run updates, never duplicates | `ON CONFLICT DO UPDATE` everywhere + UNIQUE keys on conflicts table (M4); proven by run-twice test (M6) |
| 4. Validated + tested; bad rows deliberate, not dropped | pydantic at the seam + rejections table (M2/M3); 71 = 69 + 1 + 1 accounting test (M6) |
| 5. Reconciliation — decide and document | `FIELD_PRIORITY` policy + `conflicts` table (M4); report pasted into writeup (M9) |
| 6. TS% computed from RealGM, verified vs API, differences explained | M5: formula + NULL guards, 0.02-tolerance report, 4 flags (3 planted conflicts + 1 genuine team/stint disagreement) pinned by regression test |
| Bonus: live incremental ingest (upsert, advance on `updated_at`, unseen players) | M7: monotonic-guard upserts, away roster auto-created through the same seam |
| Bonus add-on: Django read endpoint or view | both: `v_player_seasons` view (M7) + read-only Django endpoints over it (M8) |
| Writeup: schema · seam · conflicts · tests · next stat + validation · productionize · what's next | M9, section-per-bullet |
| Submit: repo/zip + notes/tradeoffs, email | this checklist |
