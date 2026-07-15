"""DB fixture used by tests that need real Postgres (ON CONFLICT / type
semantics SQLite would dodge). Requires `docker compose up -d`."""

from pathlib import Path

import pytest

from bball import db

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema.sql"
DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture
def conn():
    """A fresh schema per test: drop/recreate public, apply schema.sql."""
    connection = db.connect()
    with connection.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        cur.execute(SCHEMA_PATH.read_text())
    connection.commit()
    yield connection
    connection.close()
