"""The adapter seam: every source implements this and nothing else, so
downstream (identity resolution, conflict merge, upserts — M4) is blind to
whether a record originated from JSON or CSV."""

from __future__ import annotations

from typing import Iterator, Protocol

from bball.models import PlayerSeasonRecord, Rejection


class SourceAdapter(Protocol):
    def extract(self) -> Iterator[PlayerSeasonRecord | Rejection]:
        """Lazily yield one item per source row/season-entry. Never raises —
        a row that fails to parse is yielded as a Rejection, not thrown."""
        ...
