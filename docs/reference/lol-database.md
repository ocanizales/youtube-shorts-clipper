# League of Legends database reference

Supplied by the user 2026-07-31 as the standing domain reference for this repo:
**caption correction** and **description generation**. Kept verbatim below.

The machine-readable form lives in [`lol_kb.py`](../../lol_kb.py) — the tables
there are what the code actually reads. **When a roster changes, edit both.**
This note is the source of truth for *facts*; `lol_kb.py` is the source of truth
for *spellings and hashtags*.

## How the code uses it

| Consumer | Function | What it does |
|---|---|---|
| Captions | `lol_kb.whisper_prompt()` | Feeds the roster + game vocabulary to Whisper as `initial_prompt`, so "Keria" is a cheaper token than "Korea" during decoding. |
| Captions | `lol_kb.correct_words()` | Repairs mishears that still get through, on the timed word list so captions keep their timing. |
| Metadata | `lol_kb.context_brief()` | Briefs the local 3B model on only the entities this clip mentions. Without it the model invents biographies. |
| Metadata | `lol_kb.niche_hashtags()` | Turns detected entities into the 2–3 specific hashtags the Shorts SEO rules want (#Faker #T1 beats another #Gaming). |
| Metadata | `lol_kb.ungrounded_names()` | Checks generated text back against the transcript + briefing. A team or player named in neither was invented; `_ollama_metadata` retries once, then falls back. |
| Metadata | `lol_kb.ungrounded_claims()` | Same check for *events* — "Baron Steal" over commentary that never says Baron. |

Corrections are split SAFE / CONTEXTUAL on purpose — see the module docstring.
Words a caster genuinely uses ("Korea", "career", "gang") are **never** rewritten;
they are handled by decoder biasing, because a confidently wrong caption is worse
than a mishear.

---

## 1. League of Legends core game knowledge

### Win condition & map structure
* Map: Summoner's Rift (5v5 symmetrical map divided by a river).
* Primary objective: destroy the enemy Nexus located inside the opposing base.
* Lanes: Top, Mid, Bot (Bottom), and the Jungle (neutral areas between lanes).

### The five roles
* **Top lane** — typically tanks, bruisers, or split-pushing champions. Frontline
  survival and individual side-lane pressure.
* **Jungle** — operates in neutral territory. Clears monster camps, secures global
  objectives, executes ganks (surprise attacks on lanes).
* **Mid lane** — high-damage mages or assassins. Central position allows fast
  rotations to both top and bottom lanes.
* **Bot lane / ADC (Attack Damage Carry)** — ranged physical damage dealer. High
  late-game damage output but requires protection due to low durability.
* **Support** — partners with the ADC in the bot lane. Provides map vision via
  wards, crowd control (CC), team protection, or initiation.

### Key neutral objectives
* **Turrets & inhibitors** — defensive towers protecting lanes. Destroying an
  enemy inhibitor causes empowered Super Minions to spawn for that lane.
* **Dragons / drakes** — elemental bosses yielding permanent team buffs. Killing
  4 dragons awards a game-altering Dragon Soul. The Elder Dragon spawns later,
  granting a temporary execution buff.
* **Baron Nashor** — high-value late-game monster. Eliminating Baron grants the
  Hand of Baron buff, powering up nearby friendly minions to destroy enemy
  defenses.

---

## 2. Professional esports structure

### Major regional leagues
* **LCK (Korea)** — historically the most dominant regional league globally.
  Known for macro control, calculated play, and high mechanical skill.
* **LPL (China)** — known for aggressive, fast-paced skirmishes and team-fighting.
* **LEC (Europe)** — recognized for creative drafts and unconventional macro play.
* **LTA / LCS (Americas)** — the primary North and South American competitive
  ecosystem.

### Primary international tournaments
1. **World Championship (Worlds)** — the annual pinnacle event in Q4 featuring
   the top teams globally.
2. **Mid-Season Invitational (MSI)** — mid-year event featuring regional champions.
3. **Esports World Cup (EWC) / First Stand** — supplemental major international
   tournaments on the competitive circuit.

---

## 3. T1 deep dive & historic data

### Profile overview
* Organization: **T1** (formerly SK Telecom T1 / SKT T1).
* Region: LCK (South Korea).
* Status: the most successful and decorated esports organization in League of
  Legends history.

### Achievements & titles
* **World Championships: 6-time champions** (2013, 2015, 2016, 2023, 2024, 2025)
* **LCK regional titles: 10-time champions** (all-time Korean record)
* **Mid-Season Invitational: 2-time champions** (2016, 2017)
* **Esports World Cup: 1-time champions** (2024)
* **KeSPA Cup: 1-time champions** (2025)

> T1 is the first and only team to complete a World Championship three-peat
> (winning 2023, 2024, and 2025 consecutively).

### Franchise player: Lee "Faker" Sang-hyeok
* Role: mid laner / team captain.
* Titles: "The GOAT", "The Unkillable Demon King".
* Legacy: the undisputed greatest player in League of Legends history. Debuted
  with SKT in 2013 and has stayed with T1 throughout his career, winning all six
  of the franchise's World Championships.

### Key rosters

**Current roster**

| Role | Player |
|---|---|
| Top | Choi "Doran" Hyeon-joon |
| Jungle | Mun "Oner" Hyeon-jun |
| Mid | Lee "Faker" Sang-hyeok |
| ADC | Kim "Peyz" Su-hwan |
| Support | Ryu "Keria" Min-seok |
| Head coach | Kim "kkOma" Jeong-gyun |

**Notable historical lineups**

* **"ZOFGK" era (2022–2024)** — Zeus, Oner, Faker, Gumayusi, Keria. Three straight
  Worlds final appearances, winning back-to-back titles in 2023 and 2024.
* **Classic SKT dominance (2015–2016)** — MaRin/Duke, Bengi, Faker, Bang, Wolf.
  Back-to-back Worlds titles in 2015 and 2016 with unmatched dominance.

---

## 4. Scope beyond T1 (added 2026-08-05)

Sections 1–3 are the user's verbatim dump and stay that way. This section records
what `lol_kb.py` carries **in addition**, and why it is shaped the way it is.

### Why it was added

Titles were coming back with the wrong teams and players. Two causes, both
measured before the fix:

1. **The tables knew only T1.** For a Gen.G/HLE clip naming Chovy, Kiin, Peanut,
   Ruler and Zeka, `detect_entities` returned *nothing* and `context_brief`
   returned `""`. The prompt still ordered the model to "lead with the player or
   team name" and to "use the names from the briefing" — with no briefing. A 3B
   model handed that gap fills it from pretraining, and what it knows about
   League is T1 and Faker.
2. **Ordinary English words were treated as roster hits.** "What a bang that was,
   the wolf pack collapses mid" detected the players *Bang* and *Wolf*, and the
   player-implies-team rule then asserted **T1** over a clip containing no T1.
   The model was not hallucinating there — it was being told.

### What is in the tables now

* **~35 organisations** across LCK / LPL / LEC / LTA, each with its aliases
  (`Gen.G` / `GenG` / `GEN`, `Dplus KIA` / `DWG` / `DAMWON`).
* **~60 additional players** with full name and role.

### The deliberate gap: no team on non-T1 players

Every player added outside the T1 block carries `team=""`, on purpose.

`detect_entities` promotes a player to their team even when the caster never says
it. That is useful and correct for T1, whose roster the user maintains here. For
everyone else it is a trap: rosters churn every offseason, and a stale team in
this table would not sit quietly — it would manufacture a false attribution and
hand it to the metadata model *as briefing fact*, which is the exact failure this
whole module exists to prevent.

So the durable fact (a player's **role**) is recorded and the volatile one (their
**team**) is left to the commentary. `context_brief` states the absence out loud —
"this briefing does NOT say which team, do not name one unless the commentary
does" — because an unqualified "- Chovy: mid." is an invitation to guess.

**If you add a team to a player here, you are asserting a roster you must then
maintain.** Prefer leaving it empty.

Blurbs follow the same rule: no standings, no "current champions", no dated
placings. Org identity is stable; results go stale in weeks.

### Ambiguity guards

`AMBIGUOUS_IGNS` and `AMBIGUOUS_TEAM_FORMS` list the names that are also ordinary
English — *Bang, Wolf, Duke, Ruler, Peanut, Bin, Knight, Scout, Caps, Impact,
Liquid, MAD, Excel*. These need two signals before they count as a person or an
org: League context **and** the capitalisation Whisper gives names it recognises.
This is the same SAFE/CONTEXTUAL discipline the caption tables already used,
finally applied to entity detection.

`ITEM_POSSESSIVE_TAILS` handles the one collision inside the game itself:
"Doran's Blade" is the starting item, not T1's top laner.

### Grounding

Prompting lowers the invention rate; it does not zero it, and the failure is
silent. But the vocabulary of nameable things is closed, so `ungrounded_names`
and `ungrounded_claims` can check the model's output back against the transcript
and the briefing after the fact. On a hit, `_ollama_metadata` retries once naming
the offending words, then falls back to the hook title rather than publishing
something it cannot support.

Aliases resolve before the check, so calling T1 "SKT" or Worlds "the World
Championship" counts as grounded rather than invented.
