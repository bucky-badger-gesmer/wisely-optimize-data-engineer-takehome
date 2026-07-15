"""Command-line entry point: `uv run bball <command>`."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import db
from .load import run_ingest, run_ingest_live
from .models import Source
from .report import report_conflicts, report_ts

# schema.sql lives at the repo root: src/bball/cli.py -> parents[2] == repo root.
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema.sql"

_INGESTABLE_SOURCES = {"wisely_api": Source.WISELY_API, "realgm": Source.REALGM}


def init_db(reset: bool = False) -> None:
    """Apply schema.sql. Idempotent by default (every object is IF NOT EXISTS);
    --reset drops and recreates the public schema first for a clean slate."""
    sql = SCHEMA_PATH.read_text()
    with db.connect() as conn:
        with conn.cursor() as cur:
            if reset:
                cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
            cur.execute(sql)
        conn.commit()
    print(f"init-db: applied {SCHEMA_PATH.name}" + (" (after reset)" if reset else ""))


def ingest(source: str, data_dir: Path) -> None:
    """Extract -> resolve -> merge -> upsert. `source` is 'all' or one of
    _INGESTABLE_SOURCES; re-running is idempotent (see load.py)."""
    sources = list(_INGESTABLE_SOURCES.values()) if source == "all" else [_INGESTABLE_SOURCES[source]]
    with db.connect() as conn:
        summary = run_ingest(conn, sources, data_dir)
    print(f"ingest {source}: players={summary['players']} season_stats={summary['season_stats']} "
          f"(this run: {summary['season_groups_this_run']} player-seasons, "
          f"{summary['conflicts_logged_this_run']} conflicts logged, "
          f"{summary['rejections_this_run']} rejections) "
          f"| totals: conflicts={summary['conflicts_total']} rejections={summary['rejections_total']}")


def ingest_live(path: Path | None, replay: Path | None) -> None:
    """One poll (`path`) or a full replay in order (`--replay <dir>`, globs
    snapshot_*.json sorted by name)."""
    if replay is not None:
        paths = sorted(replay.glob("snapshot_*.json"))
        if not paths:
            raise SystemExit(f"no snapshot_*.json files found in {replay}")
    elif path is not None:
        paths = [path]
    else:
        raise SystemExit("ingest live requires a snapshot path or --replay <dir>")

    with db.connect() as conn:
        summary = run_ingest_live(conn, paths)
    print(f"ingest live: processed {summary['snapshots_processed']} snapshot(s), "
          f"{summary['boxscore_rows_processed']} boxscore rows "
          f"| totals: players={summary['players']} games={summary['games']} "
          f"player_game_stats={summary['player_game_stats']}")


_REPORTS = {"ts": report_ts, "conflicts": report_conflicts}


def report(kind: str) -> None:
    with db.connect() as conn:
        _REPORTS[kind](conn)


def main() -> None:
    parser = argparse.ArgumentParser(prog="bball")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-db", help="create tables from schema.sql (idempotent)")
    p_init.add_argument(
        "--reset",
        action="store_true",
        help="drop and recreate the schema before applying (destroys all data)",
    )

    p_ingest = sub.add_parser("ingest", help="extract -> resolve -> merge -> upsert one or all sources")
    p_ingest.add_argument(
        "source", choices=["all", *_INGESTABLE_SOURCES, "live"], help="which source to ingest"
    )
    p_ingest.add_argument(
        "path", nargs="?", type=Path, default=None, help="snapshot file (source=live only)"
    )
    p_ingest.add_argument(
        "--data-dir", type=Path, default=Path("data"), help="root data directory (default: ./data)"
    )
    p_ingest.add_argument(
        "--replay", type=Path, default=None,
        help="directory of snapshots to replay in order, e.g. data/live/ (source=live only)",
    )

    p_report = sub.add_parser("report", help="print a verification/reconciliation report")
    p_report.add_argument("kind", choices=list(_REPORTS), help="which report to run")

    args = parser.parse_args()

    if args.command == "init-db":
        init_db(reset=args.reset)
    elif args.command == "ingest":
        if args.source == "live":
            ingest_live(args.path, args.replay)
        else:
            ingest(args.source, args.data_dir)
    elif args.command == "report":
        report(args.kind)


if __name__ == "__main__":
    main()
