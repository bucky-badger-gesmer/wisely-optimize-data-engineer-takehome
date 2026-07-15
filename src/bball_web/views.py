"""Read-only JSON views over the resolved data — the "endpoint" half of the
README's "Django read endpoint or view" bonus add-on (the "view" half is
`v_player_seasons` in schema.sql).

No writes, no auth, no pagination — deliberately tiny, per the README ("don't
over-build"). Each view opens its own connection via `bball.db.connect()`
(the same helper the CLI uses) rather than going through an ORM, since the
schema is already owned by schema.sql.
"""

from __future__ import annotations

from django.http import JsonResponse
from psycopg.rows import dict_row

from bball import db


def player_list(request):
    with db.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT p.player_id, p.full_name, p.position,
                   count(s.season_stat_id) AS season_count
            FROM players p LEFT JOIN season_stats s USING (player_id)
            GROUP BY p.player_id, p.full_name, p.position
            ORDER BY p.full_name
            """
        )
        players = cur.fetchall()
    return JsonResponse(players, safe=False)


def player_seasons(request, player_id: int):
    with db.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT player_id, full_name, position FROM players WHERE player_id = %s",
            (player_id,),
        )
        player = cur.fetchone()
        if player is None:
            return JsonResponse({"error": f"no player with id {player_id}"}, status=404)

        cur.execute(
            "SELECT * FROM v_player_seasons WHERE player_id = %s ORDER BY season",
            (player_id,),
        )
        seasons = cur.fetchall()
        for row in seasons:
            row.pop("player_id", None)
            row.pop("full_name", None)
            row.pop("position", None)

    return JsonResponse({**player, "seasons": seasons})
