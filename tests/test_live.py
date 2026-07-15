"""Bonus: live incremental ingest. Verified against the real snapshots
(research.md): fixed 10-player roster, updated_at strictly increasing,
stats never decrease. Tests prove the stale/out-of-order guard rather than
relying on the (clean) sample data to exercise it."""

from pathlib import Path

from bball.load import run_ingest, run_ingest_live
from bball.models import Source

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
LIVE_DIR = DATA_DIR / "live"
SNAPSHOTS = [LIVE_DIR / f"snapshot_{i:02d}.json" for i in range(5)]


def test_live_replay_all_five_yields_10_rows_matching_final(conn):
    run_ingest_live(conn, SNAPSHOTS)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM player_game_stats")
        assert cur.fetchone()[0] == 10

        cur.execute("SELECT status, period, home_score, away_score FROM games")
        status, period, home_score, away_score = cur.fetchone()
        assert (status, period, home_score, away_score) == ("final", 4, 103, 95)

        cur.execute(
            """
            SELECT pgs.min, pgs.pts, pgs.fgm, pgs.fga
            FROM player_game_stats pgs JOIN players p USING (player_id)
            WHERE p.full_name = 'Juani Marcos'
            """
        )
        row = cur.fetchone()
        assert row == (40, 20, 8, 18)  # snapshot_04 final totals

        cur.execute(
            """
            SELECT pgs.pts FROM player_game_stats pgs JOIN players p USING (player_id)
            WHERE p.full_name = 'Kofi Mensah'
            """
        )
        assert cur.fetchone()[0] == 17


def test_live_home_players_map_to_existing_api_players(conn):
    # Ingest the API first so Juani Marcos already exists as player_id=X via
    # (source='wisely_api', source_key='104461'); the live snapshot uses a
    # DIFFERENT (source='live', source_key='104461') pair, so this only
    # resolves to the same player_id via canonical_name matching.
    run_ingest(conn, [Source.WISELY_API], DATA_DIR)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM players")
        players_before = cur.fetchone()[0]  # 13 (API-only ingest)

        cur.execute(
            "SELECT player_id FROM source_player_map WHERE source = 'wisely_api' AND source_key = '104461'"
        )
        api_player_id = cur.fetchone()[0]

    run_ingest_live(conn, SNAPSHOTS)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT player_id FROM player_game_stats WHERE player_id = %s", (api_player_id,)
        )
        assert cur.fetchone() is not None  # Juani Marcos's live rows use the SAME player_id

        cur.execute("SELECT count(*) FROM players")
        players_after = cur.fetchone()[0]
        assert players_after == players_before + 5  # only the 5 away players are new


def test_live_away_player_created_once_across_repeated_polls(conn):
    run_ingest_live(conn, SNAPSHOTS)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM players WHERE full_name = 'Kofi Mensah'")
        assert cur.fetchone()[0] == 1

    # Re-poll an already-seen snapshot -- must not create a second player row.
    run_ingest_live(conn, [SNAPSHOTS[2]])
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM players WHERE full_name = 'Kofi Mensah'")
        assert cur.fetchone()[0] == 1

        cur.execute("SELECT count(*) FROM source_player_map WHERE source = 'live'")
        assert cur.fetchone()[0] == 10  # 5 home + 5 away, still exactly one map row each


def test_live_stale_poll_is_noop(conn):
    run_ingest_live(conn, SNAPSHOTS)  # advance to the final snapshot
    with conn.cursor() as cur:
        cur.execute("SELECT status, period, home_score FROM games")
        final_state = cur.fetchone()
        cur.execute(
            """
            SELECT pgs.pts FROM player_game_stats pgs JOIN players p USING (player_id)
            WHERE p.full_name = 'Juani Marcos'
            """
        )
        final_pts = cur.fetchone()[0]

    # Replay an OLDER snapshot (updated_at is behind the current game state).
    run_ingest_live(conn, [SNAPSHOTS[2]])

    with conn.cursor() as cur:
        cur.execute("SELECT status, period, home_score FROM games")
        assert cur.fetchone() == final_state  # unchanged -- guard held

        cur.execute(
            """
            SELECT pgs.pts FROM player_game_stats pgs JOIN players p USING (player_id)
            WHERE p.full_name = 'Juani Marcos'
            """
        )
        assert cur.fetchone()[0] == final_pts  # not reverted to snapshot_02's lower value
