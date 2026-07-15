"""Adapter for data/realgm/players.csv — the messy source. Verified traps
(research.md §2): "Last, First" names with diacritics, MM:SS minutes for all
Agravanis rows, one exact duplicate row (Amarante 2021-22) and one near-
duplicate (Steinarsson 2015-16, differing PTS), three empty cells, and `%`
columns dropped in favor of recomputing from makes/attempts (M5).

Nothing is silently dropped: every collapsed row — exact dupe or near-dupe —
is logged as a Rejection with a distinguishing reason, so `rejections` plus
emitted records always accounts for all 71 CSV rows.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Iterator

from pydantic import ValidationError

from bball.models import PlayerSeasonRecord, Rejection, Source
from bball.normalize import (
    canonical_name,
    display_name,
    parse_minutes,
    parse_optional_float,
    parse_optional_int,
)

# %-columns are dropped: recomputed from makes/attempts at merge time (M5),
# which also fills the occasional empty %-cell for free.
_DROPPED_COLUMNS = {"FG%", "3P%", "FT%"}

_FLOAT_COLUMNS = {
    "PTS": "pts_pg", "FGM": "fgm_pg", "FGA": "fga_pg",
    "3PM": "tpm_pg", "3PA": "tpa_pg", "FTM": "ftm_pg", "FTA": "fta_pg",
    "REB": "reb_pg", "AST": "ast_pg", "STL": "stl_pg", "BLK": "blk_pg",
    "TOV": "tov_pg",
}


def _parse_row(row: dict) -> PlayerSeasonRecord:
    """Raises ValueError/ValidationError on any unparseable cell."""
    kwargs = {
        "source": Source.REALGM,
        "source_key": canonical_name(row["Player"]),
        "source_updated_at": None,
        "full_name": display_name(row["Player"]),
        "canonical_name": canonical_name(row["Player"]),
        "season": row["Season"],
        "team": row["Team"] or None,
        "league": row["League"] or None,
        "age": parse_optional_int(row["Age"]),
        "gp": parse_optional_int(row["GP"]),
        "min_pg": parse_minutes(row["MIN"]),
    }
    for col, field in _FLOAT_COLUMNS.items():
        kwargs[field] = parse_optional_float(row[col])
    return PlayerSeasonRecord(**kwargs)


class RealGMAdapter:
    def __init__(self, csv_path: Path):
        self.csv_path = Path(csv_path)

    def extract(self) -> Iterator[PlayerSeasonRecord | Rejection]:
        with open(self.csv_path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))

        buckets: dict[tuple, list[dict]] = defaultdict(list)
        for row in rows:
            key = (canonical_name(row["Player"]), row["Season"], row["Team"])
            buckets[key].append(row)

        for group in buckets.values():
            *dropped, kept = group  # keep the last row on same-key collisions

            for dropped_row in dropped:
                comparable = {k: v for k, v in dropped_row.items() if k not in _DROPPED_COLUMNS}
                kept_comparable = {k: v for k, v in kept.items() if k not in _DROPPED_COLUMNS}
                if comparable == kept_comparable:
                    reason = "intra-source exact duplicate"
                else:
                    reason = "intra-source near-duplicate; kept last"
                yield Rejection(source=Source.REALGM, raw=dropped_row, reason=reason)

            try:
                yield _parse_row(kept)
            except (ValidationError, ValueError, TypeError) as e:
                yield Rejection(source=Source.REALGM, raw=kept, reason=str(e))
