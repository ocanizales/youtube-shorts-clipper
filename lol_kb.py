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
    # Every other org gets a ONE-LINE blurb on purpose. `context_brief` is capped
    # at 700 chars and a clip naming three teams would otherwise spend the whole
    # budget on history the title does not need. T1 keeps its long blurb because
    # it is this channel's subject; the rest only need to be identifiable.
    #
    # Blurbs here carry NO standings, NO "current champions", NO dated results.
    # Org names are stable facts; placings go stale in weeks and a stale placing
    # in a published description is exactly the failure this module exists to
    # prevent. Titles and results belong to the commentary, not to this table.
    "Gen.G": {
        "region": "LCK", "aka": ("GenG", "Gen G", "Generation Gaming", "GEN"),
        "blurb": "Perennial LCK title contender.", "tags": ("GenG", "LCK"),
    },
    "Hanwha Life Esports": {
        "region": "LCK", "aka": ("HLE", "Hanwha Life", "Hanwha"),
        "blurb": "LCK org, formerly ROX Tigers.", "tags": ("HLE", "LCK"),
    },
    "Dplus KIA": {
        "region": "LCK", "aka": ("DK", "DWG KIA", "DAMWON", "Damwon Gaming",
                                 "DWG", "Dplus"),
        "blurb": "LCK org and 2020 World Champion as DAMWON Gaming.",
        "tags": ("DplusKIA", "LCK"),
    },
    "KT Rolster": {
        "region": "LCK", "aka": ("KT", "kt Rolster"),
        "blurb": "One of the oldest LCK organisations.", "tags": ("KT", "LCK"),
    },
    "DRX": {
        "region": "LCK", "aka": ("Detonation FocusMe KR",),
        "blurb": "LCK org and 2022 World Champion.", "tags": ("DRX", "LCK"),
    },
    "Nongshim RedForce": {
        "region": "LCK", "aka": ("NS RedForce", "Nongshim", "NSRF"),
        "blurb": "LCK organisation.", "tags": ("Nongshim", "LCK"),
    },
    "OK BRION": {
        "region": "LCK", "aka": ("BRO", "BRION", "OKBRION"),
        "blurb": "LCK organisation.", "tags": ("BRION", "LCK"),
    },
    "BNK FearX": {
        "region": "LCK", "aka": ("FearX", "BNK", "Fredit BRION"),
        "blurb": "LCK organisation.", "tags": ("FearX", "LCK"),
    },
    "JD Gaming": {
        "region": "LPL", "aka": ("JDG",),
        "blurb": "LPL powerhouse.", "tags": ("JDG", "LPL"),
    },
    "Bilibili Gaming": {
        "region": "LPL", "aka": ("BLG",),
        "blurb": "LPL title contender.", "tags": ("BLG", "LPL"),
    },
    "Top Esports": {
        "region": "LPL", "aka": ("TES", "TOP Esports"),
        "blurb": "LPL organisation.", "tags": ("TopEsports", "LPL"),
    },
    "Weibo Gaming": {
        "region": "LPL", "aka": ("WBG", "Suning"),
        "blurb": "LPL organisation, formerly Suning.", "tags": ("WBG", "LPL"),
    },
    "LNG Esports": {
        "region": "LPL", "aka": ("LNG",),
        "blurb": "LPL organisation.", "tags": ("LNG", "LPL"),
    },
    "EDward Gaming": {
        "region": "LPL", "aka": ("EDG", "Edward Gaming"),
        "blurb": "LPL org and 2021 World Champion.", "tags": ("EDG", "LPL"),
    },
    "Royal Never Give Up": {
        "region": "LPL", "aka": ("RNG",),
        "blurb": "Historic LPL organisation.", "tags": ("RNG", "LPL"),
    },
    "Invictus Gaming": {
        "region": "LPL", "aka": ("IG",),
        "blurb": "LPL org and 2018 World Champion.", "tags": ("InvictusGaming", "LPL"),
    },
    "FunPlus Phoenix": {
        "region": "LPL", "aka": ("FPX",),
        "blurb": "LPL org and 2019 World Champion.", "tags": ("FPX", "LPL"),
    },
    "G2 Esports": {
        "region": "LEC", "aka": ("G2",),
        "blurb": "The most decorated Western organisation.", "tags": ("G2", "LEC"),
    },
    "Fnatic": {
        "region": "LEC", "aka": ("FNC", "FNATIC"),
        "blurb": "LEC org and winner of the 2011 World Championship.",
        "tags": ("Fnatic", "LEC"),
    },
    "MAD Lions": {
        "region": "LEC", "aka": ("MAD", "MAD Lions KOI", "KOI"),
        "blurb": "LEC organisation.", "tags": ("MADLions", "LEC"),
    },
    "Team Vitality": {
        "region": "LEC", "aka": ("VIT", "Vitality"),
        "blurb": "LEC organisation.", "tags": ("Vitality", "LEC"),
    },
    "Karmine Corp": {
        "region": "LEC", "aka": ("KC", "KCorp"),
        "blurb": "LEC organisation with a large French fanbase.",
        "tags": ("KarmineCorp", "LEC"),
    },
    "Team Heretics": {
        "region": "LEC", "aka": ("TH", "Heretics"),
        "blurb": "LEC organisation.", "tags": ("TeamHeretics", "LEC"),
    },
    "SK Gaming": {
        "region": "LEC", "aka": ("SKG",),
        "blurb": "Long-running European organisation.", "tags": ("SKGaming", "LEC"),
    },
    "GiantX": {
        "region": "LEC", "aka": ("GX", "Excel", "Excel Esports"),
        "blurb": "LEC organisation.", "tags": ("GiantX", "LEC"),
    },
    "Cloud9": {
        "region": "LTA", "aka": ("C9", "Cloud 9"),
        "blurb": "Long-running North American organisation.", "tags": ("Cloud9", "LTA"),
    },
    "Team Liquid": {
        "region": "LTA", "aka": ("TL", "Liquid"),
        "blurb": "Long-running North American organisation.",
        "tags": ("TeamLiquid", "LTA"),
    },
    "100 Thieves": {
        "region": "LTA", "aka": ("100T", "Hundred Thieves"),
        "blurb": "North American organisation.", "tags": ("100Thieves", "LTA"),
    },
    "FlyQuest": {
        "region": "LTA", "aka": ("FLY",),
        "blurb": "North American organisation.", "tags": ("FlyQuest", "LTA"),
    },
    "NRG": {
        "region": "LTA", "aka": ("NRG Esports",),
        "blurb": "North American organisation.", "tags": ("NRG", "LTA"),
    },
    "Shopify Rebellion": {
        "region": "LTA", "aka": ("SR",),
        "blurb": "North American organisation.", "tags": ("ShopifyRebellion", "LTA"),
    },
    "Dignitas": {
        "region": "LTA", "aka": ("DIG",),
        "blurb": "Long-running North American organisation.", "tags": ("Dignitas", "LTA"),
    },
}

# ign -> (full name, role, team, tags). Historical players keep their era's team.
#
# A deliberate asymmetry: the T1 block below carries a team, everyone after it
# carries "". That is not laziness, it is the same rule the blurbs follow.
# Rosters churn every offseason, and `detect_entities` turns a player into their
# team ("a player implies their team even when the caster never says it"), so a
# stale team here does not just sit in a table — it manufactures a wrong team
# attribution and feeds it to the metadata model as fact. T1 is this channel's
# own subject and the user maintains it against `docs/reference/lol-database.md`;
# for everyone else the *role* is the durable fact and the team belongs to the
# commentary. `context_brief` prints a roleless-teamless player fine, and an
# empty team is skipped by the implication rule.
#
# What these entries are actually for: teaching Whisper to spell the name
# (`whisper_prompt`), letting `_ollama_metadata` recognise a real player instead
# of inventing one, and earning a specific hashtag.
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

    # ── LCK ──
    "Chovy":     ("Jeong Ji-hoon", "mid",      "", ("Chovy",)),
    "Ruler":     ("Park Jae-hyuk", "ADC",      "", ("Ruler",)),
    "Kiin":      ("Kim Gi-in", "top",          "", ("Kiin",)),
    "Peanut":    ("Han Wang-ho", "jungle",     "", ("Peanut",)),
    "Canyon":    ("Kim Geon-bu", "jungle",     "", ("Canyon",)),
    "ShowMaker": ("Heo Su", "mid",             "", ("ShowMaker",)),
    "Deft":      ("Kim Hyuk-kyu", "ADC",       "", ("Deft",)),
    "Viper":     ("Park Do-hyeon", "ADC",      "", ("Viper",)),
    "Zeka":      ("Kim Geon-woo", "mid",       "", ("Zeka",)),
    "BeryL":     ("Cho Geon-hee", "support",   "", ("BeryL",)),
    "Lehends":   ("Son Si-woo", "support",     "", ("Lehends",)),
    "Bdd":       ("Gwak Bo-seong", "mid",      "", ("Bdd",)),
    "Kingen":    ("Hwang Seong-hoon", "top",   "", ("Kingen",)),
    "Teddy":     ("Park Jin-seong", "ADC",     "", ("Teddy",)),
    "Rascal":    ("Kim Kwang-hee", "top",      "", ("Rascal",)),
    "Morgan":    ("Park Lu-hal", "top",        "", ("Morgan",)),
    "Delight":   ("Yoo Hwan-joong", "support", "", ("Delight",)),
    "Clid":      ("Kim Tae-min", "jungle",     "", ("Clid",)),

    # ── LPL ──
    "Bin":       ("Chen Ze-Bin", "top",        "", ("Bin",)),
    "Knight":    ("Zhuo Ding", "mid",          "", ("Knight",)),
    "Elk":       ("Zhao Jia-Hao", "ADC",       "", ("Elk",)),
    "Xun":       ("Peng Li-Xun", "jungle",     "", ("Xun",)),
    "Kanavi":    ("Seo Jin-hyeok", "jungle",   "", ("Kanavi",)),
    "369":       ("Bai Jia-Hao", "top",        "", ("369",)),
    "JackeyLove": ("Yu Wen-Bo", "ADC",         "", ("JackeyLove",)),
    "Tian":      ("Gao Tian-Liang", "jungle",  "", ("Tian",)),
    "Scout":     ("Lee Ye-chan", "mid",        "", ("Scout",)),
    "Tarzan":    ("Lee Seung-yong", "jungle",  "", ("Tarzan",)),
    "TheShy":    ("Kang Seung-lok", "top",     "", ("TheShy",)),
    "Rookie":    ("Song Eui-jin", "mid",       "", ("Rookie",)),
    "Uzi":       ("Jian Zi-Hao", "ADC",        "", ("Uzi",)),
    "GALA":      ("Chen Wei", "ADC",           "", ("GALA",)),
    "Wei":       ("Yan Yang-Wei", "jungle",    "", ("Wei",)),
    "Ming":      ("Shi Sen-Ming", "support",   "", ("Ming",)),

    # ── LEC ──
    "Caps":      ("Rasmus Winther", "mid",     "", ("Caps",)),
    "BrokenBlade": ("Sergen Celik", "top",     "", ("BrokenBlade",)),
    "Yike":      ("Martin Sundelin", "jungle", "", ("Yike",)),
    "Hans Sama": ("Steven Liv", "ADC",         "", ("HansSama",)),
    "Mikyx":     ("Mihael Mehle", "support",   "", ("Mikyx",)),
    "Rekkles":   ("Martin Larsson", "ADC",     "", ("Rekkles",)),
    "Jankos":    ("Marcin Jankowski", "jungle", "", ("Jankos",)),
    "Perkz":     ("Luka Perkovic", "mid",      "", ("Perkz",)),
    "Humanoid":  ("Marek Brazda", "mid",       "", ("Humanoid",)),
    "Razork":    ("Ivan Martin Diaz", "jungle", "", ("Razork",)),
    "Upset":     ("Elias Lipp", "ADC",         "", ("Upset",)),
    "Hylissang": ("Zdravets Galabov", "support", "", ("Hylissang",)),
    "Odoamne":   ("Andrei Pascu", "top",       "", ("Odoamne",)),

    # ── LTA / North America ──
    "Bjergsen":  ("Soren Bjerg", "mid",        "", ("Bjergsen",)),
    "Doublelift": ("Yiliang Peng", "ADC",      "", ("Doublelift",)),
    "CoreJJ":    ("Jo Yong-in", "support",     "", ("CoreJJ",)),
    "Blaber":    ("Robert Huang", "jungle",    "", ("Blaber",)),
    "Berserker": ("Kim Min-cheol", "ADC",      "", ("Berserker",)),
    "Jojopyun":  ("Joseph Pyun", "mid",        "", ("Jojopyun",)),
    "Impact":    ("Jung Eon-yeong", "top",     "", ("Impact",)),
    "Inspired":  ("Kacper Sloma", "jungle",    "", ("Inspired",)),
    "Fudge":     ("Ibrahim Allami", "top",     "", ("Fudge",)),
    "Zven":      ("Jesper Svenningsen", "ADC", "", ("Zven",)),
}

# ── 3b. IGNs that are also ordinary English words ────────────────────────────
# The same principle as CONTEXTUAL_PHRASES, applied to entity *detection* rather
# than to caption repair — and it was missing, which is the second half of why
# titles named the wrong team. `detect_entities` lowercases before matching, so
# "what a bang that was, the wolf pack collapses mid" used to detect the players
# Bang and Wolf, and the player-implies-team rule then asserted T1 over a clip
# with no T1 in it. The model was not hallucinating there; it was told.
#
# These IGNs need TWO signals instead of one: League context, and a capitalised
# occurrence in the original transcript. Whisper capitalises names it recognises
# and leaves ordinary nouns lowercase, so the case carries real information —
# which is exactly why `detect_entities` must test the raw text, not `low`.
AMBIGUOUS_IGNS = frozenset({
    "Bang", "Wolf", "Duke", "Ruler", "Peanut", "Bin", "Knight", "Rookie",
    "Scout", "Impact", "Caps", "Canyon", "Deft", "Delight", "Elk", "Viper",
    "Wei", "Ming", "Upset", "Inspired", "Fudge", "Tian", "Morgan", "Zeus",
})

# The same trap on the team side: several orgs are known by an ordinary English
# word. "Add the liquid slowly, huge impact on flavour" detected Team Liquid off
# a bare alias match — a cooking video attributed to the LTA. Guarded exactly
# like the IGNs above: context plus the original capitalisation.
AMBIGUOUS_TEAM_FORMS = frozenset({
    "Liquid", "MAD", "Excel", "KOI", "GEN", "Vitality", "Heretics", "Rogue",
})

# Curating that set by hand was the actual defect, not a gap in it. Every org
# also carries a 2-3 letter tag, and a hand-list of "ordinary words" will never
# think to include `BRO` — so a streamer saying "bro I don't even know" was
# detected as OK BRION, briefed to the model as "LCK organisation", and
# published as "OK BRION takes on Sp Az in an intense LCK match". `DIG` and
# `FLY` reproduce it verbatim (Dignitas, FlyQuest).
#
# So the rule is derived rather than listed: a short tag is never enough on its
# own. Real esports clips are unaffected because they clear both signals easily
# — casters say "baron"/"dragon"/"LCK" constantly (context) and Whisper writes a
# recognised tag in caps (capitalisation). What it costs is the one case that
# should cost: a tag shouted into an otherwise contextless stream.
_SHORT_TAG_MAX = 3


def _ambiguous_team_surface(surface: str) -> bool:
    """True if `surface` may not attribute a clip on its own evidence."""
    return len(surface) <= _SHORT_TAG_MAX or surface in AMBIGUOUS_TEAM_FORMS

# `Doran` is both a T1 top laner and the starting-item line every caster says a
# dozen times a game. The possessive is the tell: "Doran's Blade" is never the
# player, and the apostrophe is not a word character so the plain boundary match
# happily fired on it.
ITEM_POSSESSIVE_TAILS = ("blade", "ring", "shield", "blades", "rings", "shields")

# Faker's nicknames get their own line because casters use them constantly.
FAKER_EPITHETS = ("The Unkillable Demon King", "Demon King", "The GOAT")

CURRENT_T1 = ("Doran", "Oner", "Faker", "Peyz", "Keria")
ZOFGK = ("Zeus", "Oner", "Faker", "Gumayusi", "Keria")   # 2022-2024 era


# ── 4. whisper biasing ───────────────────────────────────────────────────────
# Whisper's `initial_prompt` is a decoding hint, not an instruction: tokens that
# appear in it become cheaper to emit. Keep it dense with proper nouns and short
# — the prompt eats context that the audio needs, and past ~200 tokens it starts
# steering content instead of just spelling.
# A CURATED subset, not `PLAYERS`. The table is ~70 names now and joining all of
# them would push this past the point where the hint stops spelling and starts
# steering — the decoder would begin hearing Faker in clips that never say him,
# which is the same wrong-name failure one layer earlier. Bias for the names most
# likely to be *said* and most likely to be *mangled*; the rest are still repaired
# after the fact by `correct_words`.
WHISPER_BIAS_PLAYERS = (
    "Faker", "Keria", "Oner", "Gumayusi", "Doran", "Peyz", "Zeus",
    "Chovy", "Ruler", "Kiin", "Peanut", "Canyon", "ShowMaker", "Deft",
    "Viper", "Zeka", "BeryL", "Lehends", "Bdd", "Kanavi", "Scout",
    "Bin", "Knight", "Elk", "Xun", "JackeyLove", "TheShy", "Rookie",
    "Caps", "BrokenBlade", "Mikyx", "Rekkles", "Jankos", "Humanoid",
    "Bjergsen", "CoreJJ", "Blaber", "Berserker", "Jojopyun",
)

# Team names get biased too, and that is not incidental: "Gen.G" decoded as
# "Jenji" is a team the KB then cannot detect, which empties the briefing and
# hands the metadata model the blank page it fills with T1.
WHISPER_BIAS_TEAMS = (
    "T1", "Gen.G", "Hanwha Life", "HLE", "Dplus KIA", "KT Rolster", "DRX",
    "JDG", "BLG", "Top Esports", "Weibo Gaming", "LNG", "EDward Gaming",
    "G2 Esports", "Fnatic", "Karmine Corp", "Team Vitality",
    "Cloud9", "Team Liquid", "100 Thieves", "FlyQuest",
)


def whisper_prompt() -> str:
    """Vocabulary hint fed to Whisper so esports proper nouns survive decoding."""
    names = ", ".join(WHISPER_BIAS_PLAYERS)
    teams = ", ".join(WHISPER_BIAS_TEAMS)
    return (f"League of Legends esports commentary. Players: {names}. "
            f"Teams: {teams}. Leagues: LCK, LPL, LEC, LTA. "
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

# Org names distinctive enough to prove a clip is League on their own. Curated
# by hand rather than derived from TEAMS, because `has_context` needs only ONE
# hit to open the CONTEXTUAL tables and the short aliases would wreck that: "IG",
# "TL", "SR", "KC" turn up constantly in ordinary speech, and "Liquid" and
# "Impact" are plain English. A permissive gate has to be fed unambiguous tokens.
_STRONG_TEAM_TOKENS = frozenset({
    "geng", "hle", "hanwha", "dplus", "damwon", "dwg", "drx", "nongshim",
    "brion", "fearx", "rolster", "jdg", "blg", "wbg", "lng", "edg", "rng",
    "fpx", "fnatic", "invictus", "bilibili", "weibo", "g2", "madlions",
    "vitality", "karmine", "heretics", "giantx", "cloud9", "flyquest",
    "doublelift", "bjergsen", "chovy", "showmaker", "jackeylove", "theshy",
})

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
    hits += sum(1 for t in toks if t in strong or t in _STRONG_TEAM_TOKENS)
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
def _capitalised_in(text: str, needle: str) -> bool:
    """True if `needle` appears in the RAW text with its KB capitalisation.

    The discriminator for `AMBIGUOUS_IGNS`. Whisper capitalises proper nouns it
    recognises and leaves ordinary words alone, so "Bang" and "bang" are genuinely
    different evidence — information `detect_entities` throws away when it
    lowercases. Case-sensitive by design; do not "fix" it to ignore case.
    """
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(needle)}(?![A-Za-z0-9])",
                     text) is not None


def _is_item_reference(text: str, ign: str) -> bool:
    """True for "Doran's Blade" / "Doran blade" — the item line, not the player.

    An apostrophe is not a word character, so the plain boundary match in
    `detect_entities` treats "Doran's" as a clean hit on "Doran".
    """
    tails = "|".join(ITEM_POSSESSIVE_TAILS)
    return re.search(rf"(?<![a-z0-9]){re.escape(ign.lower())}'?s?\s+({tails})"
                     rf"(?![a-z0-9])", text.lower()) is not None


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
    ctx = has_context(text)

    def seen(needle: str) -> bool:
        return re.search(rf"(?<![a-z0-9]){re.escape(needle.lower())}(?![a-z0-9])",
                         low) is not None

    for ign in PLAYERS:
        if not seen(ign) or _is_item_reference(text, ign):
            continue
        # An ordinary-English IGN needs a second signal before it counts as a
        # person: League context AND the capitalisation Whisper gives names it
        # recognises. One signal is what attributed "the wolf pack" to T1.
        if ign in AMBIGUOUS_IGNS and not (ctx and _capitalised_in(text, ign)):
            continue
        found["players"].append(ign)
    for team, info in TEAMS.items():
        for surface in (team, *info["aka"]):
            if not seen(surface):
                continue
            if _ambiguous_team_surface(surface) and \
               not (ctx and _capitalised_in(text, surface)):
                continue
            found["teams"].append(team)
            break
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

    NOTHING is briefed about a clip that is not recognisably League commentary.
    That gate matters more than it looks, because `ungrounded_names` checks the
    model's output against this briefing: whatever lands here is, by definition,
    a name the model is then allowed to publish. Deriving the briefing from a
    detection and then validating against the briefing verifies nothing — one
    false positive becomes a licensed claim. The transcript has to earn a
    briefing first, and a clip with no League vocabulary in it never does.
    """
    text = text or ""
    if not has_context(text):
        return ""
    ents = detect_entities(text)
    lines: list[str] = []
    for ign in ents["players"][:4]:
        name, role, team, _ = PLAYERS[ign]
        who = f"{ign}" + (f" ({name})" if name else "")
        # No team in the table means the roster fact is not one we can vouch for
        # (see the PLAYERS comment). Say that out loud rather than omitting it:
        # an unqualified "- Chovy: mid." invites a 3B model to supply the team
        # it half-remembers, which is the exact failure this brief exists to stop.
        where = (f" for {team}" if team else
                 " — this briefing does NOT say which team, do not name one "
                 "unless the commentary does")
        lines.append(f"- {who}: {role}{where}."
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


# ── 7. grounding check on generated text ─────────────────────────────────────
# Prompting a 3B model not to invent names lowers the rate; it does not make the
# rate zero, and the failure is silent — a fluent, confident, wrong team name.
# But the vocabulary of things it can get wrong is CLOSED: every real team and
# player is in the tables above. So a name in the model's output that appears in
# neither the transcript nor the briefing is, by construction, invented. That is
# checkable after the fact, and cheap.
#
# Same principle as the Ollama verdict rule in the committee work: on a failed
# check, fall back honestly — never ship the unverified answer.

# Tournament keys that are two spellings of one event, so "Worlds" counts as
# grounded when the commentary said "World Championship".
_EVENT_ALIASES = {
    "Worlds": ("Worlds", "World Championship"),
    "World Championship": ("Worlds", "World Championship"),
    "MSI": ("MSI", "Mid-Season Invitational"),
    "Mid-Season Invitational": ("MSI", "Mid-Season Invitational"),
    "EWC": ("EWC", "Esports World Cup"),
    "Esports World Cup": ("EWC", "Esports World Cup"),
}


def _mentions(text: str, needle: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(needle.lower())}(?![a-z0-9])",
                     f" {text.lower()} ") is not None


def entity_surface_forms() -> dict[str, tuple[str, ...]]:
    """Canonical entity name -> every string that means it.

    Aliases are grouped so that naming T1 as "SKT", or Worlds as "the World
    Championship", counts as grounded rather than invented.
    """
    forms: dict[str, tuple[str, ...]] = {}
    for team, info in TEAMS.items():
        forms[team] = (team,) + tuple(info["aka"])
    for ign in PLAYERS:
        forms.setdefault(ign, (ign,))
    for lg in LEAGUES:
        forms.setdefault(lg, (lg,))
    for ev in TOURNAMENTS:
        forms.setdefault(ev, _EVENT_ALIASES.get(ev, (ev,)))
    return forms


# Concrete claims about what HAPPENED, as opposed to who was there. Same closed
# vocabulary, same trick — and needed for the same reason: a clip whose hook read
# "Massive Baron Steal" over commentary that never says Baron is wrong in exactly
# the way a wrong team name is wrong, and no amount of prompting reliably stops a
# 3B model reaching for the most clickable esports noun it knows.
#
# Only falsifiable events belong here. "outplay" and "insane" are judgements —
# they cannot be checked against a transcript and are not the model's to get
# wrong. A surface form matching ANY listed spelling grounds the claim.
CHECKABLE_EVENTS = {
    "pentakill":    ("pentakill", "penta kill"),
    "quadra kill":  ("quadra kill", "quadrakill"),
    "triple kill":  ("triple kill", "triplekill"),
    "double kill":  ("double kill", "doublekill"),
    "baron steal":  ("baron steal",),
    "Baron":        ("baron", "nashor"),
    "first blood":  ("first blood",),
    "dragon soul":  ("dragon soul",),
    "Elder Dragon": ("elder dragon",),
    "backdoor":     ("backdoor",),
    "Nexus":        ("nexus",),
    "ace":          ("ace",),
}


def ungrounded_claims(text: str, *sources: str) -> list[str]:
    """Events `text` asserts that none of `sources` support.

    The companion to `ungrounded_names`: that one catches "T1" on a Gen.G clip,
    this one catches "Baron Steal" on a clip with no Baron in it. Note that
    "baron steal" is not grounded by a bare "baron" — claiming the steal is a
    claim about the play, not just about the objective being mentioned.
    """
    if not text:
        return []
    src = " \n ".join(s for s in sources if s)
    return sorted(canon for canon, surfaces in CHECKABLE_EVENTS.items()
                  if any(_mentions(text, s) for s in surfaces)
                  and not any(_mentions(src, s) for s in surfaces))


def ungrounded_names(text: str, *sources: str) -> list[str]:
    """Entity names used in `text` that appear in none of `sources`.

    `sources` is what the model was allowed to know — normally the transcript
    and the briefing. A non-empty result means the output asserts a player,
    team, league or event that nothing supports: the caller should fall back to
    a title it can stand behind rather than publish it.

    `AMBIGUOUS_IGNS` are skipped outright. A description reading "a clutch
    steal" must not be flagged for containing the IGN "Steal" — the whole point
    of that set is that the ordinary reading is the likely one.
    """
    if not text:
        return []
    src = " \n ".join(s for s in sources if s)
    out: list[str] = []
    for canon, surfaces in entity_surface_forms().items():
        if canon in AMBIGUOUS_IGNS:
            continue
        if any(_names_entity(text, s) for s in surfaces) and \
           not any(_names_entity(src, s) for s in surfaces):
            out.append(canon)
    return sorted(out)


def _names_entity(text: str, surface: str) -> bool:
    """True if `text` uses `surface` to mean the org, not as an ordinary word.

    `_mentions` lowercases both sides, which makes "bro I don't even know" read
    as a mention of OK BRION. That is wrong on both sides of the grounding check
    and in opposite directions: on the source side a filler word GROUNDS an
    invented team, and on the output side an innocent "bro" in a description
    gets FLAGGED as naming one. Short tags therefore carry the same
    capitalisation requirement `detect_entities` puts on them; full names and
    long aliases are unambiguous and stay case-insensitive.
    """
    if _ambiguous_team_surface(surface):
        return _capitalised_in(text, surface)
    return _mentions(text, surface)
