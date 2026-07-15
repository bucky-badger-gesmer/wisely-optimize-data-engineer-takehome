"""Database connection helpers."""

from __future__ import annotations

import os

import psycopg

DEFAULT_DATABASE_URL = "postgresql://postgres:postgres@localhost:5455/bball"


def database_url() -> str:
    """Resolve the connection string from $DATABASE_URL, falling back to the
    local docker-compose Postgres."""
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def connect() -> psycopg.Connection:
    """Open a new connection to the target database."""
    return psycopg.connect(database_url())
