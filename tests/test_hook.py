"""HPC hook-overhaul unit tests. Run: .venv/bin/python tests/test_hook.py

Covers the three pure pieces of docs/superpowers/plans/2026-07-28-hpc-hook.md:
build_vf's `suffix` (so a teaser can share one filter graph with the main
segment), `_refine_start` (the scored opening second), and `_teaser_window`.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import numpy as np
import clipper as c

DIMS = (1920, 1080)
FACECAM = (100, 50, 300, 300)

# Captured from build_vf BEFORE the suffix parameter existed. This is the
# regression guard for the framing/tracking work on `framing-overhaul`: the
# production path renders with suffix="" and must be byte-identical forever.
GOLDEN = {
    "split": "split=2[a][b];[a]crop=300:300:100:50,scale=1080:806:"
             "force_original_aspect_ratio=increase,crop=1080:806[cam];"
             "[b]scale=1080:1114:force_original_aspect_ratio=increase,"
             "crop=1080:1114[game];[cam][game]vstack=inputs=2",
    "full": "split=2[bg][fg];[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=22:4[b];[fg]scale=1080:-2[v];"
            "[b][v]overlay=(W-w)/2:(H-h)/2",
    "fit": "split[bg][fg];[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
           "crop=1080:1920,boxblur=22:4[b];[fg]scale=1080:1920:"
           "force_original_aspect_ratio=decrease[f];[b][f]overlay=(W-w)/2:(H-h)/2",
    "zoom": "split=2[pf][hs];[pf]crop@dyn=w=590:h=874:x=77:y=0,scale=1080:1602[game];"
            "[hs]crop=1920:206:0:874,scale=1080:116[hud];"
            "[game][hud]vstack=inputs=2,pad=1080:1920:0:202:black",
    "crop": "crop@dyn=w=607:h=1080:x=77:y=0,scale=1080:1920",
}


def test_build_vf_empty_suffix_is_byte_identical():
    for layout, want in GOLDEN.items():
        got = c.build_vf(layout, DIMS, 77, FACECAM, None, None, 66, 2, 100)
        assert got == want, f"{layout} drifted:\n want {want}\n got  {got}"


def test_build_vf_suffix_renames_every_internal_label():
    """A suffixed graph must share NO link label with the unsuffixed one —
    otherwise the two segments collide inside a single -filter_complex."""
    import re
    for layout in GOLDEN:
        plain = set(re.findall(r"\[(\w+)\]", c.build_vf(
            layout, DIMS, 77, FACECAM, None, None, 66, 2, 100)))
        sfx = set(re.findall(r"\[(\w+)\]", c.build_vf(
            layout, DIMS, 77, FACECAM, None, None, 66, 2, 100, suffix="_t")))
        assert not (plain & sfx), f"{layout} labels collide: {plain & sfx}"
        assert len(plain) == len(sfx), f"{layout} lost a label under suffixing"


def test_build_vf_suffix_renames_the_crop_instance():
    """sendcmd addresses `crop@dyn` BY NAME, so the teaser's crop must not
    answer to it — else the flash would pan on the main segment's timeline."""
    for layout in ("crop", "zoom"):
        sfx = c.build_vf(layout, DIMS, 77, None, None, None, 66, 2, 100, suffix="_t")
        assert "crop@dyn_t=" in sfx
        assert "crop@dyn=" not in sfx


def test_refine_start_moves_to_the_liveliest_second():
    n, clip_len, peak = 200, 30, 100
    energy = np.zeros(n)
    energy[peak] = 10.0
    nominal = peak - int(clip_len * 0.72)        # 78
    energy[nominal + 2] = 5.0                    # a shout just after the nominal open
    got = c._refine_start(energy, nominal, peak, clip_len, n)
    assert got == nominal + 2, f"expected the local energy max, got {got}"


def test_refine_start_is_a_noop_on_flat_audio():
    n, clip_len, peak = 200, 30, 100
    energy = np.full(n, 3.0)                     # nothing to prefer anywhere
    nominal = peak - int(clip_len * 0.72)
    assert c._refine_start(energy, nominal, peak, clip_len, n) == nominal, \
        "ties must break toward nominal — no movement without a reason"


def test_refine_start_keeps_the_spike_inside_its_band():
    """The loud second before the fight must not be allowed to drag the start so
    late that the clip opens on (or near) the payoff itself."""
    n, clip_len, peak = 200, 30, 100
    energy = np.zeros(n)
    energy[peak - 2] = 99.0                      # would put the spike at 2/30 = 0.07
    nominal = peak - int(clip_len * 0.72)
    got = c._refine_start(energy, nominal, peak, clip_len, n)
    frac = (peak - got) / clip_len
    assert c.HOOK_PEAK_MIN <= frac <= c.HOOK_PEAK_MAX, f"spike landed at {frac:.2f}"


def test_refine_start_never_runs_past_the_video():
    n, clip_len, peak = 40, 30, 25
    energy = np.zeros(n)
    energy[:] = np.arange(n)                     # energy rises toward the end
    nominal = max(0, peak - int(clip_len * 0.72))
    got = c._refine_start(energy, nominal, peak, clip_len, n)
    assert 0 <= got <= n - clip_len, f"{got} would read past the end of the audio"


def test_teaser_window_ends_just_after_the_spike():
    start, dur, peak_pos = 100.0, 30, 0.72
    t0, t_len = c._teaser_window(start, dur, peak_pos)
    peak = start + peak_pos * dur
    assert abs(t0 - (peak - c.TEASER_LEAD)) < 1e-6
    assert abs((t0 + t_len) - (peak + (c.TEASER_DUR - c.TEASER_LEAD))) < 1e-6
    assert t_len <= c.TEASER_DUR


def test_teaser_window_clamps_at_the_video_start():
    t0, t_len = c._teaser_window(0.0, 30, 0.02)   # spike 0.6s in, before TEASER_LEAD
    assert t0 == 0.0, "cannot seek before the start of the video"
    assert t_len > 0


def test_teaser_window_never_overruns_the_clip():
    # peak_pos ~1.0 puts the spike at the clip's own end; the flash must stop there.
    t0, t_len = c._teaser_window(10.0, 30, 0.99)
    assert t0 + t_len <= 40.0 + 1e-6


def test_teaser_is_short_enough_not_to_resolve():
    """A guard on the tuning itself, not the code: a cold open that runs long
    enough to show the outcome is the exact failure HPC warns about."""
    assert c.TEASER_DUR <= 2.5, "a teaser this long pays the clip off up front"
    assert c.TEASER_LEAD < c.TEASER_DUR, "the flash must reach past the spike onset"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"PASS {name}")
    print("all hook-unit tests passed")
