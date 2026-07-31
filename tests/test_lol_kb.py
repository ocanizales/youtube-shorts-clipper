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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"PASS {name}")
    print("lol_kb tests passed")
