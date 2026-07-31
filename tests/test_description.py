"""Shorts description SEO + the two shipped framings.
Run: .venv/bin/python tests/test_description.py

Rules under test come from docs/reference/shorts-description-seo.md.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import clipper as c


HOOK = "Faker steals Baron to win the series"
BODY = ("T1's mid laner smites Baron out from under the enemy jungler in game "
        "five, flipping the series on the spot at Worlds.")


def _first_line(desc):
    return desc.splitlines()[0]


# ── the 125-character preview window ─────────────────────────────────────────
def test_first_line_fits_the_preview_window():
    """Everything past ~125 chars sits behind '...more' and most viewers never
    expand it, so the keyword and the hashtags have to clear that bar."""
    tags = c.shorts_hashtags("Faker steals Baron at Worlds", "youtube")
    desc = c.build_description(HOOK, BODY, tags)
    assert len(_first_line(desc)) <= c.SHORTS_PREVIEW_CHARS, _first_line(desc)


def test_hashtags_survive_when_the_hook_is_too_long():
    """The hook gets trimmed, never the tags — tags are what categorise the
    video, the hook is only what sells it."""
    tags = c.shorts_hashtags("Faker", "youtube")
    desc = c.build_description("word " * 80, BODY, tags)
    line = _first_line(desc)
    assert len(line) <= c.SHORTS_PREVIEW_CHARS
    for t in tags:
        assert f"#{t}" in line, f"#{t} was dropped to make room for the hook"
    assert not line.endswith(" "), "trimmed hook left trailing whitespace"


def test_primary_keyword_leads_the_first_line():
    tags = c.shorts_hashtags("Faker steals Baron", "youtube")
    line = _first_line(c.build_description(HOOK, BODY, tags))
    assert line.lower().startswith("faker"), line


# ── hashtag strategy: #Shorts + broad + 2-3 niche ────────────────────────────
def test_shorts_tag_is_present_and_first():
    tags = c.shorts_hashtags("Faker steals Baron at Worlds", "youtube")
    assert tags[0] == "Shorts", tags


def test_tag_count_stays_in_the_three_to_five_band():
    for transcript in ("Faker steals Baron at Worlds with a pentakill",
                       "what a play", ""):
        tags = c.shorts_hashtags(transcript, "youtube")
        assert c.SHORTS_TAGS_MIN <= len(tags) <= c.SHORTS_TAGS_MAX, (transcript, tags)


def test_tags_are_specific_when_the_clip_names_someone():
    tags = c.shorts_hashtags("Faker steals Baron at Worlds", "youtube")
    assert "Faker" in tags and "T1" in tags, tags
    assert "Gaming" in tags, "still needs one broad category tag"


def test_no_duplicate_tags():
    tags = c.shorts_hashtags("Faker T1 LCK Faker T1", "youtube")
    assert len({t.lower() for t in tags}) == len(tags), tags


# ── structure & ceilings ─────────────────────────────────────────────────────
def test_cta_lands_in_the_middle_band():
    """Links/CTAs belong below the preview line and before ~500 chars.

    Asserted against the end of line 1 rather than a fixed offset of 125: the
    preview line is <=125, not ==125, so a short hook legitimately puts the CTA
    earlier. What must hold is that the CTA never competes with the keyword and
    hashtags for the preview, and is still early enough to be seen on expand.
    """
    tags = c.shorts_hashtags("Faker", "youtube")
    desc = c.build_description(HOOK, BODY, tags, endcard="Faker's settings -> pinned comment")
    at = desc.index("Faker's settings")
    assert at > len(_first_line(desc)), "CTA leaked into the preview line"
    assert at < 500, at


def test_description_respects_the_five_thousand_ceiling():
    tags = c.shorts_hashtags("Faker", "youtube")
    desc = c.build_description(HOOK, "body. " * 2000, tags)
    assert len(desc) <= c.SHORTS_DESC_MAX


def test_body_keywords_are_kept_for_indexing():
    tags = c.shorts_hashtags("Faker", "youtube")
    desc = c.build_description(HOOK, BODY, tags)
    assert "jungler" in desc and "Worlds" in desc, "secondary keywords were dropped"


# ── titles stay clean ────────────────────────────────────────────────────────
def test_title_carries_no_hashtag(tmp=None):
    """`#` in a title is parsed by YouTube as a hashtag: it burns title
    characters and files the clip under a junk feed. Index is bracketed."""
    import tempfile, pathlib as pl
    with tempfile.TemporaryDirectory() as d:
        clip = pl.Path(d) / "short_03_12s.mp4"
        clip.write_bytes(b"")
        c.write_metadata(clip, "LoL Highlight", 3, "youtube", "Faker steals Baron",
                         transcript="Faker steals Baron at Worlds")
        side = c._read_sidecar(clip)
    assert "#" not in side["TITLE"], side["TITLE"]
    assert side["TITLE"].endswith("[3]"), side["TITLE"]
    assert "#Shorts" in side["CAPTION"], "hashtags belong in the description"


# ── framing: only the two the user kept ──────────────────────────────────────
def test_only_full_and_zoom_are_selectable():
    assert c.LAYOUTS == ("full", "zoom"), c.LAYOUTS
    import argparse, contextlib, io
    for bad in ("crop", "split"):
        p = argparse.ArgumentParser()
        p.add_argument("--layout", choices=c.LAYOUTS, default="full")
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                p.parse_args(["--layout", bad])
            except SystemExit:
                continue
        raise AssertionError(f"{bad} is still selectable")


def test_both_shipped_layouts_still_build_a_filter_graph():
    for layout in c.LAYOUTS:
        vf = c.build_vf(layout, (1920, 1080), 77, None, None, 66, 2, 100)
        assert vf and "scale=" in vf, layout


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"PASS {name}")
    print("description tests passed")
