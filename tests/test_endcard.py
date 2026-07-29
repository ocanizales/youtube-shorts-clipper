"""End-to-end: the affiliate end card appears only over the final seconds.
Run: .venv/bin/python tests/test_endcard.py

Differential on a pure-white source, so the #252525 scrim is unmistakable and a
card that rendered for the whole clip (or not at all) cannot pass.
"""
import sys, pathlib, subprocess, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import numpy as np
import clipper as c

FF = c._FFMPEG
SRC_DUR, CLIP_DUR = 12, 8


def _white_source(path):
    subprocess.run([FF, "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=white:s=640x360:r=30:d={SRC_DUR}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={SRC_DUR}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-shortest", str(path)], check=True)


def _band_luma(p: pathlib.Path, t: float) -> float:
    """Mean brightness of the bottom ENDCARD_BAND strip of the frame at `t`."""
    band = int(c.H * c.ENDCARD_BAND)
    raw = subprocess.run([FF, "-loglevel", "error", "-ss", str(t), "-i", str(p),
        "-frames:v", "1",
        "-vf", f"crop={c.W}:{band}:0:{c.H - band},scale=32:32,format=gray",
        "-f", "rawvideo", "-"], capture_output=True).stdout
    return float(np.frombuffer(raw[:32 * 32], np.uint8).mean()) if raw else -1.0


def test_endcard_only_covers_the_final_seconds():
    d = pathlib.Path(tempfile.mkdtemp())
    src = d / "white.mp4"
    _white_source(src)
    orig = c.CLIPS_DIR
    c.CLIPS_DIR = d
    try:
        out = c.cut_clip(src, 0.0, CLIP_DUR, 1, "full", None, False, (640, 360),
                         ai_meta=False, thumbs=False, teaser=False,
                         endcard="SETTINGS -> PINNED COMMENT")
        early = _band_luma(out, 1.0)                      # long before the card
        late = _band_luma(out, CLIP_DUR - 0.5)            # inside the card window
        assert early > 200, f"source band should be white before the card, got {early:.0f}"
        assert late < 120, f"card scrim should darken the band, got {late:.0f}"

        # Control: same render without a card stays white to the very last frame.
        plain = c.cut_clip(src, 0.0, CLIP_DUR, 2, "full", None, False, (640, 360),
                           ai_meta=False, thumbs=False, teaser=False)
        assert _band_luma(plain, CLIP_DUR - 0.5) > 200, \
            "no-endcard clip must not darken — otherwise the test proves nothing"
    finally:
        c.CLIPS_DIR = orig


def test_endcard_lands_at_the_end_of_the_finished_short():
    """With a teaser prepended the card must still close the OUTPUT, not fire
    early at main-segment-minus-1.5s measured from the file start."""
    d = pathlib.Path(tempfile.mkdtemp())
    src = d / "white.mp4"
    _white_source(src)
    orig = c.CLIPS_DIR
    c.CLIPS_DIR = d
    try:
        out = c.cut_clip(src, 0.0, CLIP_DUR, 1, "full", None, False, (640, 360),
                         peak_pos=0.72, ai_meta=False, thumbs=False, teaser=True,
                         endcard="SETTINGS -> PINNED COMMENT")
        _, t_len = c._teaser_window(0.0, CLIP_DUR, 0.72)
        total = t_len + CLIP_DUR
        assert _band_luma(out, total - 0.4) < 120, "card missing at the real end"
        # Just before the card window opens, the band must still be clean.
        assert _band_luma(out, total - c.ENDCARD_DUR - 0.8) > 200, \
            "card fired early — endcard_from is being measured against the wrong clock"
    finally:
        c.CLIPS_DIR = orig


def test_endcard_is_absent_from_the_teaser_branch():
    """The flash must never carry the CTA."""
    tvf = c.build_vf("crop", (1920, 1080), 0, None, None, None, 66, 2, 100, suffix="_t")
    assert "drawbox" not in tvf and c.ENDCARD_BG not in tvf


def test_endcard_scrim_is_not_pure_black():
    assert c.ENDCARD_BG.lower() not in ("#000", "#000000", "black")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"PASS {name}")
    print("all endcard tests passed")
