"""`bball report ts|conflicts` — the verification/reconciliation deliverables.

report_ts is the requirement-6 check: RealGM-derived TS% vs the API's own
true_shooting_pct. The 0.02 tolerance separates 1-decimal-CSV rounding noise
from real disagreement; on the real data it flags exactly 4 player-seasons —
3 planted box-score corruptions plus one genuine team/stint conflict
(Agravanis 2022-23), which is itself evidence the check is a real detector,
not just tuned to a known answer.
"""

from __future__ import annotations

TS_TOLERANCE = 0.02


def report_ts(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.full_name, s.season, s.ts_pct_computed, s.ts_pct_api
            FROM season_stats s JOIN players p USING (player_id)
            WHERE s.ts_pct_computed IS NOT NULL AND s.ts_pct_api IS NOT NULL
            ORDER BY p.full_name, s.season
            """
        )
        rows = []
        for full_name, season, computed, api in cur.fetchall():
            computed, api = float(computed), float(api)
            delta = computed - api
            rows.append({
                "full_name": full_name,
                "season": season,
                "computed": computed,
                "api": api,
                "delta": delta,
                "flag": abs(delta) > TS_TOLERANCE,
            })

    flagged = sum(1 for r in rows if r["flag"])
    print(f"TS%% verification: {len(rows)} comparable rows, {flagged} flagged (|delta| > {TS_TOLERANCE})\n")
    print(f"{'player':<22} {'season':<8} {'computed':>9} {'api':>8} {'delta':>8}  flag")
    for r in rows:
        marker = " <-- FLAG" if r["flag"] else ""
        print(f"{r['full_name']:<22} {r['season']:<8} {r['computed']:>9.4f} {r['api']:>8.4f} "
              f"{r['delta']:>+8.4f}{marker}")
    return rows


def report_conflicts(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.full_name, c.season, c.field, c.winner_source, c.winner_value,
                   c.loser_source, c.loser_value
            FROM conflicts c JOIN players p USING (player_id)
            ORDER BY p.full_name, c.season, c.field
            """
        )
        rows = [
            {
                "full_name": full_name, "season": season, "field": field,
                "winner_source": winner_source, "winner_value": winner_value,
                "loser_source": loser_source, "loser_value": loser_value,
            }
            for full_name, season, field, winner_source, winner_value, loser_source, loser_value
            in cur.fetchall()
        ]

    print(f"Conflicts: {len(rows)} logged\n")
    last_key = None
    for r in rows:
        key = (r["full_name"], r["season"])
        if key != last_key:
            print(f"{r['full_name']} {r['season']}:")
            last_key = key
        print(f"  {r['field']:<10} {r['winner_source']}={r['winner_value']!r:<20} "
              f"vs {r['loser_source']}={r['loser_value']!r}")
    return rows
