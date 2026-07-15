"""Identity resolution + conflict-driven merge — the core of the pipeline.

Two seams live here, both config not code (README's "swap a player's/field's
source without a schema change"):
- FIELD_PRIORITY: which source wins each field, per group (box vs advanced vs
  overlapping).
- OVERRIDES: a per-(player, field) escape hatch that beats FIELD_PRIORITY.
"""

from __future__ import annotations

from typing import Any

from bball.models import PlayerSeasonRecord, Source
from bball.stats import true_shooting

# Box-score splits: only RealGM carries these (the API has no splits).
_REALGM_ONLY = [
    "ast_pg", "fgm_pg", "fga_pg", "tpm_pg", "tpa_pg",
    "ftm_pg", "fta_pg", "stl_pg", "blk_pg", "tov_pg",
]
# Advanced metrics: only the API carries these.
_API_ONLY = ["usage_pct", "ts_pct_api", "reb_pct", "per", "bpm"]
# Both sources report these — API preferred (stable id, cleaner feed), RealGM
# fills in when the API is missing a season. Every disagreement is logged
# regardless of who wins (see merge() below) — that log is the reconciliation
# deliverable, not a claim the winner is always correct (the API has its own
# planted bug: del Cerro 2019-20 league='Valencia').
_OVERLAP = ["team", "league", "age", "gp", "min_pg", "pts_pg", "reb_pg"]

FIELD_PRIORITY: dict[str, list[Source]] = {
    **{f: [Source.REALGM] for f in _REALGM_ONLY},
    **{f: [Source.WISELY_API] for f in _API_ONLY},
    **{f: [Source.WISELY_API, Source.REALGM] for f in _OVERLAP},
}

# Per-(canonical_name, field) override — beats FIELD_PRIORITY when present.
# e.g. ("adrian del cerro", "league"): [Source.REALGM, Source.WISELY_API]
# would fix the API's 'Valencia' bug without touching schema or code above.
OVERRIDES: dict[tuple[str, str], list[Source]] = {}

# Per-game stat fields get a tolerance (rounding noise from 1-decimal CSV
# values); everything else (team/league/age/gp text-or-int) is exact.
_FLOAT_TOLERANCE_FIELDS = set(_REALGM_ONLY) | {"min_pg", "pts_pg", "reb_pg"}
_TOLERANCE = 0.05


def _differs(field: str, a: Any, b: Any) -> bool:
    if a is None or b is None:
        return False
    if field in _FLOAT_TOLERANCE_FIELDS:
        return abs(float(a) - float(b)) > _TOLERANCE
    return a != b


def merge(
    records: dict[Source, PlayerSeasonRecord],
    canonical_name: str,
) -> tuple[dict[str, Any], dict[str, dict], list[dict]]:
    """Pure merge of one player-season's per-source records.

    Returns (resolved, field_sources, conflicts):
    - resolved: {field: winning_value} for every field in FIELD_PRIORITY, plus
      ts_pct_computed (derived from RealGM's own box score, not conflict-merged
      like the other fields — see the bottom of this function)
    - field_sources: {field: {"source": ..., "updated_at": ...}} for the winner
    - conflicts: [{field, winner_source, winner_value, loser_source, loser_value}]
      for every OTHER source that had a differing non-None value, win or lose.
    """
    resolved: dict[str, Any] = {}
    field_sources: dict[str, dict] = {}
    conflicts: list[dict] = []

    for field, default_priority in FIELD_PRIORITY.items():
        priority = OVERRIDES.get((canonical_name, field), default_priority)

        winner_source = None
        winner_value = None
        for source in priority:
            rec = records.get(source)
            if rec is None:
                continue
            value = getattr(rec, field)
            if value is not None:
                winner_source, winner_value = source, value
                break

        resolved[field] = winner_value
        if winner_source is not None:
            rec = records[winner_source]
            field_sources[field] = {
                "source": winner_source.value,
                "updated_at": rec.source_updated_at.isoformat() if rec.source_updated_at else None,
            }

        if winner_source is None:
            continue
        for source, rec in records.items():
            if source == winner_source:
                continue
            loser_value = getattr(rec, field)
            if loser_value is not None and _differs(field, winner_value, loser_value):
                conflicts.append({
                    "field": field,
                    "winner_source": winner_source.value,
                    "winner_value": winner_value,
                    "loser_source": source.value,
                    "loser_value": loser_value,
                })

    # Derived from RealGM's own box score (PTS/FGA/FTA) — internal consistency
    # within one source, not a conflict-merged field, so no conflict logging.
    realgm_rec = records.get(Source.REALGM)
    resolved["ts_pct_computed"] = (
        true_shooting(realgm_rec.pts_pg, realgm_rec.fga_pg, realgm_rec.fta_pg)
        if realgm_rec is not None else None
    )

    return resolved, field_sources, conflicts


def resolve_player(
    cur,
    record: PlayerSeasonRecord | Any,
    index: dict[str, int],
) -> int:
    """Map a source record to an internal player_id, creating players /
    source_player_map rows as needed. `index` is a canonical_name -> player_id
    cache seeded from `players` at run start and kept up to date here, so
    RealGM rows match existing API players without a canonical column in the
    schema (a scale tradeoff — noted in the writeup).

    Only needs `record.source`, `.source_key`, `.canonical_name`, `.full_name`,
    `.position` — any record exposing those works, not just PlayerSeasonRecord.
    LiveBoxRecord (M7) reuses this unmodified, proving the identity seam
    absorbs a third source with zero code change."""
    cur.execute(
        "SELECT player_id FROM source_player_map WHERE source = %s AND source_key = %s",
        (record.source.value, record.source_key),
    )
    row = cur.fetchone()
    if row is not None:
        player_id = row[0]
    else:
        player_id = index.get(record.canonical_name)
        if player_id is None:
            cur.execute(
                "INSERT INTO players (full_name, position) VALUES (%s, %s) RETURNING player_id",
                (record.full_name, record.position),
            )
            player_id = cur.fetchone()[0]
            index[record.canonical_name] = player_id

        cur.execute(
            """
            INSERT INTO source_player_map (source, source_key, player_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (source, source_key) DO NOTHING
            """,
            (record.source.value, record.source_key, player_id),
        )

    # Display-name preference: API > realgm, applied regardless of ingest
    # order so a realgm-first run still ends up with the API's display form.
    if record.source == Source.WISELY_API:
        cur.execute(
            "UPDATE players SET full_name = %s, position = %s WHERE player_id = %s",
            (record.full_name, record.position, player_id),
        )

    return player_id
