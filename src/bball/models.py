"""The source-agnostic intermediate model.

Every adapter (M3: wisely_api, realgm, live) emits `PlayerSeasonRecord | Rejection`.
Everything downstream — identity resolution, conflict merge, upserts (M4) — only ever
sees this shape, never a source's raw format. Field names deliberately mirror the
`season_stats` columns in schema.sql so the M4 upsert is a near-direct mapping.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Source(str, Enum):
    WISELY_API = "wisely_api"
    REALGM = "realgm"
    LIVE = "live"


class PlayerSeasonRecord(BaseModel):
    """One (source, player, season) observation, already unit-normalized
    (percentages as 0-1 fractions, minutes as decimal) but not yet merged
    across sources."""

    model_config = ConfigDict(extra="forbid")

    # Identity / provenance
    source: Source
    source_key: str  # API: str(player_id); realgm: canonical_name
    source_updated_at: datetime | None = None

    # Names
    full_name: str  # display form
    canonical_name: str  # matching form (folded, comma-flipped)

    position: str | None = None  # API only

    season: str = Field(pattern=r"^\d{4}-\d{2}$")

    # Common to both sources
    team: str | None = None
    league: str | None = None
    age: int | None = None
    gp: int | None = None
    min_pg: float | None = None
    pts_pg: float | None = None
    reb_pg: float | None = None
    ast_pg: float | None = None

    # Box score splits — realgm only (API doesn't carry these)
    fgm_pg: float | None = None
    fga_pg: float | None = None
    tpm_pg: float | None = None
    tpa_pg: float | None = None
    ftm_pg: float | None = None
    fta_pg: float | None = None
    stl_pg: float | None = None
    blk_pg: float | None = None
    tov_pg: float | None = None

    # Advanced metrics — API only (realgm doesn't carry these)
    usage_pct: float | None = None
    ts_pct_api: float | None = None
    reb_pct: float | None = None
    per: float | None = None
    bpm: float | None = None


class Rejection(BaseModel):
    """A row/record a source adapter could not parse. Logged, never dropped
    silently — lands in the `rejections` table (M4/load.py)."""

    model_config = ConfigDict(extra="forbid")

    source: Source
    raw: dict
    reason: str
