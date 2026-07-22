# Interview Prep Guide — Wisely Takehome Call

The call covers three things: **design decisions**, **where you'd take it next**, and
**how you think about the data layer under game-night load** — plus their pitch on the
role/product/equity. This guide is organized in that order, with likely follow-ups
and the answers already grounded in what you actually built.

**One correction to internalize before the call:** your writeup says "Dagster or
Airflow" for orchestration. Their stack is **Prefect + Datadog + AWS**. Everywhere
you'd have said Dagster, say Prefect (see the translation table in §3). Saying
"I wrote Dagster in the writeup, but your stack is Prefect and the mapping is
one-to-one: each adapter becomes a flow, each source a deployment on its own
schedule" turns the mismatch into a point *for* you.

---

## 0. The 90-second opening narrative

Have this memorized — it frames everything else:

> "The core problem was three sources that disagree — a JSON API, a scraped CSV,
> and a live feed — and the design goal was that no disagreement is ever silent
> and no source is ever load-bearing. Three ideas do all the work:
> **(1) I own identity** — every player gets an internal ID, and each source's key
> maps to it through a translation table, so adding or dropping a source never
> touches the data anyone queries.
> **(2) Resolution is config, not code** — a per-field priority list decides who
> wins, every disagreement is logged with both values, and every field on every
> row remembers which source supplied it.
> **(3) Everything is idempotent** — re-running the whole pipeline is a proven
> no-op, which is what makes retries, replays, and at-least-once delivery safe.
> The proof it works: the live feed was a third source with five never-seen
> players, and it plugged in with zero schema changes."

---

## 1. Design decisions — decision, why, trade-off, likely follow-up

For each one, lead with the *why*, volunteer the trade-off yourself (it reads as
maturity, and it preempts the gotcha question).

### 1a. Internal surrogate identity + `source_player_map`

- **Decision:** `players.player_id` is generated and owned by you; each source's
  key (API id `104461`, RealGM's normalized name, live-feed id) is a row in
  `source_player_map (source, source_key) → player_id`.
- **Why:** no external key is stable or universal. Sources can be added/dropped
  with map rows only — proven when the live feed added 5 unknown players with
  zero schema or identity-code changes.
- **Trade-off to volunteer:** name-based matching for RealGM could merge two
  different players who share a name. Fix: corroborating attributes (birth year,
  position) + a human review queue for low-confidence matches.
- **Likely follow-up:** *"What if two sources disagree about who a player IS?"*
  → That's an identity conflict, not a field conflict. Today the resolution
  waterfall is source-key match → canonical-name match → create. In production
  I'd add a confidence score and route ambiguous matches to a review queue
  rather than auto-merging — a wrong merge is far more expensive to unwind than
  a duplicate player.

### 1b. `season_stats` keyed on (player, season) — team is an attribute

- **Decision:** one row per player-season; team/league are resolved attributes.
- **Why:** the data forced it — sources disagree on team for the same season
  (Agravanis 2022-23: Napoli per the API, Panathinaikos per RealGM). Keying on
  team would silently split one season into two rows; keying without it forces
  an explicit reconciliation decision.
- **Trade-off to volunteer:** a genuine multi-team season needs a "stint" concept
  this schema doesn't have. The Agravanis row currently blends two sources into
  a season neither claims — stints (child table: player, season, team, date
  range, stats) or a `disputed` flag is the fix, and it's #4 on my roadmap.
- **Likely follow-up:** *"How would you migrate to stints without breaking the
  API?"* → Add the stint table alongside `season_stats`, backfill from
  provenance + conflicts, keep `season_stats` as the season-level rollup (a view
  over stints eventually). Consumers never see a breaking change.

### 1c. Per-field provenance in `field_sources` JSONB

- **Decision:** each row carries `{"pts_pg": {"source": "realgm", "updated_at": …}}`.
- **Why:** a resolved row is a *blend* — points from RealGM, team from the API.
  A row-level "source" label can't describe that; a field-level one can. It's
  also what makes the assignment's "swap a single field's source" a one-line
  config change: the label updates itself on the next run.
- **Trade-off to volunteer:** it stores only the *latest* winner, not history.
  If per-field history ever needs querying, promote it to a proper provenance
  table (long/narrow: row, field, source, value, valid_from). JSONB was the
  right cost at this scale; the table is the right shape at production scale.
- **Likely follow-up:** *"Why JSONB and not a normalized table from day one?"*
  → Access pattern: provenance is read with the row, never queried across rows,
  and never filtered on. JSONB rides along for free in the same page read. The
  moment someone asks "show me every field RealGM currently wins," normalize it.

### 1d. Audit tables: `rejections` + `conflicts`, plus CHECK constraints

- **Decision:** bad rows land in `rejections` with raw payload + reason; every
  cross-source disagreement lands in `conflicts` with both values and the
  winner — **logged even when the trusted source wins**.
- **Why the symmetry matters (your best story):** the "trusted" API has its own
  planted error — del Cerro 2019-20 lists league = "Valencia," a club, not a
  league. Priority is a default, not gospel. The conflict log is how a human
  notices the default was wrong; one `OVERRIDES` entry is how they fix it.
- **CHECKs as last line of defense:** age 14–60, percentages 0–1, `fgm ≤ fga`.
  These catch adapter bugs, not just source bugs.
- **Full accounting story:** 71 CSV rows = 69 loaded + 1 exact dupe skipped +
  1 near-dupe rejected-and-logged. Every row's fate is explainable. That
  sentence lands well — say it.

### 1e. The resolution seam: `FIELD_PRIORITY` + `OVERRIDES` config

- **Decision:** per-field source priority as a plain dict at the top of
  `resolve.py`; three field groups (RealGM owns box-score detail the API lacks;
  API owns advanced metrics RealGM lacks; shared fields default to API with
  RealGM filling gaps). Rounding tolerance of 0.05 on per-game values because
  the CSV carries one decimal.
- **Trade-off to volunteer:** the tolerances (0.05 / 0.02) are tuned to this
  CSV's precision and would be re-derived per source — ideally from each
  source's declared precision rather than hand-tuned.
- **Likely follow-up:** *"Config in code — would you move it to the DB?"* →
  At two sources, code is better: it's versioned, reviewed, and deployed with
  the resolver that interprets it. If non-engineers needed to change priorities
  or the override list grew, I'd move it to a table with an admin UI — Django
  admin makes that nearly free, which fits your stack.

### 1f. Testing philosophy

- Tests concentrate **where wrong answers are silent**: name normalization (the
  Æ case that standard accent-stripping misses), MM:SS vs decimal minutes,
  TS% divide-by-zero, conflict winners+logging, duplicate accounting,
  idempotency (snapshot every table → rerun everything → diff is zero), and
  live-replay safety (re-polls never duplicate; a stale snapshot after the
  final is a no-op).
- **Two process points to say out loud:**
  1. Tests run against **real Postgres**, not SQLite — because the idempotency
     guarantee *is* Postgres `ON CONFLICT` semantics. Testing on SQLite tests a
     different database.
  2. Known traps were locked in as **failing tests before the fix**, based on
     data inspection documented in `research.md`. Inspect first, then code.

### 1g. Smaller decisions you may be asked about

- **Percentages as 0–1 fractions everywhere**; RealGM's derived FG%-style
  columns are discarded and **recomputed from makes/attempts** — never trust a
  source's derived value when you have its inputs.
- **Both `ts_pct_api` and `ts_pct_computed` are stored** — the cross-check is a
  first-class column, not a one-off script. It flagged exactly 4 player-seasons:
  the 3 planted CSV corruptions *plus* the Agravanis ambiguity nobody planted.
  A check that catches both bad data and bad assumptions is worth reusing.
- **Indexes:** the PK/UNIQUE constraints already cover the access patterns; one
  extra index for by-player game lookups. "More would wait for real query
  patterns" — you index for measured access, not speculatively.
- **No staging tables at 71 rows** — the files were the staging layer. You knew
  the production answer (staging + batch IDs) and chose scale-appropriately.
  This "right-sized, but I know the next size" framing is your recurring theme.

---

## 2. Where I'd take it next — prioritized roadmap

Give it as an ordered list with a *reason for the order*. Suggested priority:

1. **Real migrations (Alembic).** `schema.sql` bootstraps but can never alter a
   live DB. This blocks everything else, so it's first.
2. **Watermark table + freshness SLAs.** Today the only freshness signal is
   `updated_at` on the data itself; nothing records whether a source ran.
   One small table (source, finish time, rows loaded, rejects) makes the SLA
   alert a single query: "has any source been silent longer than N hours?"
3. **Orchestration on Prefect** (their stack — see §3): each adapter becomes a
   flow, each source a deployment on its own cadence (API nightly, RealGM on
   scrape cadence, live feed on game nights). The adapters are already the job
   bodies — this is wiring, not a rewrite. CI runs pytest against a Postgres
   service container.
4. **Staging tables + honest backfills.** Land raw payloads with batch IDs.
   When the merge policy changes, replay stored history through the new rules
   instead of hoping sources still serve old data.
5. **Stints** for multi-team seasons (Agravanis is the motivation).
6. **Monitoring as standing checks** (Datadog): row-count deltas per run,
   rejection-rate spikes (the `rejections` table is already a dead-letter
   queue), TS% cross-check as a monitor, adapters asserting exact expected
   columns so a renamed RealGM column fails loudly.
7. **Identity hardening:** fuzzy matching + corroborating attributes + review
   queue; provenance history table; retraction handling (upserts currently
   never NULL-out an old value — needs snapshot diffs + explicit tombstones).
8. **API maturation:** auth, pagination, and the RealGM HTML profile pages as a
   drop-in third adapter.

**Next advanced stat** (they may re-ask #5): eFG% — inputs already in the box
score, one new column, validated the same way TS% was (independent
reconstruction, flag anything beyond rounding). TO% loses because there's no
independent column to check against; PER loses because it needs league-wide
context the dataset lacks. "It has both the inputs and an independent check"
is the whole argument.

---

## 3. Stack translation: writeup → their stack

| Your writeup said | Say instead (their stack) |
|---|---|
| Dagster/Airflow jobs per source | **Prefect**: one flow per adapter, one deployment per source with its own schedule; retries + exponential backoff on flow/task level; the live feed as a high-frequency deployment active only on game nights |
| "watermark table + SLA query" | Same table, but the alert is a **Datadog monitor** on a custom metric the flow emits (`ingest.rows_loaded`, `ingest.rejections`, `ingest.last_success_age`) |
| "alert when rejection rate spikes" | Datadog anomaly monitor on the rejection counter; dead-letter review stays in the `rejections` table |
| Postgres in Docker | **RDS/Aurora Postgres** — mention parameter groups, read replicas, RDS Proxy for pooling (see §4) |
| dbt for the resolved layer | Still valid — dbt models over staging tables, run as a Prefect task |

Prefect specifics worth having in your pocket: flows/tasks/deployments,
work pools, `retries=` / `retry_delay_seconds=` on tasks, concurrency limits
(cap simultaneous DB-writing tasks), and that Prefect's observability pairs
with Datadog rather than replacing it.

---

## 4. The data layer under game-night load ⭐

This is the topic your takehome covers least, so it's where they'll probe
hardest. Structure the answer: **characterize the workload → show the write
path is already built for it → explain how reads scale → name the real
bottleneck (connections) → observability → failure modes.**

### 4a. Characterize the workload first (do this before proposing anything)

> "First thing I'd do is put numbers on it, because game-night load is two very
> different problems: a **write side** that's small and steady, and a **read
> side** that's bursty and spiky."

- **Write side (the feed):** say 50 concurrent games × ~25 players × a poll
  every 5–10s ≈ **a few hundred upserts/second worst case**. That is *not* a
  scaling problem for Postgres — single-node Postgres does orders of magnitude
  more. The design problem is correctness under retries, out-of-order delivery,
  and duplicate polls — **which the takehome already solves** (idempotent
  upserts keyed on (game_id, player_id); the `updated_at` monotonic guard makes
  a stale snapshot after the final a proven no-op).
- **Read side (the coaches):** everyone looks at the same games at the same
  time. Read amplification, connection storms, and cache stampedes are the
  actual risk — not write throughput.

Making this distinction unprompted is the single strongest thing you can do in
this section.

### 4b. Write path — what's built, what changes at scale

- Already built: batch upsert per poll snapshot, `ON CONFLICT ... DO UPDATE`,
  the `updated_at` guard (`WHERE EXCLUDED.updated_at >= games.updated_at` —
  note it's `>=`, not `>`: an equal-timestamp replay re-applies identical
  values, which is still idempotent, while a same-timestamp *correction* isn't
  silently dropped), at-least-once safe. Say: "polling is inherently
  at-least-once, so idempotency isn't a nice-to-have — it's the correctness
  model."
- At scale, the changes are mechanical:
  - **One transaction per snapshot**, batched upserts (or COPY into a temp
    table + one merge) instead of row-at-a-time.
  - **Short transactions** — never hold a transaction across an HTTP poll.
  - **Update-in-place tables** (`player_game_stats`, `games`) are
    high-churn: tune **fillfactor** (~70–80) so updates stay HOT (no index
    write amplification), and make **autovacuum more aggressive** on these
    tables specifically (per-table `autovacuum_vacuum_scale_factor`).
  - **Partition `player_game_stats` by game date** — game-night data is
    append-hot/history-cold; partitioning keeps indexes small and makes
    archival a `DETACH`, not a `DELETE`.

### 4c. Read path — where the real work is

- **The resolved layer is precomputed at write time** — reads are cheap
  single-row/single-player lookups on covered indexes. Keep aggregation out of
  the request path; if a dashboard needs a rollup, materialize it on the write
  side (or a matview refreshed per poll cycle), don't compute it per request.
- **Read replicas** for the Django read endpoints; the write path keeps the
  primary. Know the caveat: **replication lag** on a live scoreboard is
  user-visible — monitor it, and if lag exceeds the poll interval, serve
  live-game reads from the primary and historical reads from replicas.
- **Short-TTL caching** (5–10s, matching the poll cadence) in front of the
  hottest endpoints. Live data means the cache is a *request collapser*, not a
  freshness compromise: 500 coaches refreshing the same game become 1 DB query
  per poll cycle. Guard against stampedes (stale-while-revalidate or a lock).

### 4d. The bottleneck that actually bites: connections

> "The thing that actually takes Postgres down on a spiky night isn't rows,
> it's **connections**."

- Django opens per-request connections by default; a traffic spike becomes a
  connection storm; each Postgres connection is a process with real memory.
- Fix: **PgBouncer (transaction pooling) or RDS Proxy** between Django and
  Postgres; `CONN_MAX_AGE` in Django; a hard cap well below `max_connections`.
- Know the classic gotcha: transaction pooling breaks session state (prepared
  statements, `SET`, advisory locks) — fine for this read API, but you say it
  before they do.

### 4e. Observability (Datadog — their stack)

Name specific monitors, not "add monitoring":

- **Feed freshness:** seconds since last successful poll *per live game* —
  page if it exceeds ~3× the poll interval **while a game is in progress**.
- **Replication lag** vs the poll interval.
- **p95/p99 query latency** on the read endpoints; slow-query log via
  `pg_stat_statements`.
- **Connection saturation** (in use vs pool cap vs `max_connections`).
- **Rows upserted per poll** (a sudden zero = silent feed failure) and
  **rejection-rate** anomaly.
- **Autovacuum/bloat** on the hot update tables.

### 4f. Failure modes — pre-load these answers

- *Feed sends garbage mid-game?* → Pydantic validation rejects the row into
  `rejections` with the raw payload; the game keeps its last-good state; the
  rejection-rate monitor fires. Nothing wrong ever displays.
- *Poller crashes and replays an hour of snapshots?* → Safe by construction:
  idempotent upserts + the `updated_at` guard. Replay is literally a tested
  code path (`ingest live --replay`).
- *Two pollers run at once?* → Last-writer-wins per row with the monotonic
  guard, so state can never go backwards; add an advisory lock per game_id if
  you want single-writer discipline.
- *Postgres primary fails during games?* → RDS Multi-AZ failover; the pipeline
  is retry-safe so ingestion self-heals; the read cache masks the blip.
- *What if load 100×?* → Only then: split the live path's writes to a separate
  ingest service, consider Redis for in-progress game state with Postgres as
  the durable store — but say clearly you would **not** start there; Postgres
  handles this workload with the tuning above, and premature distribution buys
  operational cost with no benefit at their scale. (This "boring tech, measured
  escalation" stance fits a small team and a load-bearing role.)

---

## 5. Rapid-fire follow-ups (one-breath answers)

- **"Why upserts instead of an append-only event log?"** — The read model needs
  current state, and coaches need it in one query. An event log is strictly more
  information but strictly more machinery; at this scale the audit tables +
  raw snapshots give replayability without the event-sourcing tax. If we ever
  need per-poll history, land snapshots in staging (roadmap #4) — that *is*
  the event log.
- **"Why not store the raw API responses?"** — I should, and it's roadmap #4
  (staging with batch IDs). At 71 rows the files were the staging layer;
  the moment sources are remote and mutable, raw capture becomes mandatory
  for honest backfills.
- **"Why Postgres for the live feed and not something 'real-time'?"** — A few
  hundred upserts/sec against a hot set of a few thousand rows is comfortably
  inside Postgres; the resolved layer, identity map, and live feed staying in
  one database means one consistency model and joins for free.
- **"Why Pydantic at the boundary if the DB has CHECKs?"** — Defense in depth
  with different failure modes: Pydantic rejects with the payload and a reason
  into `rejections` (recoverable, observable); a CHECK violation aborts a
  transaction (last-resort). Validation you can report on beats validation
  that just explodes.
- **"What's the ugliest thing in the code?"** — Have a real answer ready.
  Good candidate: near-duplicate handling keeps the last row, which is
  deterministic but arbitrary; and the resolver's tolerance constants are
  hand-tuned to one CSV. Owning something small and true beats "nothing."
- **"How do you know ingest didn't corrupt anything?"** — Full accounting
  (every one of the 71 rows explainable), the TS% cross-check (caught 3 planted
  errors + 1 real ambiguity), and the idempotency diff test.

---

## 6. Your questions for them

Pick ~5; the best ones flow from your takehome directly.

**Data layer / product (shows you're already doing the job):**
- "What does peak game night actually look like today — concurrent games,
  poll frequency, coaches online? Where does the current system groan first?"
- "What's the current source landscape — how many external APIs/scrapes, and
  which one breaks most often?"
- "How do coaches consume this during a game — dashboards, alerts, exports?
  What's the freshness expectation: seconds or minutes?"
- "You mentioned portal cycle, preseason, conference play as high-usage
  periods — which one has hurt the most historically, and why?"
- "How is identity resolution handled today — is there a canonical player ID,
  and who fixes it when a merge is wrong?"
- "What does the handoff from David and Alex look like — docs, pairing period,
  what are they most eager to hand off?"

**Role / day-to-day:**
- "What does 'own the domain' mean in week one vs month six?"
- "What's the on-call reality on game nights?"
- "What does the full-stack growth path look like concretely — first non-data
  project you'd hand me?"

**Equity (it's equity-only, so ask precisely — these are diligence questions,
not negotiation):**
- Percentage ownership (not just share count), fully diluted.
- Vesting schedule and cliff; what happens on departure or acquisition
  (single/double trigger).
- Most recent 409A valuation and strike price; any funding raised or planned.
- Runway and the plan for when cash compensation enters the picture.
- How many people are on this equity-only structure, and has anyone vested
  through it yet?

---

## 7. Postgres depth drill — expect a probe beyond the takehome

The JD is explicit: elite database engineering is the one thing that's "not
learn-on-the-job." That sentence predicts an interview move — questions that
test whether your Postgres knowledge extends past what the takehome required.
Refresh these; each maps back to something you built, so you can answer from
experience rather than trivia.

- **MVCC + vacuum.** Updates don't overwrite — they write a new row version and
  leave a dead tuple; vacuum reclaims them. This is *why* your hot
  update-in-place tables (`games`, `player_game_stats`) need aggressive
  autovacuum and fillfactor headroom (§4b) — be ready to explain the mechanism,
  not just the tuning. Know what a **HOT update** is (new version on the same
  page, no index entries touched) and that it only happens when no indexed
  column changed and the page has free space — that's the fillfactor
  connection.
- **`ON CONFLICT` semantics** — the takehome's idempotency guarantee. Know the
  edges: requires a unique index/constraint as the arbiter; `DO UPDATE` can
  reference `excluded.*`; the conditional-update pattern
  (`WHERE excluded.updated_at > tbl.updated_at`) is your stale-poll guard;
  concurrent upserts on the same key serialize rather than error (unlike a
  naive SELECT-then-INSERT, which races).
- **Isolation levels.** Read Committed is the default and is what your pipeline
  runs under; know what Repeatable Read and Serializable add and their cost
  (serialization failures → retry loops). One crisp line: "the pipeline doesn't
  need more than Read Committed because idempotent upserts make the write path
  order-insensitive."
- **Index types beyond btree**, each with a hook into your schema: **GIN** (if
  anyone ever queries *into* `field_sources` JSONB), **partial indexes** (e.g.
  only in-progress games), **covering indexes / `INCLUDE`** (index-only scans
  for the roster endpoint), **BRIN** (huge append-only tables like a full
  game-events history — pairs with the partitioning answer).
- **EXPLAIN ANALYZE workflow.** Be ready to narrate how you'd attack a slow
  query: `EXPLAIN (ANALYZE, BUFFERS)`, look for seq scans on big tables,
  misestimated row counts (stale statistics → `ANALYZE`), nested-loop blowups,
  and sorts spilling to disk (`work_mem`). Mention `pg_stat_statements` as the
  "which query is actually hurting" starting point — you already cite it in §4e.
- **Locking.** Row locks vs table locks; why long transactions are poison
  (they block vacuum from reclaiming dead tuples *and* hold locks); advisory
  locks (your single-writer-per-game answer in §4f).
- **Have one real war story from past work** — a slow query or scaling problem
  you diagnosed and fixed, with the numbers (before/after latency, what the
  plan showed, what you changed). The takehome proves design judgment; a war
  story proves the "proven, elite" part of the JD. Pick it before the call.

---

## 8. Portal cycle + basketball — the parts of the JD the takehome didn't touch

**Portal cycle is their word for a load event that isn't game night** — the JD
lists "portal cycle, preseason, conference play" as the high-usage periods. The
transfer portal is a *different* load profile from live games, and having a
take on it unprompted shows you read their world:

- Game night = high-frequency small upserts + bursty reads (§4).
- **Portal cycle = mass identity churn**: hundreds of players changing teams in
  days, rosters in flux, coaches querying across programs. That stresses
  exactly the layer you designed — `source_player_map` and identity
  resolution — not write throughput. Your talking point: "my identity design
  is actually built for the portal problem: player identity is stable while
  team is just an attribute that changes, and a transfer is an UPDATE, not a
  re-keying. The stint model on my roadmap is even more directly the portal
  data model."
- Preseason = bulk backfills and roster loads → the staging + batch-ID + replay
  story (roadmap #4).

**Basketball fluency.** The JD twice says "you love basketball" and points
toward Basketball Operations long-term. Expect some version of "why basketball /
why us." Prepare 60 honest seconds — and connect stats to coaching decisions,
because that's their product: TS% and eFG% aren't trivia to you, they're *the
honest versions of shooting efficiency a coach should trust over raw FG%* —
which is literally why the takehome plants FG% corruptions and asks you to
recompute. If you follow specific teams/players or play yourself, say so;
genuine beats polished here.

---

## 9. The "you" questions — working style, growth, and how you built this

- **Daily standup / "communicates and shows up."** Have one concrete example of
  surfacing a blocker early and one of a progress-communication habit (written
  updates, demo cadence). The JD lists this as an owned responsibility, not a
  nicety — they've likely been burned.
- **High motor + ownership.** The takehome itself is your evidence: full
  accounting of all 71 rows, tests-before-code, two bonus milestones (live
  feed + Django endpoint), containerized end-to-end run. Say it as a pattern:
  "I default to finishing the last mile — the demo runs with one command."
- **The full-stack growth story.** The role is data-first with deliberate
  full-stack growth. Your arc: the takehome already crosses the seam (Django
  read endpoints on top of the pipeline), and the growth you want is exactly
  turning coach needs into end-to-end workflows. Name one front-end thing
  you'd genuinely like to learn under their CTO's mentorship — specificity
  reads as hunger, vagueness reads as a rehearsed answer.
- **"How did you use AI tools on this?"** Increasingly asked, and worth
  answering cleanly rather than being caught flat: describe your actual
  workflow honestly — what you directed, what you verified, and the parts that
  required *your* judgment (the data inspection in `research.md`, the schema
  key decision, the priority groupings, catching the planted errors). The
  strong frame: every design decision in §1 is one you can defend to arbitrary
  depth without notes — and this call is the proof. Never claim you didn't use
  tools if you did; a false answer there fails the interview retroactively.
- **"Why an equity-only role works for you"** — they will want to know you've
  thought about it and won't bail in month four. Have a true answer about your
  runway/situation at whatever level of detail you're comfortable sharing,
  paired with the diligence questions from §6.

---

## 10. Code-level probes — questions from someone who actually read your code

If an interviewer opened the repo, these are the specific lines they'd ask
about. Each is either a deliberate choice to defend or a small gap to own
before they name it.

**Talking points you're currently leaving on the table (both in `load.py`):**

- **The partial re-ingest guarantee.** The season-stats upsert does
  `col = COALESCE(EXCLUDED.col, season_stats.col)` per column and merges
  provenance with `field_sources || EXCLUDED.field_sources`. That's why
  running `ingest realgm` alone never blanks out API-owned fields — each
  source only overwrites what it actually supplies. This is a genuinely
  elegant line of SQL; put it in the screen-share tour. (It's also the flip
  side of the retraction weakness in §5 — the *same* COALESCE that protects
  partial runs is what prevents a source from ever NULL-ing a value out.)
- **Rejections idempotency is different on purpose.** Every other table
  upserts on a natural key, but a rejected row has no reliable identity
  across runs — so `rejections` is delete-then-repopulate, scoped to the
  sources being re-ingested. If asked "how is *that* table idempotent," the
  answer is: by re-derivation, not by upsert, because no natural key exists.

**Gaps to own before they name them:**

- **`conflicts.player_id` has no foreign key** (compare: every other
  player_id in the schema has `REFERENCES players`). If asked, the defensible
  read is "an audit log should survive its subject's deletion" — but be
  honest that consistency argues for the FK since `rejections` keeps raw
  payloads instead. Fine either way; having *noticed* is what scores.
- **The conflict log keeps only the latest values.** Its `ON CONFLICT ... DO
  UPDATE` overwrites winner/loser values per unique key — same
  "latest-winner-only" limitation as `field_sources`, same fix (append-only
  history table) if anyone ever needs it.
- **Per-record work in `resolve_player`:** one SELECT per record (an N+1
  shape), a whole-run in-memory `groups` dict, one transaction per run, and
  an unconditional display-name UPDATE on every API record. All fine at 71
  rows — the scale answer is batch identity resolution (temp table + one
  join), chunked commits, and staging tables (roadmap #4). This is your
  "right-sized, but I know the next size" theme applied to the code itself.
- **The Django views open a connection per request** — and the docstring
  says so. Turn it into a segue: "my own read views have exactly the
  connection behavior I called out as the game-night bottleneck in §4d —
  in production they'd go through PgBouncer/`CONN_MAX_AGE` first." Raw SQL
  instead of the ORM is deliberate and documented (schema is owned by
  `schema.sql`, the app is read-only over it); no auth/pagination is a
  stated non-goal, already on the roadmap.

---

## 11. Final checklist before the call

- [ ] Re-run the demo end-to-end once (`docker compose up --build`, curl both
      endpoints, `bball report ts`) so it's fresh if they screen-share.
- [ ] Re-read `research.md` — the "inspect first, tests before code" story
      starts there and it's a differentiator.
- [ ] Rehearse the 90-second narrative (§0) and the workload-characterization
      opener (§4a) out loud.
- [ ] Have the numbers ready: 15 players, 71 CSV rows (69+1+1), 4 TS% flags
      (3 planted + Agravanis), 5 new live players, zero schema changes.
- [ ] Remember the swap: Dagster → **Prefect**, monitoring → **Datadog**,
      hosted Postgres → **RDS/Aurora**.
- [ ] Pick your "ugliest thing in the code" answer and your top-5 questions
      for them.
- [ ] Pick your **Postgres war story** from past work, with before/after
      numbers (§7).
- [ ] Skim the §7 depth drill the morning of — MVCC/HOT, `ON CONFLICT` edges,
      index types, EXPLAIN workflow.
- [ ] Rehearse the **portal-cycle take** (§8) — it's the load question they
      live with that the takehome never asked about.
- [ ] Settle your honest 60 seconds on **why basketball** and your answer to
      **"how did you use AI tools on this?"** (§9).
- [ ] Open `resolve.py` and `load.py` before the call and rehearse a 2-minute
      screen-share tour: FIELD_PRIORITY → waterfall → conflict logging →
      the COALESCE + `field_sources ||` upsert (§10's partial-re-ingest
      guarantee) → the `>=` stale-poll guard. If they ask to see code, you
      drive confidently instead of scrolling around.
- [ ] Skim §10 so no one who read your code can surprise you with your own
      lines (the missing FK on `conflicts`, per-request connections in the
      views).
