# Research & Plan — Wisely Basketball Data Engineer Take-Home

## 1. What the task actually is

Ingest the same ~15 players from two sources (a clean-ish JSON "API" feed and a messy
RealGM CSV) into one normalized Postgres dataset. Graded on: schema design,
source-agnostic ingestion with provenance, idempotency, deliberate validation,
documented conflict resolution, and one computed stat (TS%) verified against the API.
Bonus: incremental ingest of a live game feed. Short writeup required.

**Grading emphasis is on thinking, not volume.** The README says twice not to
over-build — lean on the writeup for anything "next."

## 2. What's actually in the data (verified by inspection)

### `data/wisely_api/*.json` — 13 players
One file per player. Stable `player_id`, `updated_at`, `seasons[]` array with
per-season: `season`, `league`, `team`, `age`, `games_played`, `minutes_per_game`,
`points_per_game`, `rebounds_per_game`, plus **advanced** metrics
(`usage_pct`, `true_shooting_pct`, `reb_pct`, `per`, `bpm`). Note
`true_shooting_pct` is a fraction (0.569) — keep units consistent.

### `data/realgm/players.csv` — 71 rows, 12 players
Columns: `Player,Season,Age,Team,League,GP,MIN,PTS,FGM,FGA,FG%,3PM,3PA,3P%,FTM,FTA,FT%,REB,AST,STL,BLK,TOV`
Confirmed mess (every item below verified by script, not assumed):
- **No stable ID** — identity is (name, team, season).
- **Name format differences**: some are `"Last, First"` (`"Agravánis, Dimítrios"`,
  `"Cerro, Adrian del"`, `"Steinarsson, Ægir"`) vs API's `"Dimitrios Agravanis"`,
  `"Adrian del Cerro"`, `"Aegir Steinarsson"` — so matching needs both
  **comma-flip** and **diacritic folding** (Ægir→Aegir, á→a). Verified: with those
  two normalizations, all 10 overlapping players match deterministically.
- **Two minute formats**: most rows are decimal (`20.5`) but all Agravanis rows use
  `MM:SS` (`26:36`, `25:06`, …). The MIN parser must handle both.
- **One exact duplicate row** (Amarante 2021-22, rows 3–4) **and one near-duplicate**:
  Steinarsson 2015-16 appears twice with different PTS (11.4 vs 11.6, rows 36–37).
  So dedup needs a policy for conflicting same-key rows within a source (e.g., keep
  last, log both), not just exact-dupe removal.
- **3 empty cells**: Hernandez 2020-21 `3P%` (derivable from 3PM/3PA — recompute),
  Agravanis 2020-21 `Age`, del Cerro 2023-24 `AST`.
- **Cross-source conflicts confirmed**: mostly small REB/PTS rounding-level diffs,
  plus three big planted ones — Fotu 2020-21 (GP 18 vs 10, MIN/PTS/REB all differ),
  del Cerro 2019-20 (GP 10 vs 4, PTS 2.1 vs 6.0), Arroyo 2024-25 (PTS 6.8 vs 5.5).
  Agravanis 2022-23 disagrees on **team** (Panathinaikos vs Napoli Basket) and
  **league**; Tepic 2020-21 disagrees on age (34 vs 33); Hrycaniuk 2025-26 team
  differs only by a `" BC"` suffix.
- **The API has a planted error too**: del Cerro 2019-20 has `league: 'Valencia'`
  (a club name, not a league). So "API always wins" is not safe to assume blindly —
  conflict logging must be symmetric. The API is also *missing* del Cerro 2023-24,
  which only the CSV carries (a cross-source fill-in case).

### Source overlap
- In **both**: 10 players.
- **API-only**: Adrian Bowie (3718), Saulius Kuzminskas (9600), Hugo Bartolome (87934).
- **RealGM-only**: Edin Atic, Denys Lukashov.

### `data/live/snapshot_00..04.json` (bonus)
One game (`20260207-RCB-NGT`), 5 snapshots pregame→final. Each has `game_id`,
`status`, `period`, `clock`, scores, `updated_at`, and a `boxscore[]` of per-player
counting stats. Verified: fixed 10-player roster across all snapshots (no adds or
drops mid-game), `updated_at` strictly increasing, no stat ever decreases between
polls, and final pts are internally consistent with fgm/tpm/ftm. Home roster
player_ids match the API feed; the 5 away players (Northgate BC, ids 900011–900015)
appear in no other source — must create player records on the fly. Since the sample
data is clean, out-of-order/stale snapshot handling is proven by tests, not by the
data.

### `data/realgm/realgm_profile_*.html`
Reference only. **Skip parsing HTML** — the CSV is the RealGM source; mention HTML
parsing as a "next" item in the writeup.

## 3. Proposed schema (Postgres)

```
players
  player_id      serial PK (internal surrogate key — never a source's id)
  full_name      text (canonical display name)
  position       text null
  created_at     timestamptz

source_player_map            -- the identity seam
  source         text        -- 'wisely_api' | 'realgm' | 'live'
  source_key     text        -- API player_id, or normalized name for realgm
  player_id      FK players
  UNIQUE (source, source_key)

season_stats                 -- unified, resolved per player-season
  player_id, season  -> UNIQUE natural key
  team, league       -- resolvable ATTRIBUTES, not part of the key: the sources
                     -- disagree on team/league for the same season (Agravanis
                     -- 2022-23), so keying on team would split one season into
                     -- two rows instead of forcing reconciliation. Tradeoff:
                     -- true multi-team seasons would need a stint concept —
                     -- note in writeup, not needed for this data.
  gp, min_pg, pts_pg, reb_pg, ast_pg, ...
  fgm, fga, tpm, tpa, ftm, fta, stl, blk, tov      (box, from RealGM)
  usage_pct, ts_pct_api, per, bpm, reb_pct          (advanced, from API)
  ts_pct_computed  numeric   -- our TS% from box score
  updated_at       timestamptz -- row-level last-write ("last-updated" per record)
  numeric(6,3)-ish types; CHECK constraints (gp >= 0, pct in [0,1], fgm <= fga)

field-level provenance: field_sources jsonb on season_stats
  {"pts_pg": {"source": "realgm", "updated_at": ...}, ...}
  -- this is the "swap a single field's source" seam; README's "every record
  -- tracks (source, last-updated)" is satisfied at BOTH levels: per-field in
  -- field_sources, per-row via updated_at

indexes: the PK/UNIQUE constraints above (players PK, (source, source_key),
  (player_id, season), (game_id, player_id)) create every btree the query
  patterns need at this scale; add player_game_stats(player_id) for by-player
  game lookups. Say in the writeup that further indexes wait for real query
  patterns — README grades "keys, types, normalization, indexes" explicitly.

raw / staging layer — SKIPPED at this scale (the source files are the staging
  layer; re-runs re-read them). In production this becomes verbatim-landed
  raw tables with batch ids. Stated as a tradeoff in the writeup.

rejections
  source, raw_payload jsonb, reason, created_at
  -- "handled deliberately, not silently dropped"

-- Bonus:
games (game_id PK, status, period, clock, home/away, scores, updated_at)
player_game_stats (game_id, player_id) UNIQUE — upsert target for live feed
```

Design notes to defend in the writeup:
- **Internal surrogate key** + `source_player_map` is what makes sources swappable:
  adding a third source is just new map rows, no schema change.
- Key `season_stats` on (player, season); team/league are resolved fields with
  provenance like any stat, because the sources conflict on them.
- **Field-level provenance** (not just row-level) directly answers the "swap a single
  field's source" requirement. The `field_sources` jsonb is the simplest viable
  version; a normalized `stat_provenance` table is the production upgrade if
  per-field history/querying is ever needed.

## 4. Pipeline architecture (the seam)

```
extract (per-source adapter) -> normalize (common intermediate model)
  -> match identity -> resolve conflicts (config-driven) -> upsert -> validate
```

- **Adapter interface**: each source implements `extract() -> Iterator[PlayerSeasonRecord]`
  where `PlayerSeasonRecord` is a shared dataclass/pydantic model (canonical field
  names, canonical units — e.g., TS% always a 0–1 fraction).
- **Identity resolution**: normalize names (comma-flip "Last, First" → "First Last",
  Unicode NFKD fold diacritics, casefold, strip), then match RealGM rows to players
  via `source_player_map`. New unmatched player → create `players` row + map entry.
- **Conflict resolution — config, not code**: a source-priority map per field, plus
  a per-(player, field) override table — so "swap a player's source" (README's exact
  ask) is one config entry, and "swap a single field's source" is one line in
  `FIELD_PRIORITY`. Neither touches schema or downstream queries. Field groups:
  - Box-score fields (PTS, FGM/FGA, …): **RealGM wins** (API doesn't carry them).
  - Advanced metrics (TS%, PER, BPM, usage): **API wins**.
  - Overlapping fields (GP, MIN, PPG, RPG, age, team, league): **API wins** (stable
    id, cleaner feed), tiebreak by latest `updated_at`; but **log every
    disagreement** to a `conflicts` report/table so the decision is auditable.
    That log *is* the documented reconciliation deliverable. Caveat (verified):
    the API has its own planted error (`league='Valencia'`), so the writeup should
    frame "API wins" as a default priority, with the conflict log as the mechanism
    a human/rule would use to override per field — which the field-level
    provenance design supports.
  - Fill-in: a season present in only one source (del Cerro 2023-24 is CSV-only)
    takes that source wholesale — no conflict, provenance records it.
- **Idempotency**: `INSERT ... ON CONFLICT (natural key) DO UPDATE`, guarded by
  `WHERE excluded.source_updated_at >= current.source_updated_at` where applicable.
  Re-running the whole pipeline must be a no-op diff. Dedupe exact CSV duplicates
  during normalize (keep one, count it in the run report).

## 5. TS% computation & verification (requirement 6)

- `ts_computed = PTS / (2 * (FGA + 0.44 * FTA))` from RealGM per-game values.
- Guard: FGA+FTA == 0 → NULL, not divide-by-zero; missing inputs → NULL + rejection note.
- Verification report: for each player-season with both values, show
  `ts_computed`, `api.true_shooting_pct`, delta. **Verified against the actual
  data (M5, computed and confirmed via `bball report ts`)**: 51 of 55 comparable rows
  land within ±0.013 (pure rounding noise from the CSV's 1-decimal per-game values);
  exactly **four** exceed the 0.02 tolerance — Fotu 2020-21 (+0.122), Arroyo 2024-25
  (+0.128), del Cerro 2019-20 (−0.049), and **Agravanis 2022-23 (+0.107)**. The first
  three are planted box-score corruptions (same-team season, tampered numbers). The
  fourth is a *different kind* of conflict: the sources describe two apparent stints
  of the same season (API: Napoli Basket/Lega A/5 GP; RealGM: Panathinaikos/HEBA A1/
  14 GP) — a genuine team/league disagreement, not corrupted data, and it directly
  motivates the schema's "team as attribute, not key" tradeoff (§3): a true multi-stint
  season would need a stint concept this schema doesn't have. So the TS% check catches
  *two* distinct failure modes — planted corruption and legitimate multi-source
  ambiguity — which is the stronger writeup story than a single clean 3-for-3.
- Tolerance: |delta| ≤ 0.02 → OK, else flag. Cleanly separates rounding noise from
  real disagreement on this data, and is a reusable cross-source validation pattern.

## 6. Validation & tests (what matters, not everything)

Validation at normalize time: required fields present, numeric parses, ranges
(percentages 0–100 or 0–1 consistently, GP > 0), FGM ≤ FGA etc. Bad row → `rejections`
with reason, pipeline continues.

Tests (pytest), prioritized:
1. **Name normalization/matching** — "Agravánis, Dimítrios" matches "Dimitrios
   Agravanis"; "Steinarsson, Ægir" matches "Aegir Steinarsson"; "Cerro, Adrian del"
   → "Adrian del Cerro" (multi-token surname — the tricky one).
2. **TS% math** — known inputs → known output; zero-attempt and missing-input edges.
3. **Idempotency** — run twice, assert row counts and contents unchanged.
4. **Conflict resolution** — given two records disagreeing, right source wins and
   the conflict is logged.
5. **Duplicate CSV rows** — exact dupe (Amarante 2021-22) collapses to one; the
   near-dupe (Steinarsson 2015-16, PTS 11.4 vs 11.6) resolves by policy and logs.
6. **MIN parsing** — decimal (`20.5`) and `MM:SS` (`26:36` → 26.6) both parse.
7. (Bonus) **Live upsert** — replaying snapshots 0–4 yields one row per player-game
   with final stats; out-of-order/stale snapshot doesn't regress data.

Use a real Postgres via `docker compose` (or testcontainers); SQLite would dodge the
`ON CONFLICT`/type behavior we're being graded on.

## 7. Bonus — live feed (worth doing; README says "the real thing we'd run")

Small `ingest_live(snapshot)` function: upsert `games` on `game_id`, upsert
`player_game_stats` on `(game_id, player_id)` with `updated_at` monotonic guard.
Unknown away-roster player → create player + `source_player_map(source='live')`
entry, so the seam handles a third source for free — nice proof the design works.
CLI: `uv run bball ingest live data/live/snapshot_03.json` (simulates a poll).

Skip the Django endpoint unless time is abundant; a SQL view (`v_player_seasons`)
plus a note is enough.

## 8. Proposed stack & repo layout

- Python 3.11+ managed with **uv** (`pyproject.toml` + committed `uv.lock`; reviewer
  reproduces the env with `uv sync`, runs everything via `uv run bball ...`).
- psycopg 3 + plain SQL, pydantic for the intermediate model, pytest,
  docker-compose with Postgres 16. Single `schema.sql` migration (no Alembic
  ceremony at this scale — say so in the writeup).
- Canonical repo layout, milestones, and DDL live in **implementation-plan.md**.

## 9. Order of work (fits the 3-day window)

1. **Day 1 AM** — docker-compose + `schema.sql`; adapters emitting the common model;
   name normalization with the three hard cases as tests written first.
2. **Day 1 PM** — identity resolution, conflict policy, idempotent upserts;
   ingest both sources end-to-end.
3. **Day 2 AM** — TS% computation + verification report; rejections/conflict logging;
   round out tests.
4. **Day 2 PM** — bonus live ingest (it reuses the seam, should be cheap).
5. **Day 3** — writeup: schema rationale, seam, reconciliation table, test rationale,
   next advanced stat (**eFG%** — trivially validatable from the same box score;
   or PER, validated against the API's `per`), productionization (Airflow/Dagster
   schedule per source, freshness SLAs keyed on `updated_at`, row-count +
   cross-source-delta monitoring, dead-letter queue = `rejections`, scale via
   partitioning `player_game_stats` by season/date). Polish, zip/push.

## 10. Deliberate scope cuts (state them in the writeup)

- No HTML scraping (CSV is the RealGM input; describe the scraper as an adapter drop-in).
- No Django endpoint (SQL view instead).
- No orchestrator — a CLI + writeup section on how it would run under Dagster/Airflow.
- Name matching is deterministic normalization, not fuzzy matching — sufficient here;
  note trigram/embedding matching as the production path for real rosters.
