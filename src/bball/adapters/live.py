"""Adapter for data/live/snapshot_*.json — one game, polled repeatedly.

Doesn't implement SourceAdapter (that protocol is per-source-file ->
PlayerSeasonRecord stream; a live snapshot is one game + its boxscore, a
different shape entirely). What it *does* reuse is resolve_player() —
LiveBoxRecord exposes the same identity attributes as PlayerSeasonRecord,
so a third source slots into the existing seam with zero code change.

Verified against the real data (research.md): fixed 10-player roster across
all 5 snapshots, updated_at strictly increasing, stats never decrease.
Home player_ids match the wisely_api feed (same numeric ids, but a
different (source, source_key) pair — matched via canonical_name, not by
assuming cross-source ids share a namespace); the 5 away players
(900011-900015) exist in no other source and get created on first sight.
"""

from __future__ import annotations

import json
from pathlib import Path

from bball.models import GameRecord, LiveBoxRecord, Source
from bball.normalize import canonical_name


def extract_snapshot(path: Path) -> tuple[GameRecord, list[LiveBoxRecord]]:
    payload = json.loads(path.read_text())

    game = GameRecord(
        game_id=payload["game_id"],
        status=payload["status"],
        period=payload.get("period"),
        clock=payload.get("clock"),
        home_team=payload.get("home_team"),
        away_team=payload.get("away_team"),
        home_score=payload.get("home_score"),
        away_score=payload.get("away_score"),
        updated_at=payload["updated_at"],
    )

    boxes = [
        LiveBoxRecord(
            source=Source.LIVE,
            source_key=str(p["player_id"]),
            full_name=p["name"],
            canonical_name=canonical_name(p["name"]),
            game_id=game.game_id,
            team=p.get("team"),
            min=p.get("min"),
            pts=p.get("pts"),
            fgm=p.get("fgm"),
            fga=p.get("fga"),
            tpm=p.get("tpm"),
            tpa=p.get("tpa"),
            ftm=p.get("ftm"),
            fta=p.get("fta"),
            reb=p.get("reb"),
            ast=p.get("ast"),
            stl=p.get("stl"),
            blk=p.get("blk"),
            tov=p.get("tov"),
            pf=p.get("pf"),
            updated_at=game.updated_at,
        )
        for p in payload.get("boxscore", [])
    ]

    return game, boxes
