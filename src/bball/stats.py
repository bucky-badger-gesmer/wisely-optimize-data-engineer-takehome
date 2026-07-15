"""Computed stats, verified against a source's own reported value.

True Shooting % is computed from RealGM's own box score (PTS/FGA/FTA) —
internal consistency within one source, not the API-preferred resolved
`pts_pg` — because that's what the verification report (report.py) checks
against the API's independently-reported `true_shooting_pct`.
"""

from __future__ import annotations


def true_shooting(pts: float | None, fga: float | None, fta: float | None) -> float | None:
    """TS% = PTS / (2 * (FGA + 0.44*FTA)). Missing inputs or zero attempts
    both yield None rather than raising or dividing by zero."""
    if pts is None or fga is None or fta is None:
        return None
    denom = 2 * (fga + 0.44 * fta)
    if denom == 0:
        return None
    return pts / denom
