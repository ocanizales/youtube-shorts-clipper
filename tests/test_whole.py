"""Whole-video framing: segmentation arithmetic + the 16:9 canvas.
Run: .venv/bin/python tests/test_whole.py

`whole` is the one framing that is not a 9:16 Short and not a highlight picker.
Both of those are pure logic and both are load-bearing: a segmentation that drops
a second of footage breaks the only promise this mode makes ("all of the video"),
and a canvas that stays vertical silently ships the wrong aspect.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import clipper as c

DIMS = (1920, 1080)


# ── segmentation: every frame lands in exactly one part ──────────────────────
def test_segments_cover_the_whole_video_with_no_gap_or_overlap():
    """The invariant the mode exists for. Checked across durations that hit every
    branch: exact multiples, runt tails, keepable tails, sub-part videos."""
    for dur in (3, 60.5, 61, 122, 125, 130, 183, 200, 1000.25, 3600, 7200.4):
        segs = c.whole_segments(dur)
        assert segs, f"{dur}s produced no parts"
        assert segs[0][0] == 0.0, f"{dur}s does not start at 0:00"
        total = sum(length for _, length in segs)
        assert abs(total - dur) < 1e-6, f"{dur}s -> {total}s of parts (footage lost)"
        for (s0, l0), (s1, _) in zip(segs, segs[1:]):
            assert abs(s0 + l0 - s1) < 1e-6, f"{dur}s: gap/overlap at {s1}"


def test_parts_are_chronological_61_second_slices():
    """Clip 1 is 0:00-1:01, clip 2 is 1:01-2:02, and so on."""
    segs = c.whole_segments(400)
    assert c.WHOLE_PART_LEN == 61
    assert segs[0] == (0.0, 61.0)
    assert segs[1] == (61.0, 61.0)
    assert segs[2] == (122.0, 61.0)
    assert [s for s, _ in segs] == sorted(s for s, _ in segs), "parts must be in order"


def test_a_runt_tail_is_merged_rather_than_shipped_or_dropped():
    """3 seconds is not a video. It is also not something we are allowed to throw
    away, so it extends the last part instead of becoming Part N+1."""
    segs = c.whole_segments(125)                    # 61 + 61 + 3
    assert len(segs) == 2, segs
    assert segs[-1] == (61.0, 64.0), segs
    assert sum(l for _, l in segs) == 125


def test_a_tail_worth_watching_becomes_its_own_part():
    segs = c.whole_segments(200)                    # 61*3 + 17
    assert len(segs) == 4, segs
    assert segs[-1][1] == 200 - 3 * 61
    assert segs[-1][1] >= c.WHOLE_TAIL_MIN


def test_the_merge_threshold_is_the_only_thing_that_moves_the_boundary():
    """Right at the threshold the tail stands alone; one second under it merges."""
    base = 61 * 2
    keep = c.whole_segments(base + c.WHOLE_TAIL_MIN)
    merge = c.whole_segments(base + c.WHOLE_TAIL_MIN - 1)
    assert len(keep) == 3 and keep[-1][1] == c.WHOLE_TAIL_MIN
    assert len(merge) == 2 and merge[-1][1] == 61 + c.WHOLE_TAIL_MIN - 1


def test_a_merged_part_still_fits_a_short():
    """Merging is only defensible while the fattest possible part is still
    postable. YouTube's Shorts ceiling is 3 minutes; ours is well under it."""
    worst = 61 + c.WHOLE_TAIL_MIN - 1
    assert worst <= 180, f"a merged part can reach {worst}s"


def test_a_video_shorter_than_one_part_is_still_one_part():
    """There is nothing to merge into, and dropping it would drop the video."""
    assert c.whole_segments(30) == [(0.0, 30.0)]
    assert c.whole_segments(2) == [(0.0, 2.0)]


def test_an_exact_multiple_gets_no_phantom_tail():
    assert c.whole_segments(122) == [(0.0, 61.0), (61.0, 61.0)]


def test_an_unreadable_duration_yields_no_parts():
    """`_duration` returns 0.0 when ffprobe can't answer; that must not become a
    zero-length render."""
    assert c.whole_segments(0) == []
    assert c.whole_segments(-5) == []


def test_part_count_matches_the_duration():
    for dur, want in ((61, 1), (122, 2), (125, 2), (200, 4), (3600, 59)):
        assert len(c.whole_segments(dur)) == want, dur


# ── canvas: this framing, and only this framing, is landscape ────────────────
def test_whole_is_the_only_landscape_framing():
    assert c.canvas("whole") == (1920, 1080)
    for layout in ("full", "split", "zoom", "crop", "fit"):
        assert c.canvas(layout) == (1080, 1920), layout


def test_the_filtergraph_targets_the_landscape_canvas():
    vf = c.build_vf("whole", DIMS, 0, None, None, 47, *c.caption_anchor("whole", DIMS))
    assert "scale=1920:1080" in vf, vf
    assert "1080:1920" not in vf, "whole is still being scaled to a vertical canvas"
    assert f"flags={c.SCALE_FLAGS}" in vf, "whole lost its explicit scaler"


def test_a_non_169_source_is_letterboxed_not_stretched():
    """4:3 footage keeps its shape on the 16:9 canvas."""
    vf = c.build_vf("whole", (1440, 1080), 0, None, None, 47, 2, 100)
    assert "force_original_aspect_ratio=decrease" in vf
    assert "pad=1920:1080" in vf
    assert ":black" not in vf, "standing rule: the pad is the house grey, not black"


def test_whole_does_not_crop_or_track():
    """The point of this framing is that the frame is untouched — no punch-in, no
    pan, nothing that could cut the action out of a part."""
    vf = c.build_vf("whole", DIMS, 500, None, None, 47, 2, 100, sendcmd=None)
    assert "crop@dyn" not in vf and "boxblur" not in vf, vf


# ── captions and the end card have to move with the canvas ───────────────────
def test_captions_sit_in_the_landscape_lower_third():
    an, margin = c.caption_anchor("whole", DIMS)
    _, Hc = c.canvas("whole")
    assert an == 2, "landscape captions are bottom-anchored"
    assert 0.05 * Hc < margin < 0.25 * Hc, f"{margin}px is not a lower third of {Hc}px"


def test_captions_clear_the_end_card_band():
    """A margin tuned against a 1920px-tall Short would put the caption inside the
    end-card band on a 1080px-tall canvas, which is a fifth of this frame."""
    _, margin = c.caption_anchor("whole", DIMS)
    _, Hc = c.canvas("whole")
    assert margin >= int(Hc * c.ENDCARD_BAND), \
        f"caption margin {margin} sits inside the {int(Hc * c.ENDCARD_BAND)}px CTA band"


def test_the_end_card_band_scales_with_the_canvas():
    kw = dict(endcard="Link in the pinned comment", endcard_from=10.0)
    tall = c.build_vf("full", DIMS, 0, None, None, 66, 8, 100, **kw)
    wide = c.build_vf("whole", DIMS, 0, None, None, 47, 2, 151, **kw)
    assert f"h={int(1920 * c.ENDCARD_BAND)}" in tall
    assert f"h={int(1080 * c.ENDCARD_BAND)}" in wide, wide
    assert f"fontsize={c.ENDCARD_SIZE}:" in tall
    assert f"fontsize={c.ENDCARD_SIZE}:" not in wide, \
        "the CTA is still typeset for a 1920px-tall canvas"


def test_the_ass_script_declares_the_canvas_it_was_written_for():
    """libass stretches the whole script to the frame if PlayRes disagrees with
    it, so a 9:16 PlayRes on a 16:9 render distorts every caption."""
    import tempfile
    for layout in ("whole", "full"):
        Wc, Hc = c.canvas(layout)
        with tempfile.TemporaryDirectory() as d:
            clip = pathlib.Path(d) / "clip.mp4"
            clip.write_bytes(b"")
            orig, c._WHISPER = c._WHISPER, _StubWhisper()
            try:
                ass, _, _ = c.make_dynamic_captions(clip, 2, 100, 47,
                                                    play_res=c.canvas(layout))
                head = ass.read_text(encoding="utf-8")
            finally:
                c._WHISPER = orig
        assert f"PlayResX: {Wc}" in head and f"PlayResY: {Hc}" in head, layout


class _Word:
    def __init__(self, w, a, b):
        self.word, self.start, self.end = w, a, b


class _Segment:
    words = [_Word("BARON", 0.2, 1.0), _Word("STEAL", 1.0, 2.0)]


class _Info:
    language, language_probability = "en", 0.99


class _StubWhisper:
    """Minimum faster-whisper surface make_dynamic_captions touches."""
    def transcribe(self, path, **kw):
        return iter([_Segment()]), _Info()


# ── pipeline wiring ──────────────────────────────────────────────────────────
def test_whole_is_a_selectable_framing_and_crop_is_not():
    assert c.LAYOUTS == ("full", "whole", "split", "zoom"), c.LAYOUTS


def test_make_clips_hands_whole_off_to_the_parts_pipeline():
    import inspect
    src = inspect.getsource(c.make_clips)
    assert "make_whole_parts" in src, "whole would fall into the highlight pipeline"
    assert src.index('layout == "whole"') < src.index("find_hype_moments"), \
        "whole must divert BEFORE the audio-spike detection it has no use for"


def test_parts_are_rendered_without_a_teaser_or_a_vertical_thumbnail():
    """A cold open would replay footage the next seconds are about to show in
    order; a 1080x1920 cover on a 16:9 part would be worse than none."""
    import inspect
    src = inspect.getsource(c.make_whole_parts)
    assert "teaser=False" in src and "thumbs=False" in src, src


def test_parts_are_titled_part_one_through_part_n():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        clip = pathlib.Path(d) / "short_02_61s.mp4"
        clip.write_bytes(b"")
        c.write_metadata(clip, "LoL Highlight", 2, "youtube", "Faker steals Baron",
                         transcript="Faker steals Baron", title_override="Part 2")
        side = c._read_sidecar(clip)
    assert side["TITLE"] == "Part 2", side["TITLE"]
    assert "#Shorts" in side["CAPTION"], "a part still gets its caption + hashtags"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"PASS {name}")
    print("all whole-video tests passed")
