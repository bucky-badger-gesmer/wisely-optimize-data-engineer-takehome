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

### One row per player-season; team is a detail → **the folder label**
In a filing cabinet, the folder label decides what gets filed together. I
label folders "Agravanis — 2022-23" and put the team *inside* the folder as a
fact to check. Why not on the label? The two sources disagree about his
2022-23 team (Napoli vs Panathinaikos). If team were on the label, the second
report wouldn't match the first folder — a clerk would just open a *second*
folder, and one season would quietly become two official-looking records.
Same label → both reports land in one folder → the disagreement is impossible
to miss, and we have to resolve it. **Rule: the label only carries what no
source can disagree about (who + which season); everything else goes inside.**
*Trade-off I volunteer:* a player traded mid-season really did play for two
teams — that folder needs a first-half/second-half divider. That's the
"stints" feature on my roadmap.

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

### Updates fill blanks, they don't erase → **merging two contact cards**
When one source re-delivers its data, only the fields it actually filled in
get written — a blank on the new card never erases a phone number you already
had. That's why re-loading just RealGM can't wipe out the stats the API owns.
*Honest flip side:* the same politeness means a source can never *retract* a
number by sending a blank — fixing that is on the roadmap.

### Impossible values bounce at the door → **the ID check at the club**
Whatever the pipeline's logic says, the database itself refuses obvious
nonsense: an age of 200, a shooting percentage over 100%, more makes than
attempts. It's the last line of defense — it catches not just bad data but
*bugs in my own code*.

### Trust but verify the math → **re-adding the receipt yourself**
The API hands us True Shooting % pre-computed. I also compute it myself from
the raw makes and attempts and compare. The check flagged exactly four
player-seasons: the three errors planted in the data **plus one nobody
planted** (the Agravanis team dispute). A check that catches both bad data
and bad assumptions earns its keep — that's the pattern I'd reuse for the
next stat, eFG%, which wins because the ingredients are already in the
pantry *and* there's an independent receipt to check it against.

### Testing where mistakes hide → **counting the money twice, not checking the pens**
I didn't test everything equally — I tested where a wrong answer would *look
right*: name spellings with unusual characters, minutes written as "34:30"
vs "34.5", divide-by-zero math, and the promise that re-running changes
nothing. And I wrote the tests *before* the fixes, from studying the raw
data first — like scouting film before the game, so you know exactly which
plays to prepare for.

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

**Their other three busy seasons** (each a different problem wearing the
same "high usage" label):

- **Portal cycle** → not a firehose of updates, but *hundreds of players
  changing jerseys in a week.* My design is already shaped for it — a
  player's identity is permanent, the jersey is just a detail inside the
  folder. A transfer is an edit, not a new person.
- **Preseason** → *restocking the whole warehouse at once* — bulk loads and
  backfills. That's the "keep the game film" idea: because raw deliveries
  are stored, re-loading history is replaying tape, not hoping the sources
  still have it.
- **Conference play** → *every night is game night.* Not a bigger spike — a
  grind. The danger shifts from overload to fatigue: something quietly
  breaking on night 40 and nobody noticing. That's why the alarms are smoke
  detectors (they go off on their own), and why recovery from any bad night
  is "re-run it," never a manual repair job.

---

## The tools, in plain English

### Postgres → **the courthouse record**
Postgres is the database itself — the filing cabinet all the analogies above
live inside. What makes it the right home for the master record:

- **Written in ink, on disk.** Power goes out mid-write? Either the whole
  change landed or none of it did — never half. (That all-or-nothing promise
  is called a *transaction*.)
- **The clerk enforces the rules.** The "ID check at the club" from earlier —
  no age of 200, no more makes than attempts — is enforced by the record
  office itself, not by whoever's dropping off paperwork.
- **Relationships are real.** A stat line can't point at a player who doesn't
  exist; folders can't orphan. The structure polices itself.
- **Boring on purpose.** Thirty years old, runs half the internet, endlessly
  documented. For the single source of truth, boring is a feature — you want
  the courthouse, not a startup's experimental filing app.
- And the project's key guarantees — safe re-runs, "newer beats older" — are
  built from Postgres's own machinery, which is why the tests run against
  real Postgres and not a stand-in.

### Redis → **the whiteboard by the phone**
Redis keeps everything in RAM (memory), not on disk — a whiteboard, not a
ledger. Blazing fast to read and write, but wipeable. Famous for caching,
though it's really a general "fast scratch space": counters, queues,
leaderboards.

The distinction that matters — **what happens if the whiteboard gets erased?**

- **As a cache** (fine, standard): the whiteboard holds a *copy* of what the
  courthouse already recorded — today's score, so 500 callers don't all
  march into the records room. Erased? Nothing is lost; the next caller
  checks the ledger and rewrites the board.
- **As the source of truth** (avoid until forced): if the *only* place the
  live score exists is the whiteboard, an erased board is a real loss. Now
  you're running two record systems and worrying about them agreeing.

One-liner for the call: "I'd use Redis where losing it costs nothing — a
request collapser in front of the reads. I wouldn't put game state in it
until Postgres measurably can't keep up, because a cache you can lose is
cheap, but a second source of truth is expensive."

### Prefect → **the shift manager**
A plain alarm clock (cron) can start a job, but it doesn't notice when the
job fails at 3am, doesn't retry it, and keeps no attendance record. Prefect
is the shift manager: it starts each job on schedule, retries the flaky
step (just that step, not the whole shift), keeps a logbook of every run,
and calls someone when a shift is missed. My pipeline's pieces are already
the right shape to hand it — this is hiring a manager, not rebuilding the
team.

### Datadog → **the building's alarm panel**
One panel collecting signals from every room: how fast things are running,
how full things are getting, what errored where. Two kinds of displays —
**dashboards** (screens a human looks at when diagnosing) and **monitors**
(alarms that watch themselves and page you). The design rule: nobody stares
at a screen at 2am, so anything that matters gets an alarm, not a chart —
"has the feed gone quiet mid-game?" pages someone; it doesn't wait to be
noticed.

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
6. **Same recipes, real kitchen** — the project already runs as containers
   with one command; production is the same containers moved into AWS's
   managed kitchen (ECS for the app, RDS for the database, S3 as the
   walk-in freezer for raw deliveries). Nothing gets rewritten — it gets
   re-housed.

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
