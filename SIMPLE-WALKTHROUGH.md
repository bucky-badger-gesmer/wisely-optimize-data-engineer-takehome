# Simple Walkthrough — The Whole Project in Five Minutes

*(The full version, with every decision explained, is [WALKTHROUGH.md](WALKTHROUGH.md).)*

## The problem

Two data sources describe the same ~15 basketball players — and they disagree:

- A clean **API feed** (JSON): stable player IDs, advanced stats.
- A messy **spreadsheet** (CSV): detailed box scores, but no IDs, inconsistent
  name spellings, duplicates, missing cells, and a few planted errors.

The job: merge both into **one clean Postgres dataset** where every number
remembers where it came from, re-running never creates duplicates, and every
disagreement is decided *and* documented.

It's like merging two contact lists: same people spelled differently, some
fields conflicting. You need rules for "who is the same person," rules for
"which value wins," and a record of every judgment call.

## How it works — the pipeline in four steps

1. **Translate.** Each source has an *adapter* that converts its files into
   one shared record format. New source = new adapter; nothing else changes.
2. **Identify.** Every player gets *our own* internal ID. A lookup table maps
   each source's identity ("API player 104461", "RealGM's 'Agravánis,
   Dimítrios'") to that ID. Matching across sources uses a normalized name
   (accents stripped, "Last, First" flipped), so spelling variants still find
   the same human.
3. **Merge.** A simple config table says which source wins each field
   (spreadsheet owns box-score detail, API owns advanced stats). Every real
   disagreement is logged to a `conflicts` table with both values — even when
   we're confident in the winner, because the "trusted" source has errors too.
4. **Load safely.** Every write is an upsert ("update if exists, insert if
   not"), so running the pipeline twice changes nothing. Bad rows aren't
   dropped — they're stored in a `rejections` table with the raw data and a
   reason.

## The best part: the check that caught something real

True Shooting % is computed from the spreadsheet's box score and compared to
the API's own TS% number. Exactly **4 player-seasons flag**: the 3 corruptions
planted in the CSV — *and one genuine cross-source ambiguity nobody planted*
(two sources describing what look like different halves of the same season).
The detector works on problems it wasn't tuned for.

## The bonuses

- **Live game feed:** a third source (5 poll snapshots of one game) plugged in
  with **zero schema changes**. Re-polls upsert instead of duplicating, a
  timestamp guard makes stale/out-of-order polls a no-op, and never-before-seen
  away players are created once and remembered.
- **Read API:** two read-only Django endpoints (`/players/`,
  `/players/<id>/seasons/`) serving the resolved data with per-field
  provenance. Deliberately tiny — no ORM, no auth, no over-build.

## Honest weaknesses (short version)

Name-only matching could merge two different people who share a name; one
blended season row (the ambiguity the TS% check caught) is a documented guess;
tolerances are hand-tuned to this dataset; provenance keeps only the latest
winner, not history; and a source can never *retract* data. Each has a named
fix in the full walkthrough.

## Run it

```bash
docker compose up --build      # one command: Postgres + schema + ingest + API on :8000
curl localhost:8000/players/
```

Or step by step on the host: `docker compose up -d db` → `uv sync` →
`uv run bball init-db` → `uv run bball ingest all` (twice, to see idempotency)
→ `uv run bball report ts` → `uv run python manage.py runserver`.
`uv run pytest` runs the whole suite. Full details: [INSTRUCTIONS.md](INSTRUCTIONS.md).
