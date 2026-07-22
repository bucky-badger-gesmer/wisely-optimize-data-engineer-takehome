# Interview Cheat Sheet — Plain-English Edition

Every idea from the full guide, translated into analogies a non-engineer gets
immediately. Use these when the conversation goes product-level or when a
plain answer lands better than a technical one. (Deep technical versions:
`interview-prep.md`.)

---

## The whole project, in one breath

> "Imagine three scouts filed reports on the same fifteen players — and the
> reports don't agree. My job was to build the single trusted record: decide
> whose numbers to use for each stat, write down every disagreement instead
> of hiding it, and make sure re-running the whole thing never double-counts
> anything."

---

## The big ideas, one analogy each

### We issue our own player IDs → **the passport office**
Each source calls players something different — the API uses ID numbers,
RealGM just uses names. I issue every player one internal "passport," and a
translation table maps each source's nickname to it. New source shows up?
We add entries to the phone book — we never reissue passports.
*Proof it works:* the live game feed arrived with five brand-new players and
plugged in without changing anything.

### One row per player-season; team is a detail → **the yearbook page**
Each player gets one page per season. Which team they wore is written *on*
the page — it's not what the page is filed under. Why? Because the sources
literally disagree about Agravanis's 2022-23 team (Napoli vs Panathinaikos).
File by team and one season would split into two pages that each look real.
*Trade-off I volunteer:* a player traded mid-season really needs two
"chapters" on that page — that's the "stints" feature on my roadmap.

### Every number cites its source → **footnotes in a research paper**
One season row is a blend — points might come from RealGM while the team name
comes from the API. So every single value carries a footnote: who supplied it,
and when. Want a different source to win a stat? Change one line of config,
and the footnote updates itself on the next run.

### Who wins each stat → **a depth chart**
For every stat there's a depth chart: who starts, who's the backup if the
starter didn't show. RealGM starts for box-score detail (the API doesn't
carry it), the API starts for advanced metrics (RealGM doesn't carry them),
and for stats both report, the API starts with RealGM backing up. And like
any depth chart, the coach can override it for one matchup — there's a
one-line override list for special cases.

### Every disagreement gets written down → **the referee's incident report**
Even when the "trusted" source wins, the disagreement is logged with both
values. Why bother? Because the trusted source had its own planted error
(a club listed as a league). **The depth chart is a default, not gospel** —
the incident report is how a human notices the default was wrong, and a
one-line override is how they fix it.

### Nothing is thrown away silently → **the lost-and-found desk**
A row that can't be loaded doesn't vanish — it goes to lost-and-found with a
tag saying exactly why. Full accounting: 71 rows arrived, 69 loaded, 1 exact
duplicate skipped, 1 suspicious near-duplicate tagged and shelved. I can tell
you the fate of every single row.

### Safe to re-run, always → **a scoreboard, not a counter**
The pipeline *sets* values, it doesn't *add* them — like a scoreboard showing
78, not a clicker you press 78 times. Run everything twice and the second run
changes nothing (I prove this in a test: photograph every table, re-run it
all, compare — zero differences). This matters because in the real world
things retry, replay, and arrive twice. Boring re-runs are the feature.

### Old updates can't overwrite new ones → **the scoreboard never runs backwards**
Each live update carries a timestamp, and the database refuses to apply an
older update over a newer one. So if a delayed poll from the 3rd quarter
arrives after the final buzzer — nothing happens. Tested, not assumed.

### Trust but verify the math → **re-adding the receipt yourself**
The API hands us True Shooting % pre-computed. I also compute it myself from
the raw makes and attempts and compare. The check flagged exactly four
player-seasons: the three errors planted in the data **plus one nobody
planted** (the Agravanis team dispute). A check that catches both bad data
and bad assumptions earns its keep — that's the pattern I'd reuse for the
next stat (eFG%).

---

## Game night, in plain English

**Two different problems wearing one name:**

- **The writes (game data coming in)** → *a kitchen with a short ticket
  rail.* A few hundred small updates a second is a slow night for Postgres.
  And the safety rules are already built: re-sent updates change nothing,
  late updates get ignored.
- **The reads (coaches checking in)** → *everyone rushing the concession
  stand at halftime.* This is the real risk: thousands of people asking the
  same question at the same moment.

**The three fixes, as analogies:**

1. **Connection pooling** → *bank tellers.* Don't let every customer walk
   into the vault; a small number of tellers serve an orderly line. (The
   thing that actually takes the database down on a spiky night isn't the
   data volume — it's too many simultaneous open conversations.)
2. **Caching** → *one person checks the score and announces it to the room.*
   500 coaches refreshing the same game becomes one database question every
   few seconds. The score updates every ~5 seconds anyway, so nobody gets
   stale information.
3. **Read replicas** → *photocopies of the record book.* Readers use the
   copies; only the scorekeeper writes in the master. Just watch that the
   copies don't lag behind the master by more than a scoreboard tick.

**Monitoring** → *smoke detectors, not fire inspections.* Standing alarms:
"has any source gone quiet mid-game?", "is the rejection shelf filling up
faster than normal?", "are the photocopies falling behind the master?"

**Portal cycle** (their other big load event) → different problem entirely:
not a firehose of updates, but *hundreds of players changing jerseys in a
week.* My design is already shaped for it — a player's identity is permanent,
the jersey is just a detail on the page. A transfer is an edit, not a new
person.

---

## Where I'd take it next — the plain version

1. **Blueprints before renovation** — right now I can build the database from
   scratch but not remodel a live one. Real migration tooling comes first.
2. **Delivery tracking** — today I can see what's on the shelf but not
   whether the truck actually ran. A small log of "source X ran at time Y,
   delivered N rows" turns "is anything stale?" into one query and one alarm.
3. **Put the schedule on autopilot** (Prefect — their tool): nightly API run,
   scrape-cadence RealGM run, live feed on game nights. The pieces already
   exist; this is wiring, not rebuilding.
4. **Keep the game film** — store raw deliveries as received, so when the
   merge rules change we can re-grade history under the new rules instead of
   hoping the sources still have old data.
5. **Two jerseys, one season** — model mid-season trades properly (stints).

---

## Numbers to have cold

- **15** players · **71** CSV rows = **69 + 1 + 1** (loaded / exact dupe /
  near-dupe shelved)
- **4** TS% flags = **3 planted errors + 1 real ambiguity** (Agravanis)
- **5** brand-new players in the live feed → **zero** schema changes
- Re-run everything → **zero** differences

## Words to swap on the call

- ~~Dagster/Airflow~~ → **Prefect** (their scheduler)
- monitoring → **Datadog** (their alarm system)
- Docker Postgres → **RDS/Aurora on AWS** (their hosting)

## If you only remember three lines

1. "No disagreement is ever silent, and no source is ever load-bearing."
2. "The depth chart is a default, not gospel — the conflict log is how a
   human finds out the default was wrong."
3. "Game night is two problems: the kitchen is fine — it's the line at the
   concession stand you have to design for."
