"""Smoke tests for the M8 bonus Django endpoint (src/bball_web).

The `conn` fixture (conftest.py) commits a fresh schema + ingested data to the
real dockerized Postgres; the views open their own `bball.db.connect()`
against that same database, so no ORM/transaction wiring is needed between
the test and the view — just ingest, then hit the view through Django's test
client and read back what got committed.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bball_web.settings")
import django  # noqa: E402

django.setup()

from django.test import Client  # noqa: E402

from bball.load import run_ingest  # noqa: E402
from bball.models import Source  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def test_player_list_returns_15(conn):
    run_ingest(conn, [Source.WISELY_API, Source.REALGM], DATA_DIR)

    resp = Client().get("/players/")
    assert resp.status_code == 200
    players = resp.json()
    assert len(players) == 15
    assert all("season_count" in p for p in players)


def test_player_seasons_has_provenance_and_ts(conn):
    run_ingest(conn, [Source.WISELY_API, Source.REALGM], DATA_DIR)
    with conn.cursor() as cur:
        cur.execute("SELECT player_id FROM players WHERE full_name ILIKE %s", ("%del Cerro%",))
        player_id = cur.fetchone()[0]

    resp = Client().get(f"/players/{player_id}/seasons/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["seasons"], "expected at least one season row"

    flagged = next(s for s in body["seasons"] if s["season"] == "2019-20")
    assert flagged["ts_pct_computed"] is not None
    assert "team" in flagged["field_sources"]
    assert flagged["field_sources"]["team"]["source"] in ("wisely_api", "realgm")


def test_unknown_player_404(conn):
    run_ingest(conn, [Source.WISELY_API, Source.REALGM], DATA_DIR)

    resp = Client().get("/players/999999/seasons/")
    assert resp.status_code == 404
