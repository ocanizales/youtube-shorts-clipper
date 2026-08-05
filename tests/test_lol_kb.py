"""League knowledge base: whisper biasing, mishear repair, entity -> hashtags.
Run: .venv/bin/python tests/test_lol_kb.py
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import lol_kb as kb


# ── whisper biasing ──────────────────────────────────────────────────────────
def test_whisper_prompt_names_the_roster_and_stays_short():
    p = kb.whisper_prompt()
    for ign in ("Faker", "Keria", "Oner", "Gumayusi"):
        assert ign in p, f"{ign} missing from the decoder bias"
    assert "Baron Nashor" in p and "LCK" in p
    # Past ~200 tokens the prompt starts steering content, not just spelling.
    assert len(p.split()) < 200, "bias prompt is long enough to steer content"


# ── mishear repair: safe table ───────────────────────────────────────────────
def test_safe_repairs_need_no_context():
    assert kb.correct_text("fake her goes in") == "Faker goes in"
    assert kb.correct_text("penta kill") == "Pentakill"
    assert kb.correct_text("baron nasher is down") == "Baron Nashor is down"


def test_multiword_repair_keeps_one_span_of_timing():
    """'fake her' 1.0->1.6 must become ONE token still covering 1.0->1.6 —
    the ASS builder needs a start and an end on every caption word."""
    words = [("fake", 1.0, 1.3), ("her", 1.3, 1.6), ("wins", 1.6, 2.0)]
    out = kb.correct_words(words)
    assert out[0] == ("Faker", 1.0, 1.6), out[0]
    assert out[1] == ("wins", 1.6, 2.0)


def test_repair_carries_sentence_punctuation_across_a_merge():
    out = kb.correct_words([("penta", 1.0, 1.2), ("kill!", 1.2, 1.5)])
    assert out[0][0] == "Pentakill!", out[0][0]


# ── mishear repair: contextual table is gated ────────────────────────────────
def test_contextual_repair_fires_only_inside_a_league_clip():
    assert kb.correct_text("the owner ganks mid") != "the owner ganks mid", \
        "League context should promote 'owner' to Oner"
    assert "Oner" in kb.correct_text("the owner ganks mid")
    # No League context anywhere -> the ordinary English reading survives.
    assert kb.correct_text("the owner sold the shop") == "the owner sold the shop"


def test_ambiguous_words_are_never_rewritten():
    """'Korea' is a word LCK casters genuinely use. A confident wrong caption is
    worse than a mishear, so these are handled by decoder biasing, not rewriting."""
    for word in ("korea", "career", "coma", "gang", "pays"):
        assert (word,) not in kb.CONTEXTUAL_PHRASES, f"{word} is too ambiguous to rewrite"
    assert "Korea" in kb.correct_text("Korea wins the Baron fight")


# ── entity detection -> hashtags ─────────────────────────────────────────────
def test_detects_players_and_infers_their_team():
    ents = kb.detect_entities("Faker with the outplay")
    assert "Faker" in ents["players"]
    assert "T1" in ents["teams"], "a player implies their team"


def test_niche_hashtags_prefer_the_specific():
    tags = kb.niche_hashtags("Faker steals Baron at Worlds", limit=3)
    assert "Faker" in tags and "T1" in tags
    assert all(" " not in t for t in tags), "hashtags cannot contain spaces"


def test_niche_hashtags_fall_back_when_nothing_is_named():
    tags = kb.niche_hashtags("what a play by the mid laner", limit=3)
    assert len(tags) >= 2 and "LeagueOfLegends" in tags


def test_context_brief_only_covers_what_the_clip_mentions():
    brief = kb.context_brief("Faker with the pentakill")
    assert "Faker" in brief and "T1" in brief
    assert "LPL" not in brief, "briefing leaked entities the clip never mentioned"
    assert kb.context_brief("just a random highlight") == ""


# ── wrong-team / wrong-player regressions ────────────────────────────────────
# The user's report was "titles have super incorrect teams and players". Two
# distinct causes, one test each, plus the check that catches whatever slips past
# both. Do not relax these into "assert something was detected" — the whole point
# is WHICH names come out.

# Cause 1: the KB knew only T1, so any other clip produced an EMPTY briefing and
# the metadata prompt still demanded a name. The model supplied T1 from memory.
NON_T1_CLIP = ("Chovy with the flank! Gen.G looking to close this out, Kiin "
               "holding top and Peanut collapsing. Ruler steps up for the "
               "shutdown on Zeka. HLE cannot answer here.")


def test_non_t1_clip_is_identified_rather_than_left_blank():
    ents = kb.detect_entities(NON_T1_CLIP)
    assert "Chovy" in ents["players"] and "Kiin" in ents["players"]
    assert "Gen.G" in ents["teams"], "the team actually named went undetected"
    assert "T1" not in ents["teams"], "attributed a non-T1 clip to T1"
    brief = kb.context_brief(NON_T1_CLIP)
    assert brief, "empty briefing is what invites the model to invent a team"
    assert "Gen.G" in brief


def test_briefing_never_asserts_a_team_it_cannot_vouch_for():
    """Players carry no team in the table, so the brief must say so out loud
    rather than print a bare role the model will happily complete itself."""
    brief = kb.context_brief("Chovy on the Azir, incredible shuffle")
    assert "Chovy" in brief
    assert "do not name one" in brief.lower()


# Cause 2: IGNs that are ordinary English words matched bare, and the
# player-implies-team rule then asserted T1 over clips containing no T1 at all.
def test_ordinary_english_is_not_a_roster():
    for line in ("What a bang that was, the wolf pack collapses mid!",
                 "That is a wolf in sheep's clothing from the jungler",
                 "He goes for the duke play here, huge bang from the ult"):
        ents = kb.detect_entities(line)
        assert ents["players"] == [], f"invented players from: {line}"
        assert ents["teams"] == [], f"invented a team from: {line}"


def test_ordinary_english_is_not_a_team_either():
    ents = kb.detect_entities("Add the liquid slowly, huge impact on flavour")
    assert ents["teams"] == [], "a cooking video was attributed to Team Liquid"


def test_capitalised_ambiguous_ign_still_counts_in_a_league_clip():
    """The guard must not cost us the real players — Bang and Wolf are T1."""
    ents = kb.detect_entities("Bang steps up for the pentakill, Wolf lands the "
                              "hook, T1 close it out")
    assert "Bang" in ents["players"] and "Wolf" in ents["players"]
    assert "T1" in ents["teams"]


def test_dorans_blade_is_an_item_not_the_top_laner():
    for line in ("He buys Doran's Blade and a ward, then backs",
                 "Second Dorans Ring going into the mid lane"):
        assert "Doran" not in kb.detect_entities(line)["players"], line


# The net that catches whatever the prompt does not.
def test_ungrounded_names_flags_a_team_the_clip_never_mentioned():
    bad = kb.ungrounded_names("T1 take the Baron", NON_T1_CLIP, "")
    assert "T1" in bad, "an invented team passed the grounding check"


def test_ungrounded_names_accepts_what_the_sources_support():
    assert kb.ungrounded_names("Gen.G close it out", NON_T1_CLIP, "") == []
    # An alias is the same entity, not an invention.
    assert kb.ungrounded_names("SKT win it", "T1 win the series", "") == []
    # …and the briefing counts as a source, not just the transcript.
    assert kb.ungrounded_names("Faker goes in", "", "- Faker: mid for T1.") == []


def test_ungrounded_names_ignores_ordinary_english_igns():
    """'a clutch steal' must not be flagged for containing an IGN-shaped word."""
    assert kb.ungrounded_names("what an impact that had", "no names here") == []


def test_ungrounded_claims_catches_an_invented_highlight():
    """Observed live: a clip whose commentary never says Baron came back with the
    hook 'Massive Baron Steal'. Wrong in the same way a wrong team is wrong."""
    clip = "Massive engage onto three, the flash ult connects, that's a triple kill"
    assert "baron steal" in kb.ungrounded_claims("Massive Baron Steal", clip)
    # What the commentary does support must pass untouched.
    assert kb.ungrounded_claims("insane triple kill", clip) == []


def test_a_mentioned_objective_does_not_ground_a_claim_about_it():
    """'Baron is up' is not evidence that anybody stole it."""
    assert "baron steal" in kb.ungrounded_claims("baron steal!", "Baron is up in 30")


def test_ungrounded_claims_ignores_unfalsifiable_praise():
    """'outplay' and 'insane' are judgements, not claims — never flag them."""
    assert kb.ungrounded_claims("an insane outplay, absolutely clean", "") == []


def test_whisper_bias_covers_more_than_t1_and_stays_short():
    p = kb.whisper_prompt()
    for ign in ("Chovy", "Caps", "Knight"):
        assert ign in p, f"{ign} missing — Whisper will mangle it"
    assert "Gen.G" in p, "team names must be biased too, not just players"
    assert len(p.split()) < 200, "bias prompt is long enough to steer content"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"PASS {name}")
    print("lol_kb tests passed")
