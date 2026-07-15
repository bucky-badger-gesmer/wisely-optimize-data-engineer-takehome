"""Adapter for data/wisely_api/*.json — one file per player, a `seasons[]`
array of per-season entries. Clean and uniform (verified): expect zero
rejections on the real data. The try/except below is defensive, not a hedge
against known mess."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from pydantic import ValidationError

from bball.models import PlayerSeasonRecord, Rejection, Source
from bball.normalize import canonical_name


class WiselyApiAdapter:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    def extract(self) -> Iterator[PlayerSeasonRecord | Rejection]:
        for path in sorted(self.data_dir.glob("*.json")):
            payload = json.loads(path.read_text())
            player_id = payload.get("player_id")
            name = payload.get("name")
            updated_at = payload.get("updated_at")
            position = payload.get("position")

            for season in payload.get("seasons", []):
                raw = {**{k: v for k, v in payload.items() if k != "seasons"}, **season}
                try:
                    yield PlayerSeasonRecord(
                        source=Source.WISELY_API,
                        source_key=str(player_id),
                        source_updated_at=updated_at,
                        full_name=name,
                        canonical_name=canonical_name(name),
                        position=position,
                        season=season.get("season"),
                        team=season.get("team"),
                        league=season.get("league"),
                        age=season.get("age"),
                        gp=season.get("games_played"),
                        min_pg=season.get("minutes_per_game"),
                        pts_pg=season.get("points_per_game"),
                        reb_pg=season.get("rebounds_per_game"),
                        usage_pct=season.get("usage_pct"),
                        ts_pct_api=season.get("true_shooting_pct"),
                        reb_pct=season.get("reb_pct"),
                        per=season.get("per"),
                        bpm=season.get("bpm"),
                    )
                except (ValidationError, ValueError, TypeError) as e:
                    yield Rejection(source=Source.WISELY_API, raw=raw, reason=str(e))
