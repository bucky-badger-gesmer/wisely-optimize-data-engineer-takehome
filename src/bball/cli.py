"""Command-line entry point: `uv run bball <command>`."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import db

# schema.sql lives at the repo root: src/bball/cli.py -> parents[2] == repo root.
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema.sql"


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


def main() -> None:
    parser = argparse.ArgumentParser(prog="bball")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-db", help="create tables from schema.sql (idempotent)")
    p_init.add_argument(
        "--reset",
        action="store_true",
        help="drop and recreate the schema before applying (destroys all data)",
    )

    args = parser.parse_args()

    if args.command == "init-db":
        init_db(reset=args.reset)


if __name__ == "__main__":
    main()
