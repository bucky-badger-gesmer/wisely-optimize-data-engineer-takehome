#!/usr/bin/env python
"""Django's manage.py, pointed at the src-layout bball_web settings.

Run via `uv run python manage.py runserver` after `uv run bball ingest all`.
"""

import os
import sys
from pathlib import Path


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bball_web.settings")
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Run `uv sync` to install it."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
