"""Orchestrates extract -> resolve -> merge -> upsert for one ingest run.

Idempotency story:
- players / source_player_map / season_stats / conflicts are all upserted via
  their UNIQUE keys — re-running never duplicates.
- rejections has no natural key (a rejected row isn't identifiable across
  runs), so it's fully re-derived per run: rows for the sources being
  (re-)ingested are deleted first, then repopulated from this run's rejects.
- A *partial* re-ingest (e.g. `ingest realgm` after `ingest all`) must not
  blank out fields owned by a source not being touched this run. The upsert
  uses COALESCE(new, existing) per column and jsonb `||` merge on
  field_sources, so untouched fields survive a partial re-run untouched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from psycopg.types.json import Jsonb

from bball.adapters.realgm import RealGMAdapter
from bball.adapters.wisely_api import WiselyApiAdapter
from bball.models import PlayerSeasonRecord, Rejection, Source
from bball.normalize import canonical_name
from bball.resolve import merge, resolve_player

# Column order for season_stats.
_COLUMNS = [
    "team", "league", "age", "gp", "min_pg", "pts_pg", "reb_pg",
    "ast_pg", "fgm_pg", "fga_pg", "tpm_pg", "tpa_pg", "ftm_pg", "fta_pg",
    "stl_pg", "blk_pg", "tov_pg",
    "usage_pct", "ts_pct_api", "ts_pct_computed", "reb_pct", "per", "bpm",
]

_SET_CLAUSE = ",\n              ".join(
    f"{c} = COALESCE(EXCLUDED.{c}, season_stats.{c})" for c in _COLUMNS
)

_UPSERT_SQL = f"""
    INSERT INTO season_stats (player_id, season, {", ".join(_COLUMNS)}, field_sources, updated_at)
    VALUES (%(player_id)s, %(season)s, {", ".join(f"%({c})s" for c in _COLUMNS)}, %(field_sources)s, now())
    ON CONFLICT (player_id, season) DO UPDATE SET
              {_SET_CLAUSE},
              field_sources = season_stats.field_sources || EXCLUDED.field_sources,
              updated_at = now()
"""

_CONFLICT_SQL = """
    INSERT INTO conflicts (player_id, season, field, winner_source, winner_value, loser_source, loser_value)
    VALUES (%(player_id)s, %(season)s, %(field)s, %(winner_source)s, %(winner_value)s,
            %(loser_source)s, %(loser_value)s)
    ON CONFLICT (player_id, season, field, winner_source, loser_source)
    DO UPDATE SET winner_value = EXCLUDED.winner_value, loser_value = EXCLUDED.loser_value
"""


def upsert_season_stats(cur, player_id: int, season: str, resolved: dict, field_sources: dict) -> None:
    params = {"player_id": player_id, "season": season, "field_sources": Jsonb(field_sources)}
    for c in _COLUMNS:
        params[c] = resolved.get(c)
    cur.execute(_UPSERT_SQL, params)


def insert_conflict(cur, player_id: int, season: str, conflict: dict) -> None:
    cur.execute(_CONFLICT_SQL, {
        "player_id": player_id,
        "season": season,
        "field": conflict["field"],
        "winner_source": conflict["winner_source"],
        "winner_value": str(conflict["winner_value"]),
        "loser_source": conflict["loser_source"],
        "loser_value": str(conflict["loser_value"]),
    })


def insert_rejection(cur, rejection: Rejection) -> None:
    cur.execute(
        "INSERT INTO rejections (source, raw, reason) VALUES (%s, %s, %s)",
        (rejection.source.value, Jsonb(rejection.raw), rejection.reason),
    )


_ADAPTER_ORDER = [Source.WISELY_API, Source.REALGM]  # API resolved first: display-name authority


def _build_adapter(source: Source, data_dir: Path):
    if source == Source.WISELY_API:
        return WiselyApiAdapter(data_dir / "wisely_api")
    if source == Source.REALGM:
        return RealGMAdapter(data_dir / "realgm" / "players.csv")
    raise ValueError(f"no adapter for source {source!r} (live is M7)")


def run_ingest(conn, sources: Iterable[Source], data_dir: Path) -> dict:
    sources = [s for s in _ADAPTER_ORDER if s in set(sources)]  # canonical processing order

    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM rejections WHERE source = ANY(%s)",
            ([s.value for s in sources],),
        )

        cur.execute("SELECT player_id, full_name FROM players")
        index = {canonical_name(name): pid for pid, name in cur.fetchall()}

        groups: dict[tuple[int, str], dict[Source, PlayerSeasonRecord]] = {}
        rejection_count = 0

        for source in sources:
            for item in _build_adapter(source, data_dir).extract():
                if isinstance(item, Rejection):
                    insert_rejection(cur, item)
                    rejection_count += 1
                    continue
                player_id = resolve_player(cur, item, index)
                groups.setdefault((player_id, item.season), {})[item.source] = item

        conflict_count = 0
        for (player_id, season), recs in groups.items():
            name = next(iter(recs.values())).canonical_name
            resolved, field_sources, conflicts = merge(recs, name)
            upsert_season_stats(cur, player_id, season, resolved, field_sources)
            for c in conflicts:
                insert_conflict(cur, player_id, season, c)
                conflict_count += 1

        cur.execute("SELECT count(*) FROM players")
        player_count = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM season_stats")
        season_stats_count = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM conflicts")
        total_conflicts = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM rejections")
        total_rejections = cur.fetchone()[0]

    conn.commit()

    return {
        "players": player_count,
        "season_stats": season_stats_count,
        "season_groups_this_run": len(groups),
        "conflicts_logged_this_run": conflict_count,
        "conflicts_total": total_conflicts,
        "rejections_this_run": rejection_count,
        "rejections_total": total_rejections,
    }
