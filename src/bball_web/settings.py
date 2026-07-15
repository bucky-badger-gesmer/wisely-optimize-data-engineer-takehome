"""Minimal Django settings for the M8 read-only bonus endpoint.

No ORM, no migrations, no DATABASES entry — Postgres already owns the schema
(schema.sql) and views (bball/db.py) talk to it directly via psycopg. This app
exists only to serve JSON over that data, so it's pared down to what
`django.urls.path` + `JsonResponse` need to run.

Dev-only choices, called out here rather than hidden: DEBUG/ALLOWED_HOSTS/
SECRET_KEY below are fine for a local read-only take-home demo and are not
production values (see the writeup's productionize section).
"""

from __future__ import annotations

DEBUG = True
ALLOWED_HOSTS = ["*"]

# Not a real secret: no sessions, no auth, no CSRF-protected writes in this
# app — Django just requires the setting to exist to boot.
SECRET_KEY = "dev-only-not-a-secret-no-writes-no-auth"

ROOT_URLCONF = "bball_web.urls"
INSTALLED_APPS: list[str] = []
MIDDLEWARE: list[str] = []
DATABASES: dict = {}
USE_TZ = True
