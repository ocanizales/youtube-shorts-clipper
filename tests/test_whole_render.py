"""End-to-end: whole-video mode really cuts the whole video into 16:9 parts.
Run: .venv/bin/python tests/test_whole_render.py

A filtergraph that composes cleanly can still produce a broken frame, and
segmentation arithmetic that passes unit tests can still be wired to the wrong
seek. So this renders an actual source through `make_clips(layout="whole")` and
reads the pixels back.

The source encodes its own timestamp: every frame is a flat grey whose luma IS
the elapsed second (geq lum='T'). That makes part boundaries measurable — the
first frame of Part 2 must be the second that follows the last frame of Part 1,
with no gap and no repeat — which is the one property the mode has to guarantee.

`WHOLE_PART_LEN` is shrunk for the duration of the test so the real segmentation
path runs against a source short enough to encode in seconds.
"""
import sys, pathlib, subprocess, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import numpy as np
import clipper as c

FF = c._FFMPEG
SRC_DUR = 10          # seconds
PART_LEN = 4          # -> 4 + 4 + 2, and 2 < TAIL_MIN so the tail merges
TAIL_MIN = 3


def _render_source(path):
    """Flat grey frames whose luma equals the elapsed second, plus a tone."""
    subprocess.run([FF, "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=black:s=320x180:r=10:d={SRC_DUR}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={SRC_DUR}",
        "-vf", "geq=lum='T*20':cb=128:cr=128",
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)


def _luma_at(clip: pathlib.Path, when: str) -> float:
    """Mean luma of one frame — `when` is an ffmpeg seek fragment."""
    args = ["-sseof", "-0.15"] if when == "last" else ["-ss", "0"]
    raw = subprocess.run([FF, "-loglevel", "error", *args, "-i", str(clip),
                          "-frames:v", "1", "-vf", "scale=32:18,format=gray",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    return float(np.frombuffer(raw[:32 * 18], np.uint8).mean())


def test_whole_mode_renders_every_second_of_the_source_as_169_parts():
    d = pathlib.Path(tempfile.mkdtemp())
    src = d / "vod.mp4"
    _render_source(src)
    assert abs(c._duration(src) - SRC_DUR) < 0.3, c._duration(src)

    saved = (c.CLIPS_DIR, c.WHOLE_PART_LEN, c.WHOLE_TAIL_MIN)
    c.CLIPS_DIR, c.WHOLE_PART_LEN, c.WHOLE_TAIL_MIN = d, PART_LEN, TAIL_MIN
    try:
        parts, hero = c.make_clips(src, layout="whole", max_clips=99, clip_len=30,
                                   subs=False, ai_meta=False, thumbs=True,
                                   endcard="Link in the pinned comment")
    finally:
        c.CLIPS_DIR, c.WHOLE_PART_LEN, c.WHOLE_TAIL_MIN = saved

    # -- count and coverage ---------------------------------------------------
    assert hero is None, "a 9:16 hero cover has no business on a landscape series"
    assert len(parts) == 2, [p.name for p in parts]      # 4 + (4+2 merged)
    durs = [c._duration(p) for p in parts]
    assert abs(sum(durs) - SRC_DUR) < 0.4, f"{sum(durs)}s rendered from {SRC_DUR}s"
    assert abs(durs[0] - PART_LEN) < 0.25, durs
    assert abs(durs[1] - (SRC_DUR - PART_LEN)) < 0.25, durs

    # -- aspect ---------------------------------------------------------------
    for p in parts:
        assert c._dims(p) == (1920, 1080), f"{p.name} is {c._dims(p)}, not 16:9"

    # -- the parts are consecutive, not overlapping or skipping ---------------
    # luma == 20 * source-seconds, so Part 2 must open where Part 1 closed.
    p1_end, p2_start = _luma_at(parts[0], "last"), _luma_at(parts[1], "first")
    assert p2_start > p1_end, "Part 2 starts before Part 1 ends"
    assert p2_start - p1_end < 20 * 1.0, \
        f"a gap of ~{(p2_start - p1_end) / 20:.1f}s of footage between the parts"
    assert _luma_at(parts[0], "first") < 20, "Part 1 does not start at 0:00"
    assert _luma_at(parts[1], "last") > 20 * (SRC_DUR - 1.5), \
        "the last part stops short of the end of the video"

    # -- titles ---------------------------------------------------------------
    for i, p in enumerate(parts, 1):
        assert c._read_sidecar(p).get("TITLE") == f"Part {i}", p.name


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"PASS {name}")
    print("whole-video render tests passed")
