"""TS% math in isolation, plus the end-to-end pin: exactly 4 flags on the
real data at tolerance 0.02 (3 planted box-score corruptions + Agravanis
2022-23's genuine team/stint disagreement — see resolve.py/report.py)."""

from pathlib import Path

import pytest

from bball.load import run_ingest
from bball.models import Source
from bball.report import TS_TOLERANCE, report_ts
from bball.stats import true_shooting

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

EXPECTED_FLAGGED = {
    ("Isaac Fotu", "2020-21"),
    ("Nacho Arroyo", "2024-25"),
    ("Adrian del Cerro", "2019-20"),
    ("Dimitrios Agravanis", "2022-23"),
}


# --- true_shooting(): pure ---------------------------------------------------

def test_true_shooting_known_value():
    # pts=25, fga=20, fta=10 -> 25 / (2*(20+4.4)) = 25/48.8
    assert true_shooting(25, 20, 10) == pytest.approx(25 / 48.8)


def test_true_shooting_zero_attempts_is_none():
    assert true_shooting(0, 0, 0) is None


@pytest.mark.parametrize("pts,fga,fta", [(None, 10, 5), (10, None, 5), (10, 10, None)])
def test_true_shooting_missing_input_is_none(pts, fga, fta):
    assert true_shooting(pts, fga, fta) is None


# --- report_ts(): end-to-end on real data -----------------------------------

def test_report_ts_flags_exactly_four(conn):
    run_ingest(conn, [Source.WISELY_API, Source.REALGM], DATA_DIR)
    rows = report_ts(conn)

    assert len(rows) == 55  # comparable rows: both ts_pct_computed and ts_pct_api present

    flagged = {(r["full_name"], r["season"]) for r in rows if r["flag"]}
    assert flagged == EXPECTED_FLAGGED
    assert len(flagged) == 4


def test_report_ts_stored_only_where_both_sources_present(conn):
    run_ingest(conn, [Source.WISELY_API, Source.REALGM], DATA_DIR)
    with conn.cursor() as cur:
        # Adrian Bowie is API-only -> no RealGM box score -> ts_pct_computed NULL
        cur.execute(
            """
            SELECT s.ts_pct_computed FROM season_stats s JOIN players p USING (player_id)
            WHERE p.full_name = 'Adrian Bowie'
            """
        )
        rows = cur.fetchall()
        assert rows  # Bowie has seasons loaded
        assert all(r[0] is None for r in rows)

        # A both-source row has a non-null computed value.
        cur.execute(
            """
            SELECT s.ts_pct_computed FROM season_stats s JOIN players p USING (player_id)
            WHERE p.full_name = 'Isaac Fotu' AND s.season = '2020-21'
            """
        )
        (computed,) = cur.fetchone()
        assert computed is not None


def test_report_ts_delta_within_tolerance_not_flagged(conn):
    run_ingest(conn, [Source.WISELY_API, Source.REALGM], DATA_DIR)
    rows = report_ts(conn)
    for r in rows:
        if (r["full_name"], r["season"]) not in EXPECTED_FLAGGED:
            assert abs(r["delta"]) <= TS_TOLERANCE
