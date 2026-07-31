"""
League of Legends domain knowledge base — game, pro scene, and T1.

Two consumers, both of which used to guess:

  1. **Captions** (`clipper.make_dynamic_captions`). Whisper has never heard of
     Keria or Nashor, so it reaches for the nearest ordinary English word:
     "Korea", "Baron nature", "owner". Two defences here — `whisper_prompt()`
     biases the decoder toward the real vocabulary *before* it commits, and
     `correct_words()` repairs what still slips through *after*.

  2. **Metadata** (`clipper._ollama_metadata` / `build_description`).
     `context_brief()` tells a 3B model who is on screen, and
     `detect_entities()` turns what was actually said into the niche hashtags
     the Shorts SEO rules ask for — #T1 #Faker beats a hardcoded #Gaming.

Source: the user's own reference dump (2026-07-31), kept verbatim in
`docs/reference/lol-database.md`. Roster facts go stale — when a player moves,
edit the tables here and the note there together.

Design note on corrections: a *wrong* fix is worse than a mishear, because it
puts a confident falsehood on screen. So the phrase table is split in two.
SAFE entries are distinctive enough to be unambiguous in any context
("penta kill", "baron nasher"). CONTEXTUAL entries collide with ordinary
English — "Korea", "owner", "coma", "gang" — and only fire once the clip has
already proven it is a League clip (`has_context`). A cooking video that says
"gang" keeps its word.
"""

import re

# ── 1. game vocabulary ───────────────────────────────────────────────────────
ROLES = ("top lane", "jungle", "jungler", "mid lane", "bot lane", "ADC",
         "attack damage carry", "support")

OBJECTIVES = ("Nexus", "Baron Nashor", "Baron", "Elder Dragon", "Dragon Soul",
              "drake", "dragon", "turret", "tower", "inhibitor", "Super Minions",
              "Hand of Baron", "Summoner's Rift")

GAME_TERMS = ("gank", "ward", "vision", "crowd control", "CC", "engage",
              "flash", "ultimate", "ult", "teamfight", "skirmish", "split push",
              "backdoor", "ace", "pentakill", "quadra kill", "triple kill",
              "double kill", "first blood", "steal", "smite", "recall", "macro")

# Moment words that make good niche hashtags when they actually get said.
HIGHLIGHT_MOMENTS = ("pentakill", "quadra kill", "baron steal", "ace",
                     "backdoor", "outplay", "first blood", "dragon soul")

# ── 2. pro scene ─────────────────────────────────────────────────────────────
LEAGUES = {
    "LCK": "Korea — historically the most dominant region; macro control and "
           "calculated, mechanically precise play.",
    "LPL": "China — aggressive, fast-paced skirmishing and team-fighting.",
    "LEC": "Europe — creative drafts and unconventional macro.",
    "LCS": "North America — the NA competitive ecosystem.",
    "LTA": "The Americas — North and South American competitive ecosystem.",
}

TOURNAMENTS = {
    "Worlds": "World Championship — the annual Q4 pinnacle event.",
    "World Championship": "The annual Q4 pinnacle event.",
    "MSI": "Mid-Season Invitational — mid-year, regional champions.",
    "Mid-Season Invitational": "Mid-year event featuring regional champions.",
    "EWC": "Esports World Cup — supplemental major.",
    "Esports World Cup": "Supplemental major international tournament.",
    "First Stand": "Supplemental major international tournament.",
    "KeSPA Cup": "Korean domestic tournament.",
}

# ── 3. teams & players ───────────────────────────────────────────────────────
# `tags` are the hashtags an entity earns when it is detected in a transcript.
TEAMS = {
    "T1": {
        "region": "LCK",
        "aka": ("SK Telecom T1", "SKT T1", "SKT"),
        "blurb": "The most decorated organisation in League history: 6 Worlds "
                 "titles (2013, 2015, 2016, 2023, 2024, 2025), 10 LCK titles, "
                 "2 MSI titles, 1 Esports World Cup, 1 KeSPA Cup. The first and "
                 "only three-peat World Champion (2023-2024-2025).",
        "tags": ("T1", "LCK"),
    },
}

# ign -> (full name, role, team, tags). Historical players keep their era's team.
PLAYERS = {
    "Faker":     ("Lee Sang-hyeok", "mid",     "T1", ("Faker", "T1")),
    "Doran":     ("Choi Hyeon-joon", "top",    "T1", ("T1",)),
    "Oner":      ("Mun Hyeon-jun", "jungle",   "T1", ("T1",)),
    "Peyz":      ("Kim Su-hwan", "ADC",        "T1", ("T1",)),
    "Keria":     ("Ryu Min-seok", "support",   "T1", ("Keria", "T1")),
    "kkOma":     ("Kim Jeong-gyun", "head coach", "T1", ("T1",)),
    "Zeus":      ("", "top",     "T1", ("T1",)),
    "Gumayusi":  ("", "ADC",     "T1", ("T1",)),
    "Bengi":     ("", "jungle",  "T1", ("T1",)),
    "Bang":      ("", "ADC",     "T1", ("T1",)),
    "Wolf":      ("", "support", "T1", ("T1",)),
    "MaRin":     ("", "top",     "T1", ("T1",)),
    "Duke":      ("", "top",     "T1", ("T1",)),
}

# Faker's nicknames get their own line because casters use them constantly.
FAKER_EPITHETS = ("The Unkillable Demon King", "Demon King", "The GOAT")

CURRENT_T1 = ("Doran", "Oner", "Faker", "Peyz", "Keria")
ZOFGK = ("Zeus", "Oner", "Faker", "Gumayusi", "Keria")   # 2022-2024 era


# ── 4. whisper biasing ───────────────────────────────────────────────────────
# Whisper's `initial_prompt` is a decoding hint, not an instruction: tokens that
# appear in it become cheaper to emit. Keep it dense with proper nouns and short
# — the prompt eats context that the audio needs, and past ~200 tokens it starts
# steering content instead of just spelling.
def whisper_prompt() -> str:
    """Vocabulary hint fed to Whisper so esports proper nouns survive decoding."""
    names = ", ".join(PLAYERS)
    return (f"League of Legends esports commentary. Players: {names}. "
            f"Teams: T1. Leagues: LCK, LPL, LEC, LCS. "
            f"Events: Worlds, MSI, KeSPA Cup. "
            f"Terms: Baron Nashor, Elder Dragon, Dragon Soul, Summoner's Rift, "
            f"inhibitor, pentakill, quadra kill, ace, gank, ward, flash, "
            f"teamfight, backdoor, Nexus.")


# ── 5. mishear repair ────────────────────────────────────────────────────────
# Keys are normalised token tuples (see `_norm`); values are the canonical text.
# SAFE: distinctive enough that a match is a match in any video.
SAFE_PHRASES: dict[tuple[str, ...], str] = {
    ("fake", "her"): "Faker",
    ("faker",): "Faker",
    ("fakir",): "Faker",
    ("baron", "nasher"): "Baron Nashor",
    ("baron", "nature"): "Baron Nashor",
    ("baron", "nashore"): "Baron Nashor",
    ("barren", "nashor"): "Baron Nashor",
    ("nashor",): "Nashor",
    ("penta", "kill"): "Pentakill",
    ("pentakill",): "Pentakill",
    ("quadra", "kill"): "Quadra Kill",
    ("summoners", "rift"): "Summoner's Rift",
    ("summoner", "rift"): "Summoner's Rift",
    ("guma", "yusi"): "Gumayusi",
    ("gumayushi",): "Gumayusi",
    ("elder", "drake"): "Elder Dragon",
    ("dragon", "soul"): "Dragon Soul",
    ("super", "minions"): "Super Minions",
    ("inhib",): "inhibitor",
    ("l", "c", "k"): "LCK",
    ("elsie", "k"): "LCK",
    ("l", "p", "l"): "LPL",
    ("a", "d", "c"): "ADC",
    ("t", "one"): "T1",
    ("tea", "one"): "T1",
    ("kespa",): "KeSPA",
    ("worlds",): "Worlds",
}

# CONTEXTUAL: real English words that are only mishears inside a League clip.
# Entries here are chosen so the *ordinary* reading is implausible in esports
# commentary. Deliberately NOT here: "Korea" (LCK casters say it constantly and
# mean the country), "career", "coma", "gang", "pays". Those collide with words
# a caster genuinely uses, and the fix for them is `whisper_prompt()` biasing
# the decoder up front — not a rewrite that would be confidently wrong.
CONTEXTUAL_PHRASES: dict[tuple[str, ...], str] = {
    ("kariya",): "Keria",
    ("owner",): "Oner",
    ("one", "er"): "Oner",
    ("koma",): "kkOma",
    ("peas",): "Peyz",
    ("dorian",): "Doran",
    ("duran",): "Doran",
    ("gonk",): "gank",
    ("bengie",): "Bengi",
}

# A clip has to clear this many SAFE hits before CONTEXTUAL repairs are allowed.
CONTEXT_MIN_HITS = 1
_MAX_PHRASE = max(len(k) for k in {**SAFE_PHRASES, **CONTEXTUAL_PHRASES})


def _norm(word: str) -> str:
    """Lowercase, punctuation-free form used for every lookup."""
    return re.sub(r"[^a-z0-9]", "", word.lower())


def has_context(text: str) -> bool:
    """True if the text is clearly League commentary.

    Gate for the CONTEXTUAL table. Deliberately cheap and generous: a single
    unambiguous League noun is enough, because the alternative table entries
    are only reachable from here anyway.
    """
    toks = [_norm(w) for w in text.split()]
    hits = sum(1 for n in range(1, _MAX_PHRASE + 1)
               for i in range(len(toks) - n + 1)
               if tuple(toks[i:i + n]) in SAFE_PHRASES)
    # Words that only really turn up in a League clip. "mid" and "top" are
    # deliberately absent — far too ordinary to prove anything on their own.
    strong = {"nexus", "baron", "nashor", "dragon", "drake", "inhibitor",
              "jungler", "jungle", "gank", "ganks", "ganked", "ganking",
              "teamfight", "pentakill", "quadra", "backdoor", "summoners",
              "rift", "minions", "turret", "adc", "midlane", "botlane",
              "lck", "lpl", "lec", "t1", "faker", "riot", "league", "worlds"}
    hits += sum(1 for t in toks if t in strong)
    return hits >= CONTEXT_MIN_HITS


def _table(text: str | None) -> dict[tuple[str, ...], str]:
    """SAFE always; CONTEXTUAL only once the clip has proven itself."""
    if text is not None and has_context(text):
        return {**SAFE_PHRASES, **CONTEXTUAL_PHRASES}
    return dict(SAFE_PHRASES)


def correct_words(words: list[tuple[str, float, float]],
                  ) -> list[tuple[str, float, float]]:
    """Repair mishears in a Whisper word list, preserving timing.

    `words` is [(text, start, end)] as produced by word_timestamps=True.
    A multi-word mishear collapses into ONE caption token spanning the whole
    original span ("fake her" 1.2->1.6 becomes "Faker" 1.2->1.6), which is why
    this operates on the word list rather than on the joined transcript — the
    ASS builder needs every token to keep a start and an end.
    """
    if not words:
        return words
    table = _table(" ".join(w for w, _, _ in words))
    out: list[tuple[str, float, float]] = []
    i = 0
    while i < len(words):
        for n in range(min(_MAX_PHRASE, len(words) - i), 0, -1):
            key = tuple(_norm(words[i + k][0]) for k in range(n))
            if "" in key or key not in table:
                continue
            fixed = table[key]
            # Carry the last token's sentence punctuation across the merge.
            tail = re.search(r"[.,!?]+$", words[i + n - 1][0])
            out.append((fixed + (tail.group() if tail else ""),
                        words[i][1], words[i + n - 1][2]))
            i += n
            break
        else:
            out.append(words[i])
            i += 1
    return out


def correct_text(text: str) -> str:
    """Same repairs against free text (transcript, title hook, AI input)."""
    if not text:
        return text
    words = [(w, 0.0, 0.0) for w in text.split()]
    return " ".join(w for w, _, _ in correct_words(words))


# ── 6. entity detection -> hashtags & AI context ─────────────────────────────
def detect_entities(text: str) -> dict[str, list[str]]:
    """What the commentary actually mentions: players, teams, leagues, moments.

    Runs on corrected text, so "Korea" has already become "Keria" by the time
    we look. Returns lists in mention order, deduplicated.
    """
    found: dict[str, list[str]] = {"players": [], "teams": [], "leagues": [],
                                   "events": [], "moments": []}
    if not text:
        return found
    low = f" {text.lower()} "

    def seen(needle: str) -> bool:
        return re.search(rf"(?<![a-z0-9]){re.escape(needle.lower())}(?![a-z0-9])",
                         low) is not None

    for ign in PLAYERS:
        if seen(ign):
            found["players"].append(ign)
    for team, info in TEAMS.items():
        if seen(team) or any(seen(a) for a in info["aka"]):
            found["teams"].append(team)
    for lg in LEAGUES:
        if seen(lg):
            found["leagues"].append(lg)
    for ev in TOURNAMENTS:
        if seen(ev):
            found["events"].append(ev)
    for m in HIGHLIGHT_MOMENTS:
        if seen(m) or seen(m.replace(" ", "")):
            found["moments"].append(m)
    # A player implies their team even when the caster never says it.
    for ign in found["players"]:
        team = PLAYERS[ign][2]
        if team and team not in found["teams"]:
            found["teams"].append(team)
    return found


def _tagify(text: str) -> str:
    """'baron steal' -> 'BaronSteal' (YouTube hashtags cannot contain spaces)."""
    return "".join(p[:1].upper() + p[1:] for p in re.split(r"[\s_-]+", text) if p)


def niche_hashtags(text: str, limit: int = 3) -> list[str]:
    """The 2-3 *specific* hashtags the Shorts SEO rules ask for.

    Priority is specificity: a named player out-targets a team, which out-targets
    a league, which out-targets a generic highlight word. Falls back to the
    evergreen League tags when the transcript gives us nothing.
    """
    ents = detect_entities(text or "")
    ordered: list[str] = []
    for ign in ents["players"]:
        ordered.extend(PLAYERS[ign][3])
    for team in ents["teams"]:
        ordered.extend(TEAMS[team]["tags"])
    ordered.extend(ents["leagues"])
    ordered.extend(_tagify(m) for m in ents["moments"])
    ordered.extend(_tagify(e) for e in ents["events"])
    out: list[str] = []
    for t in ordered:
        tag = _tagify(t) if " " in t else t
        if tag.lower() not in {o.lower() for o in out}:
            out.append(tag)
        if len(out) >= limit:
            break
    while len(out) < min(2, limit):                     # never ship a bare #Shorts
        for fb in ("LeagueOfLegends", "LoL"):
            if fb.lower() not in {o.lower() for o in out}:
                out.append(fb)
                break
        else:
            break
    return out[:limit]


def context_brief(text: str = "", limit: int = 700) -> str:
    """Compact domain briefing injected into the Ollama metadata prompt.

    Only the entities this clip actually mentions — a 3B model given the whole
    knowledge base writes about the knowledge base instead of the clip.
    """
    ents = detect_entities(text or "")
    lines: list[str] = []
    for ign in ents["players"][:4]:
        name, role, team, _ = PLAYERS[ign]
        who = f"{ign}" + (f" ({name})" if name else "")
        lines.append(f"- {who}: {role} for {team}."
                     + (f" {'; '.join(FAKER_EPITHETS[:2])}. Six-time world "
                        f"champion, widely called the greatest player ever."
                        if ign == "Faker" else ""))
    for team in ents["teams"][:2]:
        lines.append(f"- {team}: {TEAMS[team]['blurb']}")
    for lg in ents["leagues"][:2]:
        lines.append(f"- {lg}: {LEAGUES[lg]}")
    for ev in ents["events"][:2]:
        lines.append(f"- {ev}: {TOURNAMENTS[ev]}")
    for m in ents["moments"][:3]:
        lines.append(f"- '{m}' is a highlight-worthy play; say so plainly.")
    brief = "\n".join(lines)
    return brief[:limit]
