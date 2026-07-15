-- Wisely basketball take-home — full schema.
-- Applied idempotently by `bball init-db` (every object is IF NOT EXISTS).
-- Percentages are stored as 0–1 fractions everywhere (the API convention).

CREATE TABLE IF NOT EXISTS players (
  player_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  full_name   text NOT NULL,
  position    text,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- The identity seam: maps each source's key to our internal surrogate player_id.
-- Adding a third source is just new rows here — no schema change.
CREATE TABLE IF NOT EXISTS source_player_map (
  source      text   NOT NULL,          -- 'wisely_api' | 'realgm' | 'live'
  source_key  text   NOT NULL,          -- api id / normalized name / live id
  player_id   bigint NOT NULL REFERENCES players(player_id),
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (source, source_key)
);

-- Unified, resolved stats per player-season. Keyed on (player_id, season);
-- team/league are resolved ATTRIBUTES, not part of the key, because sources
-- disagree on them for the same season (e.g. Agravanis 2022-23).
CREATE TABLE IF NOT EXISTS season_stats (
  season_stat_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  player_id   bigint NOT NULL REFERENCES players(player_id),
  season      text   NOT NULL,           -- '2020-21'
  team        text,
  league      text,
  age         smallint      CHECK (age BETWEEN 14 AND 60),
  gp          smallint      CHECK (gp >= 0),
  min_pg      numeric(5,2)  CHECK (min_pg >= 0),
  pts_pg numeric(5,2), reb_pg numeric(5,2), ast_pg numeric(5,2),
  fgm_pg numeric(5,2), fga_pg numeric(5,2),
  tpm_pg numeric(5,2), tpa_pg numeric(5,2),
  ftm_pg numeric(5,2), fta_pg numeric(5,2),
  stl_pg numeric(5,2), blk_pg numeric(5,2), tov_pg numeric(5,2),
  usage_pct   numeric(5,2),
  ts_pct_api  numeric(5,4)  CHECK (ts_pct_api BETWEEN 0 AND 1),
  ts_pct_computed numeric(5,4),
  reb_pct     numeric(5,2), per numeric(5,2), bpm numeric(6,2),
  -- field-level provenance: {"pts_pg":{"source":"realgm","updated_at":...}}
  field_sources jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at  timestamptz NOT NULL DEFAULT now(),   -- row-level last-updated
  UNIQUE (player_id, season),
  CHECK (fgm_pg IS NULL OR fga_pg IS NULL OR fgm_pg <= fga_pg),
  CHECK (tpm_pg IS NULL OR tpa_pg IS NULL OR tpm_pg <= tpa_pg),
  CHECK (ftm_pg IS NULL OR fta_pg IS NULL OR ftm_pg <= fta_pg)
);

-- Bad rows land here with a reason instead of being silently dropped.
CREATE TABLE IF NOT EXISTS rejections (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source text NOT NULL, raw jsonb NOT NULL,
  reason text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);

-- Auditable reconciliation log. The UNIQUE key makes re-runs idempotent.
CREATE TABLE IF NOT EXISTS conflicts (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  player_id bigint NOT NULL, season text NOT NULL, field text NOT NULL,
  winner_source text NOT NULL, winner_value text,
  loser_source text NOT NULL,  loser_value text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (player_id, season, field, winner_source, loser_source)
);

-- Bonus: live game feed.
CREATE TABLE IF NOT EXISTS games (
  game_id text PRIMARY KEY, status text NOT NULL,
  period smallint, clock text,
  home_team text, away_team text, home_score int, away_score int,
  updated_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS player_game_stats (
  game_id text NOT NULL REFERENCES games(game_id),
  player_id bigint NOT NULL REFERENCES players(player_id),
  team text, min smallint, pts smallint,
  fgm smallint, fga smallint, tpm smallint, tpa smallint,
  ftm smallint, fta smallint, reb smallint, ast smallint,
  stl smallint, blk smallint, tov smallint, pf smallint,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (game_id, player_id)
);

-- Every PK/UNIQUE above already creates the btree the access patterns need.
-- The one addition: by-player queries across games hit the non-leading PK column.
CREATE INDEX IF NOT EXISTS idx_pgs_player ON player_game_stats (player_id);
