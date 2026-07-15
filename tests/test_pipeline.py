"""Idempotency + end-to-end accounting — the pieces test_resolve.py/test_ts.py
don't already cover: a formal double-run zero-diff check, an explicit
no-dupes check on identity resolution, and the 71 = 69 + 1 + 1 CSV row
accounting tied to the database (not just the adapter in isolation)."""

from pathlib import Path

from bball.load import run_ingest
from bball.models import Source

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _snapshot(cur):
    cur.execute("SELECT player_id, full_name, position FROM players ORDER BY player_id")
    players = cur.fetchall()
    cur.execute(
        """
        SELECT player_id, season, team, league, age, gp, min_pg, pts_pg, reb_pg,
               ast_pg, fgm_pg, fga_pg, tpm_pg, tpa_pg, ftm_pg, fta_pg,
               stl_pg, blk_pg, tov_pg, usage_pct, ts_pct_api, ts_pct_computed,
               reb_pct, per, bpm, field_sources
        FROM season_stats ORDER BY player_id, season
        """
    )
    season_stats = cur.fetchall()  # updated_at deliberately excluded: it's expected to change
    return players, season_stats


def test_ingest_all_counts_and_no_dupes(conn):
    run_ingest(conn, [Source.WISELY_API, Source.REALGM], DATA_DIR)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*), count(DISTINCT full_name) FROM players")
        total, distinct = cur.fetchone()
        assert total == 15
        assert distinct == 15  # no split/merged identity

        cur.execute("SELECT count(*) FROM season_stats")
        assert cur.fetchone()[0] == 84

        cur.execute(
            "SELECT count(*) FROM (SELECT player_id, season FROM season_stats "
            "GROUP BY player_id, season HAVING count(*) > 1) t"
        )
        assert cur.fetchone()[0] == 0

        cur.execute("SELECT source, count(*) FROM source_player_map GROUP BY source")
        by_source = dict(cur.fetchall())
        assert by_source == {"wisely_api": 13, "realgm": 12}


def test_ingest_all_twice_is_zero_diff(conn):
    run_ingest(conn, [Source.WISELY_API, Source.REALGM], DATA_DIR)
    with conn.cursor() as cur:
        players_1, season_stats_1 = _snapshot(cur)
        cur.execute("SELECT count(*) FROM conflicts")
        conflicts_1 = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM rejections")
        rejections_1 = cur.fetchone()[0]

    run_ingest(conn, [Source.WISELY_API, Source.REALGM], DATA_DIR)
    with conn.cursor() as cur:
        players_2, season_stats_2 = _snapshot(cur)
        cur.execute("SELECT count(*) FROM conflicts")
        conflicts_2 = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM rejections")
        rejections_2 = cur.fetchone()[0]

    assert players_1 == players_2
    assert season_stats_1 == season_stats_2
    assert conflicts_1 == conflicts_2 == 36
    assert rejections_1 == rejections_2 == 2


def test_realgm_row_accounting_71_total(conn):
    run_ingest(conn, [Source.WISELY_API, Source.REALGM], DATA_DIR)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM rejections WHERE source = 'realgm'")
        rejected = cur.fetchone()[0]

        cur.execute(
            """
            SELECT count(*) FROM season_stats s
            WHERE EXISTS (
                SELECT 1 FROM jsonb_each(s.field_sources) f
                WHERE f.value ->> 'source' = 'realgm'
            )
            """
        )
        loaded_from_realgm = cur.fetchone()[0]

    assert rejected == 2
    assert loaded_from_realgm == 69
    assert loaded_from_realgm + rejected == 71  # every CSV row: loaded, deduped, or rejected
