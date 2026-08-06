"""A source shorter than the requested clip length must still produce a clip.
Run: .venv/bin/python tests/test_short_source.py

This is the Twitch-clip case. `find_hype_moments` keeps a window only when
`start + clip_len <= n`, so when `clip_len` is larger than the source there is
no such window and it returns an empty list — the render then "succeeds" with
zero clips, exit 0, nothing printed that looks like an error. A filter that can
reject *everything* needs a floor, or it reports success for having done nothing.

The floor lives inside `find_hype_moments` for a reason worth protecting. It was
first written in `make_clips` against `_duration()`, i.e. the container's
duration, and that is a DIFFERENT NUMBER than the one the windowing tests
against: a 30.000s container here decodes to 29.967s of audio, which floors to
n=29. Clamping to 30 left every window rejected exactly as before. So the case
that matters is not "source shorter than clip_len" — it is "source whose audio
floors below its container duration", which is the ordinary case for a real
download, and it is why this test asserts against a fractional-length source.
"""
import sys, pathlib, subprocess, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import clipper as c

FF = c._FFMPEG
# Deliberately fractional: floor(audio) must land BELOW the container duration,
# which is what defeated the first version of the fix.
SRC_DUR = 6.4


def _render_source(path):
    subprocess.run([FF, "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=black:s=320x180:r=10:d={SRC_DUR}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={SRC_DUR}",
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(path)], check=True)


def test_short_source_still_yields_a_moment():
    with tempfile.TemporaryDirectory() as td:
        src = pathlib.Path(td) / "short.mp4"
        _render_source(src)

        # The premise: the container reports more than the audio floors to.
        assert int(c._duration(src)) >= 6, "fixture is not the shape this guards"

        # Ask for far more than exists — the Twitch default (45s on a 30s clip).
        moments, used = c.find_hype_moments(src, clip_len=45, top_n=5, peak_pos=0.72)

        assert moments, ("a source shorter than clip_len produced no moments — "
                         "the render would report success with zero clips")
        assert used < 45, "clip_len came back unclamped"
        # Every returned window must fit inside what was actually analysed, or
        # the cut runs past the end of the source it was chosen from.
        assert all(m + used <= c._duration(src) + 1 for m in moments), \
            f"a window runs past the source: {moments} at {used}s"


def test_long_enough_source_is_left_alone():
    """The clamp must not fire when it isn't needed — a normal VOD keeps the
    length the caller asked for, or every render silently changes shape."""
    with tempfile.TemporaryDirectory() as td:
        src = pathlib.Path(td) / "long.mp4"
        _render_source(src)
        _, used = c.find_hype_moments(src, clip_len=3, top_n=1, peak_pos=0.72)
        assert used == 3, f"clip_len was altered on a long-enough source: {used}"


if __name__ == "__main__":
    test_short_source_still_yields_a_moment()
    test_long_enough_source_is_left_alone()
    print("ok")
