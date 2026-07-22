# Plain-English Walkthrough — How This Application Works and Why

This is a study guide, not the graded writeup. It walks through every milestone,
explains each architectural decision in everyday language, and ends with an
honest weaknesses list, the process story, and a cheat-sheet mapping to the six
questions the writeup must answer.

---

## The big picture (30 seconds)

We were given the same ~15 basketball players described by **two different
sources** that don't agree with each other:

- A clean **API feed** (JSON files) — has stable player IDs and advanced stats.
- A messy **RealGM spreadsheet** (CSV) — has the detailed box score, but no
  player IDs, inconsistent name spellings, duplicate rows, missing cells, and a
  few numbers that flat-out contradict the API (some of that mess was planted
  on purpose).

The job: merge both into **one clean, trustworthy dataset** in a Postgres
database, where every number remembers where it came from, re-running the
pipeline never creates duplicates, and disagreements between sources are
*decided and documented* rather than silently papered over.

Think of it like merging two contact lists on your phone: same people, spelled
differently, some fields conflicting, some entries only in one list. You need
rules for "who is the same person," rules for "which phone number wins," and a
record of every judgment call you made.

---

## Milestone 1 — The foundation: environment + database schema

**What was built:** a disposable Postgres database (runs in Docker, one
command to start), and `schema.sql` — the blueprint for every table.

### Decision: we assign our own player ID, never trusting a source's ID

Every player gets an internal ID number that *we* generate (`players` table).
The API's ID (`104461`) and RealGM's identity (a name) are never used as our
key.

**Why:** sources come and go. If our whole database were keyed on the API's ID
and we ever dropped that API, everything breaks. With our own neutral ID, any
source can be attached or detached without touching the data everyone queries.

### Decision: a "translation table" connects each source to our players

The `source_player_map` table is a simple lookup: *"source X calls this player
Y → that's our player #3."* One row per (source, source-key) pair.

**Why:** this is half of the "source-swap seam" the assignment asks for.
Adding a whole new source later means adding *rows* to this table — never
changing the schema. (Milestone 7 proves this: the live game feed, a third
source, plugged in with zero schema changes.)

### Decision: one row per player **per season** — team is a field, not part of the key

`season_stats` is keyed on (player, season). Team and league are just
attributes stored on the row, like points or rebounds.

**Why:** the sources actually *disagree* on which team a player was on
(Agravanis 2022-23: the API says Napoli Basket, RealGM says Panathinaikos). If
team were part of the key, that disagreement would silently create **two rows
for one season** instead of forcing us to reconcile it into one. Trade-off:
a player who genuinely played for two teams in one season would need a "stint"
concept this schema doesn't have — a known limitation, stated in the writeup.

### Decision: every field wears a sticker saying where it came from

Each `season_stats` row carries `field_sources` — a small JSON note per field:
*"points came from RealGM, updated at this time; usage came from the API…"*
Plus a row-level `updated_at` timestamp.

**Why:** the assignment asks for provenance at the level of *a single field*
("swap a single field's source"). Row-level provenance ("this row came from
RealGM") can't express "points from RealGM but team from the API." Field-level
can.

### Decision: two "paper trail" tables — nothing disappears silently

- `rejections`: any row we couldn't safely load is stored here **with the raw
  data and a reason**, instead of being thrown away.
- `conflicts`: every time the two sources disagreed and we picked a winner,
  the disagreement is recorded — field, both values, both sources, who won.

**Why:** the grading criteria literally say "handled deliberately, not
silently dropped" and "decide — and document." These tables *are* the
documentation, queryable at any time.

### Decision: the database itself refuses impossible data

CHECK constraints: ages must be 14–60, percentages 0–1, and you can't make
more shots than you attempted (`fgm ≤ fga`).

**Why:** last line of defense. Even if a bug slips past the code, the database
will reject nonsense rather than store it.

### Smaller calls worth knowing

- **Percentages stored as 0–1 fractions everywhere** (0.569, not 56.9) — one
  convention, the API's, so numbers are always comparable.
- **RealGM's `FG%`-style columns are thrown away and recomputed** from
  makes/attempts — recomputing is more trustworthy than trusting a derived
  column, and it fills a missing `3P%` cell for free.
- **The given data files are committed untouched.** The mess *is* the test
  data; "fixing" the files would hide exactly what the pipeline is graded on.
- **No staging tables** at 71 rows — the source files themselves act as the
  staging layer. In production you'd land raw copies first (writeup note).

---

## Milestone 2 — A common language + taming the messy names and numbers

**What was built:** `models.py` (the shared internal record format) and
`normalize.py` (the cleaning functions), with tests written *first*.

### Decision: every source is translated into one shared record shape

`PlayerSeasonRecord` is the single internal format — same field names, same
units — no matter which source a record came from.

**Why:** everything downstream (matching, merging, loading) only has to
understand *one* shape. This is what makes sources swappable: a new source
just needs a translator into this shape.

### Decision: a name has two forms — a display form and a matching form

- `display_name`: what humans see — "Ægir Steinarsson", accents preserved.
- `canonical_name`: what the computer matches on — comma order flipped
  ("Cerro, Adrian del" → "adrian del cerro"), accents stripped (Ægir → aegir,
  á → a), lowercased, whitespace collapsed.

**Why:** RealGM writes "Agravánis, Dimítrios"; the API writes "Dimitrios
Agravanis". Those must be recognized as the same human without mangling how
the name is displayed. One verified subtlety: the standard Unicode
accent-stripping trick does **not** handle Æ, so it's folded explicitly. With
these rules, all 10 overlapping players match deterministically — verified
against the real data, not assumed.

### Decision: minutes come in two formats, and garbage is rejected loudly

`parse_minutes` accepts decimal ("20.5") and clock format ("26:36" → 26.6
minutes). Empty means "unknown" (NULL). Anything else raises an error that
becomes a rejection record — never a silently-loaded wrong number.

**Why:** all of one player's rows use MM:SS while everyone else's are decimal
(a real quirk in the CSV, found by inspection). And a loud failure beats a
quiet wrong answer.

### Decision: tests were written before the code, encoding the known traps

The three hardest names, the Æ case, the MM:SS rows, empty cells → NULL.

**Why:** every quirk was first verified by inspecting the actual data
(documented in `research.md`), then locked in as a test so the cleaning logic
can't regress.

---

## Milestone 3 — Adapters: one translator per source

**What was built:** `adapters/wisely_api.py` and `adapters/realgm.py`, both
implementing the same tiny interface: *read your files, emit
`PlayerSeasonRecord`s (or `Rejection`s)*.

### Decision: the adapter pattern — each source gets its own plug

Like travel plug adapters: the wall socket (our pipeline) never changes; each
country's plug (source) gets its own adapter. The pipeline doesn't know or
care whether records came from JSON, CSV, or (later) a live feed.

**Why:** this is the other half of the source-swap seam. Want to parse
RealGM's raw HTML pages instead of the CSV someday? Write one new adapter;
nothing else in the system changes.

### Decision: one bad row never kills the run

The RealGM adapter wraps each CSV row in its own error handling. A bad row
becomes a `Rejection` (raw data + reason, stored in the database) and the run
continues.

**Why:** "validated, not silently dropped" — and in production you never want
one corrupt line to take down a whole nightly load.

### Decision: duplicate rows within the CSV get a *policy*, not just deletion

- An **exact** duplicate row (identical in every column) → keep one, drop one.
- A **near** duplicate (same player-season, *different* stats — Steinarsson
  2015-16 appears with 11.4 and 11.6 points) → keep the **last** row, and log
  the incident with **both** payloads so a human can audit it.

**Why:** exact dupes are noise; conflicting dupes are information. Full
accounting is enforced by a test: 71 CSV rows = 69 loaded + 1 exact dupe +
1 near dupe. Every row's fate is explainable.

---

## Milestone 4 — The heart: "who is this?" and "who wins?"

**What was built:** `resolve.py` (identity matching + the merge policy) and
`load.py` (writing to the database without ever duplicating).

### Decision: identity resolution is a three-step waterfall

For each incoming record:
1. Have we seen this (source, source-key) before? → use that player. (Fast path.)
2. No? Does the *canonical name* match an existing player? → link them
   (this is how a RealGM row finds its API twin) and remember the link.
3. Still no? → genuinely new player; create them and remember the link.

Display-name rule: when both sources know a player, the API's spelling of
their name wins (cleaner feed), regardless of ingest order.

### Decision: "who wins each field" is **configuration, not code**

`FIELD_PRIORITY` is a plain table in one file — for each field, an ordered
list of sources:

- **Box-score details** (shooting splits, steals, blocks…): RealGM wins —
  the API doesn't even carry them.
- **Advanced metrics** (usage, PER, BPM…): API wins — RealGM doesn't carry them.
- **Overlapping fields** (games played, points, team, age…): API preferred,
  RealGM fills gaps.

Plus `OVERRIDES`: a per-player, per-field escape hatch that beats the default.

**Why — this answers the assignment's exact question:** *"swap a player's — or
a single field's — source without changing the schema or anything downstream."*
Swapping a field's source = editing one line of config. Swapping one player's
field = one `OVERRIDES` entry. No schema change, no query change, and
`field_sources` provenance automatically reflects the new winner.

### Decision: log every disagreement, even when we're confident in the winner

Whenever the losing source had a *different* value, a `conflicts` row is
written — win or lose, both values preserved. Small rounding noise is excused
(per-game numbers within 0.05 don't count as conflicts, because the CSV only
carries one decimal place).

**Why the logging is symmetric:** the API — the "trusted" source — has its own
planted bug (del Cerro 2019-20 lists his *league* as "Valencia", which is a
club, not a league). So "API wins" is a **default**, not a claim of truth. The
conflict log is the mechanism by which a human spots that and flips one line
of config to fix it. That's the reconciliation story in one sentence.

### Decision: upserts everywhere — "update if it exists, insert if it doesn't"

Every write uses Postgres `ON CONFLICT ... DO UPDATE` on the row's natural
key. And the update fills each column with the new value **or keeps the old
one if the new run didn't provide it** (COALESCE).

**Why:** the first half is idempotency — running the pipeline twice can never
create duplicates, like re-saving a contact updates it instead of cloning it.
The second half protects **partial** re-runs: re-ingesting only RealGM won't
blank out the advanced stats the API owns.

---

## Milestone 5 — The advanced stat: compute TS% and check it against a second source

**What was built:** True Shooting % computed from RealGM's box score, stored
*alongside* the API's own TS% value, and a verification report
(`bball report ts`).

### Decision: store both numbers, then compare — the comparison IS the deliverable

`ts_pct_computed = PTS / (2 × (FGA + 0.44 × FTA))` from RealGM's numbers,
with guards (no attempts, or missing inputs → NULL, never divide-by-zero).
The API's `true_shooting_pct` is kept in its own column. The report shows
both, the difference, and flags any gap bigger than 0.02.

**Why 0.02:** the CSV rounds everything to one decimal place, so small
disagreement is inevitable noise. On the real data, 51 of 55 comparable rows
land within ±0.013 — pure rounding. The tolerance cleanly separates noise
from signal.

### The result — and why it's the best story in the project

Exactly **four** player-seasons flag:

- **Fotu 2020-21, Arroyo 2024-25, del Cerro 2019-20** — the three box-score
  corruptions planted in the CSV. Caught, as designed.
- **Agravanis 2022-23** — *not* planted corruption. The two sources describe
  what look like two different stints of the same season (different team,
  league, and games played). A genuine cross-source ambiguity.

**Why this matters:** the check caught a failure mode nobody tuned it for.
It's a real detector, not a test rigged to a known answer — and the Agravanis
case is also the concrete justification for the "team is an attribute, not a
key" schema decision from Milestone 1. One design decision, validated by an
independent check. That's the strongest thread in the whole writeup.

---

## Milestone 6 — Proving it: idempotency and end-to-end tests

**What was built:** `test_pipeline.py` — the tests that check the *system*,
not just the parts.

### The three proofs

1. **Correct totals:** ingest everything → exactly 15 players (13 from the
   API + 2 who exist only in RealGM), one row per player-season, no dupes.
2. **Run it twice → zero changes.** Snapshot every table, run the whole
   pipeline again, compare: identical. That's idempotency *demonstrated*,
   not asserted.
3. **Full accounting:** all 71 CSV rows accounted for — 69 loaded, 1 exact
   duplicate, 1 near-duplicate logged. Nothing vanished.

### Decision: tests run against real Postgres, not a lightweight stand-in

**Why:** the whole idempotency mechanism is Postgres's `ON CONFLICT` upsert
behavior. Testing against SQLite (the easy option) would skip exactly the
behavior being graded. Each test gets a fresh schema so tests can't
contaminate each other.

### What was deliberately tested — and what wasn't

Tests concentrate where the risk is: name matching (the three hard cases),
TS% math and its edges, conflict-resolution winners + logging, duplicate
policy, minute parsing, run-twice idempotency. No tests for trivial glue —
the assignment says "tests for the parts that matter," and that's a
prioritization statement worth making out loud.

---

## Milestone 7 (bonus) — The live game feed: keeping a box score fresh

**What was built:** a third adapter for the live feed (five poll snapshots of
one game, pregame → final), two new tables (`games`, `player_game_stats`),
and `bball ingest live` with a `--replay` mode.

### Decision: each poll upserts, guarded by a timestamp — stale data can't overwrite fresh data

Every write includes `WHERE incoming.updated_at >= existing.updated_at`. A
snapshot that arrives late or out of order simply does nothing.

**Why:** on a real game night, network retries and out-of-order polls happen.
The guard makes the ingest safe to replay in any order — the tests prove that
replaying an old snapshot *after* the final changes nothing.

### The payoff: a third source plugged in with **zero schema changes**

The away team's five players exist in no other source. They flow through the
*same* identity resolution as everyone else: not in the map → not matched by
name → created once, remembered forever after. The home players, meanwhile,
matched to their existing API records by name. The seam designed in
Milestones 1–4 absorbed a brand-new source without modification — the best
possible evidence that the "source-agnostic" design actually works.

Also shipped here: `v_player_seasons`, a database **view** presenting the
resolved, provenance-annotated player-season data as one clean queryable
surface — the foundation Milestone 8 sits on.

---

## Milestone 8 (bonus) — A tiny window into the data: Django read endpoints

**What was built:** two read-only web endpoints —
`GET /players/` (the roster with season counts) and
`GET /players/<id>/seasons/` (a player's full resolved seasons, including
both TS% columns and the per-field provenance).

### Decision: deliberately tiny — read-only, raw SQL, no ORM, no migrations, no auth

The endpoints run plain SQL against the `v_player_seasons` view using the same
database helper the CLI uses. Django's model layer is skipped entirely.

**Why:** Postgres already owns the schema (`schema.sql`); teaching Django a
second copy of it would create two sources of truth for no benefit at this
size. The assignment says twice not to over-build; auth, pagination, and
containerizing the web app are one-line productionization notes, not builds.

### Detail worth pointing at in a demo

The endpoints expose **our internal player ID** (del Cerro is `3`), not any
source's raw key (the API's `104461`). Even at the API surface, no source
leaks through — the abstraction holds top to bottom.

---

## Where it's weak — say it before they ask

The writeup asks where you think the work is weak. These are the honest
answers, each with the fix you'd reach for — naming them is self-awareness,
not confession:

1. **Name matching can false-*merge*, not just false-miss.** Two genuinely
   different players who share a canonical name would be silently collapsed
   into one person. The walkthrough only mentions the opposite failure
   (spelling variants that *don't* match, fixable with fuzzy matching). Fix:
   corroborating attributes in the match — birth year, position, career-span
   overlap — before accepting a name-only link, and a review queue for
   ambiguous matches instead of auto-linking.
2. **The Agravanis 2022-23 row is a blended record.** RealGM's box score
   describes (apparently) one stint, the API's team/league another — so the
   stored row mixes them into a season *neither source claims*. The TS% check
   caught the ambiguity, but the resolution (API team + RealGM box score) is a
   documented guess, not a verified fact. Fix: the "stint" concept — or at
   minimum a `disputed` flag on the row so consumers know.
3. **"Keep the last row" for near-duplicates is arbitrary.** CSV row order
   isn't authoritative; last-wins was chosen because *something* deterministic
   had to be, and both payloads are logged. Fix: with real sources, prefer the
   row with the later scrape/update timestamp, or route near-dupes to review.
4. **The tolerances are hand-tuned to this dataset.** 0.05 on per-game
   conflicts and 0.02 on TS% both derive from the CSV's one-decimal rounding.
   A source with different precision needs the thresholds re-derived — they're
   constants in code, not per-source config yet.
5. **`field_sources` only remembers the latest winner.** The JSON blob is a
   snapshot, not a history — you can't ask "what did this field say last
   month, and from whom?" — and JSON is awkward to index/query at scale. Fix:
   the normalized provenance table already noted as future work.
6. **A source can never retract data.** The upsert's COALESCE keeps the old
   value when a new run supplies NULL — great for partial re-runs, but it
   means a source that *corrects a value to empty* or *removes a player
   entirely* is never noticed. Same for conflicts: a conflict row lingers even
   after the source fixes its data. Fix: full-snapshot ingests diff against
   the previous landed snapshot and emit explicit tombstones/clears.

---

## The process story — the "what you tried" note

Three process facts worth stating explicitly in the writeup, since they shaped
everything above: **research before code** — every data quirk (the MM:SS
minutes, the Æ character, the planted conflicts, the duplicate rows) was found
by inspecting the actual files and written down in `research.md` *before* any
pipeline code existed; **tests before code** for the known traps — the hard
names, the minute formats, the TS% edges were locked in as failing tests
first; and **one milestone per commit**, so the git history reads as the
build's narrative arc. Nothing in the pipeline handles a hypothetical — every
guard corresponds to a documented, observed quirk.

---

## Cheat-sheet: the six writeup questions, answered

### 1. "Your schema, and why"

Players get an internal ID we control; a translation table
(`source_player_map`) links each source's identity to ours. Stats live in one
row per player-per-season — team is a stored fact, not part of the key,
because the sources disagree on team for the same season (Agravanis) and
keying on it would split one season into two rows instead of forcing a
decision. Every field carries a provenance sticker (`field_sources`), the
database enforces sanity rules (percentages 0–1, makes ≤ attempts), and two
audit tables (`rejections`, `conflicts`) guarantee nothing is silently
dropped or silently decided. Indexes: the primary/unique keys already create
every index the access patterns need; one extra for by-player game lookups;
more would wait for real query patterns.

### 2. "How the source-swap seam works" — three layers

1. **Adapters** — each source has a translator into one shared record format;
   a new source is a new adapter, nothing downstream changes.
2. **Identity map** — `source_player_map` ties any source's key to our
   internal player; adding a source adds rows, never schema.
3. **Priority config** — `FIELD_PRIORITY` (which source wins each field) and
   `OVERRIDES` (per-player escape hatch) are configuration, not code.
   Swapping a field's source is one edited line; swapping it for one player
   is one entry. Provenance updates automatically.

Proof it works: the live feed — a third source with five never-before-seen
players — plugged in with zero schema changes and zero changes to the
identity code.

### 3. "How you resolved conflicts"

Per-field source priority: RealGM owns the box score (API doesn't carry it),
the API owns advanced metrics (RealGM doesn't carry them), and for the
overlap the API is preferred with RealGM filling gaps. Rounding-level noise
is tolerated (0.05 on per-game values); every genuine disagreement is logged
to the `conflicts` table with both values, *regardless of who won* — because
the "trusted" API has its own planted error (league='Valencia'), so priority
is a default, not gospel, and the log plus a one-line override is how a human
corrects it.

### 4. "What you tested and why"

The parts where wrong answers are silent: name matching (the three hard names
incl. Æ), minute parsing (two formats), TS% math and its divide-by-zero
edges, conflict winners + logging, the duplicate-row policy, full-row
accounting (71 = 69 + 1 + 1), run-twice idempotency (snapshot-diff equals
zero), and live-feed replay/stale-poll safety. Against **real Postgres**,
because the idempotency guarantee *is* Postgres upsert behavior — SQLite
would have tested a different database's semantics.

### 5. "Which advanced stat next, and how you'd validate it"

**eFG% (effective field-goal %)**: `(FGM + 0.5 × 3PM) / FGA` — every input is
already in the RealGM box score, and it validates the same way TS% did:
compute it, compare against an independent reconstruction (the CSV's own FG%
and 3P% columns recomputed from makes/attempts), flag beyond a rounding
tolerance. The TS% exercise showed this cross-check pattern catches both
corrupted data *and* genuine source ambiguity — eFG% reuses the pattern
wholesale. (PER validated against the API's `per` column is the more
ambitious alternative.)

### 6. "How you'd productionize, and what you'd do next"

**Orchestration:** each source becomes a scheduled job in Dagster/Airflow
(API nightly, RealGM scrape on its cadence, live feed polling on game
nights) — the adapters already are the job bodies. CI runs the pytest suite
against a Postgres service container on every change.
**Freshness:** today "freshness" is only the `updated_at` stamps on the data
itself — nothing records *when each source last ingested successfully*. Add a
per-source watermark table (source, last success, rows in, rejects) that every
run updates; the SLA alert is then one query ("any source silent > N hours?").
**Monitoring:** row-count deltas per run, rejection-rate alarms (the
`rejections` table is already a dead-letter queue), cross-source tolerance
breaches — the TS% check generalized into a standing monitor — and **schema
drift** on the sources: adapters assert the exact column/field set they expect
and fail loudly with an alert when RealGM renames a column, rather than
part-parsing it.
**Scale:** land raw source payloads verbatim in staging tables with batch IDs
(skipped at 71 rows — the files were the staging layer), bulk-load via COPY,
partition `player_game_stats` by game date, move the resolved layer to dbt.
Staging also buys the **backfill story**: when merge policy changes (a
priority flip, a new override), replay the landed raw history through the new
rules instead of hoping the sources still serve old data.
**Schema evolution:** `schema.sql` is `CREATE IF NOT EXISTS` only — it can
bootstrap but never *alter* a live database. Production needs versioned
migrations (Alembic/sqitch, or dbt for the resolved layer) so schema changes
ship like code changes.
**Next with more time:** parse RealGM's HTML profile pages as a drop-in
adapter (the CSV was the source this round); fuzzy name matching
(trigram/embeddings) for real-world rosters where deterministic
normalization isn't enough; a "stint" concept for true multi-team seasons
(the Agravanis case is the motivation); a normalized provenance table if
per-field history needs querying; and auth/pagination/containerization for
the read API. **Known limitation, documented not hidden:** re-ingesting a
single source alone is last-writer-wins on shared fields — provenance tracks
it, and a full `ingest all` restores priority-correct values. (The fuller
honest-limitations list is the "Where it's weak" section above.)

---

## If you're asked to demo it live — the 5-command tour

```bash
docker compose up -d && uv sync          # start Postgres, install deps
uv run bball init-db                     # create the schema (safe to re-run)
uv run bball ingest all                  # ingest both sources; run it twice — second run changes nothing
uv run bball report ts                   # the TS% verification: exactly 4 flags
uv run bball report conflicts            # the documented reconciliation log
```

Bonus paths: `uv run bball ingest live --replay data/live/` (the live game,
stale-poll-safe), then `uv run python manage.py runserver` and
`curl localhost:8000/players/3/seasons/` (resolved stats + provenance over
HTTP). `uv run pytest` runs the whole test suite green.
