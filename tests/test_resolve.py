"""merge() is pure and unit-tested directly; resolve_player() and the
real-data ingest need Postgres (the `conn` fixture from conftest.py)."""

from pathlib import Path

import pytest

from bball.load import run_ingest
from bball.models import PlayerSeasonRecord, Source
from bball.resolve import OVERRIDES, merge, resolve_player

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _rec(source: Source, **overrides) -> PlayerSeasonRecord:
    defaults = dict(
        source=source,
        source_key="k",
        full_name="Test Player",
        canonical_name="test player",
        season="2020-21",
    )
    defaults.update(overrides)
    return PlayerSeasonRecord(**defaults)


# --- merge(): pure, no DB ----------------------------------------------------

def test_merge_api_wins_overlap_field_and_logs_conflict():
    records = {
        Source.WISELY_API: _rec(Source.WISELY_API, team="Napoli Basket"),
        Source.REALGM: _rec(Source.REALGM, team="Panathinaikos"),
    }
    resolved, field_sources, conflicts = merge(records, "test player")

    assert resolved["team"] == "Napoli Basket"
    assert field_sources["team"]["source"] == "wisely_api"
    assert any(
        c["field"] == "team" and c["winner_value"] == "Napoli Basket"
        and c["loser_source"] == "realgm" and c["loser_value"] == "Panathinaikos"
        for c in conflicts
    )


def test_merge_fills_in_from_lone_source():
    # Only RealGM has this player-season (e.g. del Cerro 2023-24, API-missing).
    records = {Source.REALGM: _rec(Source.REALGM, team="Gran Canaria II", ast_pg=None)}
    resolved, field_sources, conflicts = merge(records, "test player")

    assert resolved["team"] == "Gran Canaria II"
    assert field_sources["team"]["source"] == "realgm"
    assert conflicts == []


def test_merge_single_source_zero_conflicts():
    records = {Source.WISELY_API: _rec(Source.WISELY_API, pts_pg=11.4, reb_pg=5.9)}
    _, _, conflicts = merge(records, "test player")
    assert conflicts == []


@pytest.mark.parametrize(
    "api_val, realgm_val, expect_conflict",
    [
        (11.40, 11.44, False),  # within 0.05 tolerance -> rounding noise
        (6.8, 5.5, True),       # exceeds tolerance -> real disagreement
    ],
)
def test_merge_tolerance_boundary(api_val, realgm_val, expect_conflict):
    records = {
        Source.WISELY_API: _rec(Source.WISELY_API, pts_pg=api_val),
        Source.REALGM: _rec(Source.REALGM, pts_pg=realgm_val),
    }
    _, _, conflicts = merge(records, "test player")
    has_pts_conflict = any(c["field"] == "pts_pg" for c in conflicts)
    assert has_pts_conflict is expect_conflict


def test_merge_overrides_flips_winner(monkeypatch):
    monkeypatch.setitem(OVERRIDES, ("test player", "league"), [Source.REALGM, Source.WISELY_API])
    records = {
        Source.WISELY_API: _rec(Source.WISELY_API, league="Valencia"),
        Source.REALGM: _rec(Source.REALGM, league="Segunda FEB"),
    }
    resolved, field_sources, _ = merge(records, "test player")
    assert resolved["league"] == "Segunda FEB"
    assert field_sources["league"]["source"] == "realgm"


def test_merge_realgm_only_fields_not_overwritten_by_api_absence():
    records = {
        Source.WISELY_API: _rec(Source.WISELY_API, pts_pg=10.0),
        Source.REALGM: _rec(Source.REALGM, fgm_pg=4.0, fga_pg=8.0),
    }
    resolved, field_sources, _ = merge(records, "test player")
    assert resolved["fgm_pg"] == 4.0
    assert field_sources["fgm_pg"]["source"] == "realgm"


# --- resolve_player(): needs Postgres ---------------------------------------

def test_resolve_player_creates_new_player_once(conn):
    with conn.cursor() as cur:
        index = {}
        rec = _rec(Source.WISELY_API, source_key="999", full_name="New Guy", canonical_name="new guy")
        pid1 = resolve_player(cur, rec, index)
        pid2 = resolve_player(cur, rec, index)
        assert pid1 == pid2

        cur.execute("SELECT count(*) FROM players WHERE full_name = 'New Guy'")
        assert cur.fetchone()[0] == 1
    conn.commit()


def test_resolve_player_matches_across_sources_by_canonical_name(conn):
    with conn.cursor() as cur:
        index = {}
        api_rec = _rec(
            Source.WISELY_API, source_key="26515",
            full_name="Dimitrios Agravanis", canonical_name="dimitrios agravanis",
        )
        realgm_rec = _rec(
            Source.REALGM, source_key="dimitrios agravanis",
            full_name="Agravánis, Dimítrios", canonical_name="dimitrios agravanis",
        )
        api_pid = resolve_player(cur, api_rec, index)
        realgm_pid = resolve_player(cur, realgm_rec, index)
        assert api_pid == realgm_pid

        cur.execute(
            "SELECT count(*) FROM source_player_map WHERE player_id = %s", (api_pid,)
        )
        assert cur.fetchone()[0] == 2
    conn.commit()


def test_resolve_player_api_display_name_preference(conn):
    with conn.cursor() as cur:
        index = {}
        realgm_rec = _rec(
            Source.REALGM, source_key="dimitrios agravanis",
            full_name="Agravánis, Dimítrios", canonical_name="dimitrios agravanis",
        )
        pid = resolve_player(cur, realgm_rec, index)

        api_rec = _rec(
            Source.WISELY_API, source_key="26515",
            full_name="Dimitrios Agravanis", canonical_name="dimitrios agravanis",
        )
        resolve_player(cur, api_rec, index)

        cur.execute("SELECT full_name FROM players WHERE player_id = %s", (pid,))
        assert cur.fetchone()[0] == "Dimitrios Agravanis"
    conn.commit()


# --- real-data end-to-end ingest --------------------------------------------

def test_ingest_all_real_data_counts(conn):
    summary = run_ingest(conn, [Source.WISELY_API, Source.REALGM], DATA_DIR)
    assert summary["players"] == 15
    assert summary["season_stats"] == 84
    assert summary["conflicts_total"] == 36
    assert summary["rejections_total"] == 2


def test_ingest_agravanis_2022_23_is_one_row_with_conflicts(conn):
    run_ingest(conn, [Source.WISELY_API, Source.REALGM], DATA_DIR)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.team, s.league, s.gp
            FROM season_stats s JOIN players p USING (player_id)
            WHERE p.full_name = 'Dimitrios Agravanis' AND s.season = '2022-23'
            """
        )
        rows = cur.fetchall()
        assert len(rows) == 1
        team, league, gp = rows[0]
        assert team == "Napoli Basket"

        cur.execute(
            """
            SELECT c.field FROM conflicts c
            JOIN players p USING (player_id)
            WHERE p.full_name = 'Dimitrios Agravanis' AND c.season = '2022-23'
            """
        )
        fields = {r[0] for r in cur.fetchall()}
        assert {"team", "league", "gp"} <= fields


def test_ingest_del_cerro_league_conflict_logged(conn):
    run_ingest(conn, [Source.WISELY_API, Source.REALGM], DATA_DIR)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.league, c.loser_value
            FROM season_stats s
            JOIN players p USING (player_id)
            JOIN conflicts c ON c.player_id = p.player_id AND c.season = s.season AND c.field = 'league'
            WHERE p.full_name = 'Adrian del Cerro' AND s.season = '2019-20'
            """
        )
        rows = cur.fetchall()
        assert len(rows) == 1
        resolved_league, loser_value = rows[0]
        assert resolved_league == "Valencia"  # API wins by default priority...
        assert loser_value == "Segunda FEB"   # ...but the disagreement is logged
