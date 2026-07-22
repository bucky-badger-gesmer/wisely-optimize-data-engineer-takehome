# Writeup — Rough Draft

*Draft answering the six writeup questions. Sources: [WALKTHROUGH.md](WALKTHROUGH.md)
(full decision log) and [SIMPLE-WALKTHROUGH.md](SIMPLE-WALKTHROUGH.md) (overview).*

---

## 1. The schema, and why

Four ideas drive the schema:

- **We own player identity.** Every player gets an internal ID we generate
  (`players`). No source's key is ever our key — the API's `104461` and
  RealGM's name-as-identity are attached to our ID through a translation
  table, `source_player_map` (one row per source + source-key pair). Sources
  can be added or dropped without touching the data anyone queries.
- **One row per player-season; team is an attribute, not part of the key.**
  `season_stats` is keyed on (player, season). This was a deliberate choice
  forced by the data: the sources *disagree* on team for the same season
  (Agravanis 2022-23 — Napoli per the API, Panathinaikos per RealGM). Keying
  on team would silently split one season into two rows; keying without it
  forces a reconciliation decision. Trade-off, stated plainly: a genuine
  multi-team season needs a "stint" concept this schema doesn't have.
- **Every value remembers where it came from.** A single season row is a
  blend: the points might come from RealGM while the team name comes from the
  API. So each row carries a small JSON column, `field_sources`, that records
  — for each individual field — which source supplied it and when. A single
  "this row came from X" label couldn't describe a blended row like that; a
  per-field label can. It's also what the assignment's "swap a single field's
  source" requires: change which source wins a field, and that field's label
  updates on its own.
- **Nothing disappears silently.** Two audit tables: `rejections` (any row
  that couldn't be loaded, stored with raw payload + reason) and `conflicts`
  (every cross-source disagreement, both values, who won). CHECK constraints
  (ages 14–60, percentages 0–1, `fgm ≤ fga`) are the last line of defense.

Conventions: percentages stored as 0–1 fractions everywhere; RealGM's derived
`FG%`-style columns discarded and recomputed from makes/attempts. Indexes:
the natural primary/unique keys already cover the access patterns; one extra
for by-player game lookups; more would wait for real query patterns.

## 2. How the source-swap seam works

Three layers, each swappable independently:

1. **Adapters: each source gets its own translator.** The API speaks JSON,
   RealGM speaks CSV — so each source gets a small piece of code whose only
   job is to convert its files into one standard record shape
   (`PlayerSeasonRecord`). Everything past that point works with the standard
   shape and never sees a file format. Adding a source means writing one new
   translator; nothing else changes. (Think travel plug adapters: the wall
   socket stays the same, each country brings its own plug.)
2. **Identity map: a lookup table that says who's who.** `source_player_map`
   is just rows like "the API's player `104461` is our player #1" and
   "RealGM's 'juani marcos' is *also* our player #1." A new source shows up?
   We add rows to this table — we never have to redesign any tables.
3. **Priority config: "who wins" is a settings list, not logic buried in
   code.** `FIELD_PRIORITY` (a plain dictionary at the top of `resolve.py`)
   says, for each field, which source to trust first (e.g. points: API first,
   RealGM fills gaps). `OVERRIDES`, right next to it, handles one-off
   exceptions ("for this one player, take the team from RealGM instead").
   Want a different source to win a field? Edit one line there. And since
   every field already carries its where-did-this-come-from label
   (`field_sources`, from section 1), the label updates by itself on the next
   run — no database redesign, no query changes.

Proof it works, not just a claim: the live game feed (Milestone 7) is a third
source with five never-before-seen players, and it plugged in with **zero
schema changes and zero changes to the identity code**. Unknown away players
flowed through the same resolution waterfall (source-key match → canonical-name
match → create once, remember forever); home players matched their existing
API records by name.

## 3. How I resolved conflicts

Per-field source priority, in three groups: RealGM owns the box-score
*detail* — shooting splits (FGM/FGA, 3P, FT), assists, steals, blocks,
turnovers — which the API doesn't carry at all; the API owns the advanced
metrics (usage, TS%, PER, BPM), which RealGM doesn't carry; and the fields
both sources report — team, league, age, games played, and the basic
per-game stats (minutes, points, rebounds) — go to the API by default, with
RealGM filling in seasons the API is missing. Rounding
noise is tolerated (per-game values within 0.05 don't count as conflicts —
the CSV only carries one decimal place); every genuine disagreement is logged
to `conflicts` with both values, **regardless of who won**.

The symmetry of that logging is the point: the "trusted" API has its own
planted error (del Cerro 2019-20 lists league = "Valencia" — a club, not a
league). So priority is a default, not gospel. The reconciliation story in
one sentence: the conflict log is how a human notices the default was wrong,
and a one-line config change (or one `OVERRIDES` entry) is how they fix it.

## 4. What I tested, and why

Tests concentrate where wrong answers are *silent*:

- **Name matching** — the three hardest real cases, including the Æ character
  that standard Unicode accent-stripping doesn't handle.
- **Minute parsing** — decimal and MM:SS formats (one player's rows really do
  use MM:SS); garbage raises → becomes a rejection, never a wrong number.
- **TS% math** and its divide-by-zero / missing-input edges.
- **Conflict resolution** — correct winners and correct logging.
- **Duplicate policy with full accounting** — 71 CSV rows = 69 loaded + 1
  exact dupe + 1 near-dupe logged; every row's fate explainable.
- **Idempotency, demonstrated not asserted** — snapshot every table, run the
  entire pipeline again, diff = zero.
- **Live replay safety** — re-polls never duplicate; a stale snapshot
  replayed after the final is a no-op (the `updated_at` guard).

Two process choices worth naming: tests run against **real Postgres**, because
the idempotency guarantee *is* Postgres `ON CONFLICT` semantics — SQLite would
test a different database. And the known traps were locked in as failing
tests *before* the code that handles them, based on data quirks found by
inspection first (documented in `research.md`). No tests for trivial glue —
"tests for the parts that matter" is a prioritization statement.

## 5. Which advanced stat next, and how I'd validate it

I'd add eFG% (effective field goal percentage) next: `(FGM + 0.5 × 3PM) / FGA`.
Every input is already sitting in the RealGM box score, so there's no new
data to source. It rewards the extra value of a three-pointer, which makes it
a more honest read on shooting than raw FG%, and it's a natural companion to
the TS% column that already exists.

To validate it, I'd reuse the pattern that TS% already proved out: compute
the number myself, compare it against an independent reconstruction, and flag
anything that differs by more than what rounding can explain. For eFG% the
independent check is the CSV's own FG% and 3P% columns, recomputed from makes
and attempts. If my eFG% and the recomputed shooting percentages tell
different stories about the same player-season, something is wrong with the
underlying makes or attempts, and the report should say so.

I trust that pattern because it already caught more than it was designed to.
The TS% check flagged exactly four player-seasons: the three corruptions
planted in the CSV, plus one genuine cross-source ambiguity nobody planted
(the Agravanis split-season). A check that catches both bad data and bad
assumptions is worth reusing wholesale.

Two other candidates I considered and why they come second. TO% (turnover
percentage) is computable from the same box score, but no source carries a
turnover-percentage column to check it against, so validation falls back to
range and sanity checks, which is a much weaker story. PER, validated against
the API's own `per` column, is the more ambitious option, but its formula
needs league-wide context this dataset doesn't include. eFG% wins because it
has both the inputs and an independent check.

## 6. Productionizing, and what I'd do next

**Orchestration.** Each source becomes a scheduled job in Dagster or Airflow:
the API nightly, RealGM on its scrape cadence, the live feed on game nights.
The adapters are already the job bodies, so this is wiring, not a rewrite.
CI runs pytest against a Postgres service container on every change. The
compose stack in the repo already sketches the shape.

**Freshness.** Right now the only freshness signal is the `updated_at` stamp
on the data itself. Nothing records whether each source actually ran and
succeeded. I'd add a small watermark table that every run updates with the
source, finish time, rows loaded, and rejects. The SLA alert then falls out
of one query: has any source been silent longer than N hours?

**Monitoring.** Watch row-count deltas per run, alert when the rejection rate
spikes (the `rejections` table is already a dead-letter queue), and turn the
TS% cross-check into a standing monitor instead of a one-off report. Adapters
should also assert the exact columns they expect, so when RealGM renames one
the job fails loudly instead of half-parsing the file.

**Scale.** Land raw payloads in staging tables with batch IDs. I skipped this
at 71 rows, where the files themselves were the staging layer. Bulk load with
COPY, partition `player_game_stats` by game date, and move the resolved layer
into dbt. Staging also makes backfills honest: when the merge policy changes,
replay the stored history through the new rules instead of hoping the sources
still serve old data.

**Schema evolution.** `schema.sql` only creates objects. It can bootstrap a
database but never alter a live one, so production needs real migrations
(Alembic or sqitch).

**Next with more time:** parse RealGM's HTML pages as a drop-in adapter, add
fuzzy name matching for rosters where strict normalization falls short, model
multi-team seasons as stints (the Agravanis case is the motivation), move
provenance into a proper table if per-field history ever needs querying, and
add auth and pagination to the read API.

**Where it's weak.** Name-only matching could merge two different players who
share a name; the fix is corroborating attributes and a review queue. The
Agravanis row blends two sources into a season neither of them claims; stints
or a `disputed` flag would fix that. Keeping the last row for near-duplicates
is arbitrary, though it's deterministic and both payloads are logged. The
tolerances (0.05 and 0.02) are tuned to this CSV's one-decimal precision and
would need re-deriving for a new source. `field_sources` stores only the
latest winner, not history. And a source can never retract data, because the
upserts keep an old value when a new run supplies NULL; fixing that means
diffing full snapshots and writing explicit tombstones.
