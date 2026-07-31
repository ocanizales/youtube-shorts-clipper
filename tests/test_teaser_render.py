"""End-to-end: the cold-open teaser really is prepended, and really is the climax.
Run: .venv/bin/python tests/test_teaser_render.py

Differential by construction — the source is black except for a bright window
around the spike, so a clip that merely got longer (or that flashed the *wrong*
moment) cannot pass.
"""
import sys, pathlib, subprocess, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import numpy as np
import clipper as c

FF = c._FFMPEG
SRC_DUR, CLIP_DUR, PEAK_POS = 12, 10, 0.8
PEAK = CLIP_DUR * PEAK_POS                       # 8.0s in — where the "fight" is
BRIGHT = (PEAK - 1.5, PEAK + 1.0)                # the only lit window in the source


def _render_source(path):
    """Black everywhere except a bright window straddling the spike, plus audio.

    Audio matters: the teaser path concatenates [0:a][1:a] and then loudnorms
    once, so a silent source would not exercise the branch that actually ships.
    """
    subprocess.run([FF, "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=black:s=640x360:r=30:d={SRC_DUR}",
        "-f", "lavfi", "-i", f"color=white:s=640x360:r=30:d={SRC_DUR}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={SRC_DUR}",
        "-filter_complex",
        f"[0][1]overlay=enable='between(t,{BRIGHT[0]},{BRIGHT[1]})'",
        "-map", "2:a", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(path)], check=True)


def _duration(p: pathlib.Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True).stdout
    return float(out.strip())


def _luma_at(p: pathlib.Path, t: float) -> float:
    """Mean brightness of the output frame at `t` seconds."""
    raw = subprocess.run([FF, "-loglevel", "error", "-ss", str(t), "-i", str(p),
                          "-frames:v", "1", "-vf", "scale=32:32,format=gray",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    return float(np.frombuffer(raw[:32 * 32], np.uint8).mean()) if raw else 0.0


def _cut(src, out_dir, teaser):
    c.CLIPS_DIR = out_dir
    return c.cut_clip(src, 0.0, CLIP_DUR, 1 if teaser else 2, "full", None, False,
                      (640, 360), peak_pos=PEAK_POS, ai_meta=False, thumbs=False,
                      teaser=teaser)


def test_teaser_prepends_the_climax():
    d = pathlib.Path(tempfile.mkdtemp())
    src = d / "src.mp4"
    _render_source(src)
    orig_clips = c.CLIPS_DIR
    try:
        with_t = _cut(src, d, True)
        without = _cut(src, d, False)

        # 1. The clip got longer by the length of the flash — and only that.
        t0, t_len = c._teaser_window(0.0, CLIP_DUR, PEAK_POS)
        grew = _duration(with_t) - _duration(without)
        assert abs(grew - t_len) < 0.35, f"grew by {grew:.2f}s, expected ~{t_len:.2f}s"

        # 2. The clip now OPENS on the bright moment (the fight)...
        assert _luma_at(with_t, 0.4) > 150, "cold open is not showing the climax"
        # ...and immediately after the flash it is back in the dark build-up.
        assert _luma_at(with_t, t_len + 0.5) < 60, "no hard cut back to the build-up"
        # 3. ...which is exactly what the un-teasered clip opened on before.
        assert _luma_at(without, 0.4) < 60, "control clip should open dark"

        # 4. The payoff is still in there, at its original place plus the offset.
        assert _luma_at(with_t, t_len + PEAK) > 150, "the climax itself went missing"
    finally:
        c.CLIPS_DIR = orig_clips


def test_teaser_graph_is_valid_for_every_layout():
    """The teaser doubles the reframing graph inside one -filter_complex. The
    tracked layouts are the sharp edge: two `crop@dyn` instances would both
    answer the sendcmd script, and duplicated link labels are a hard ffmpeg
    error. A moving subject is used so track_path actually emits a pan script.
    """
    d = pathlib.Path(tempfile.mkdtemp())
    src = d / "moving.mp4"
    xe = f"(main_w-overlay_w)*(0.2+0.6*t/{SRC_DUR})"
    subprocess.run([FF, "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=black:s=640x360:r=30:d={SRC_DUR}",
        "-f", "lavfi", "-i", f"color=white:s=60x360:r=30:d={SRC_DUR}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={SRC_DUR}",
        "-filter_complex", f"[0][1]overlay=x='{xe}':y=0",
        "-map", "2:a", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(src)], check=True)

    orig_clips = c.CLIPS_DIR
    c.CLIPS_DIR = d
    try:
        _, t_len = c._teaser_window(0.0, CLIP_DUR, PEAK_POS)
        for i, layout in enumerate(("full", "zoom"), 1):
            out = c.cut_clip(src, 0.0, CLIP_DUR, i, layout, None, False, (640, 360),
                             peak_pos=PEAK_POS, ai_meta=False, thumbs=False,
                             teaser=True)
            assert out.exists() and out.stat().st_size > 10_000, f"{layout} render is empty"
            assert _duration(out) > CLIP_DUR + t_len * 0.5, \
                f"{layout} did not get the teaser concatenated"
    finally:
        c.CLIPS_DIR = orig_clips


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"PASS {name}")
    print("all teaser-render tests passed")
