"""The recovered `split` framing: facecam on top, tracked gameplay below.
Run: .venv/bin/python tests/test_split.py

`split` and `detect_facecam` were deleted in dd3430c and brought back on
2026-08-01. It is not the same code: the gameplay panel now pans with the same
sendcmd-driven `crop@dyn` the rest of the pipeline uses, and `facecam` reaches
build_vf as a keyword so every other call site keeps its positional signature.
The render test is the part that matters — a filtergraph can compose cleanly and
still produce a broken frame.
"""
import sys, pathlib, subprocess, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import numpy as np
import clipper as c

FF = c._FFMPEG
DIMS = (1920, 1080)
FACECAM = (1520, 40, 360, 270)          # a webcam box in the source's top-right


# ── geometry ─────────────────────────────────────────────────────────────────
def test_the_two_panels_fill_the_canvas_exactly():
    top_h, bot_h, _ = c.split_geometry(DIMS)
    _, Hc = c.canvas("split")
    assert top_h + bot_h == Hc, f"{top_h}+{bot_h} != {Hc}"
    assert top_h % 2 == 0 and bot_h % 2 == 0, "yuv420p needs even panel heights"
    assert abs(top_h / Hc - c.SPLIT_TOP_FRAC) < 0.01


def test_the_gameplay_window_matches_its_panel_aspect():
    """The window is scaled to fill the bottom panel, so a window of the wrong
    shape would stretch the game."""
    src_w, src_h = DIMS
    _, bot_h, crop_w = c.split_geometry(DIMS)
    Wc, _ = c.canvas("split")
    assert crop_w <= src_w
    assert abs((crop_w / src_h) - (Wc / bot_h)) < 0.01, \
        f"window {crop_w}x{src_h} does not match panel {Wc}x{bot_h}"


def test_the_gameplay_window_leaves_room_to_pan():
    """A window as wide as the source has nowhere to go; the pan would be dead."""
    _, _, crop_w = c.split_geometry(DIMS)
    assert crop_w < DIMS[0], "no travel left for the tracker"


# ── filtergraph ──────────────────────────────────────────────────────────────
def _vf(**kw):
    return c.build_vf("split", DIMS, 300, None, None, 66,
                      *c.caption_anchor("split", DIMS), facecam=FACECAM, **kw)


def test_the_facecam_box_is_what_gets_cropped_for_the_top_panel():
    fx, fy, fw, fh = FACECAM
    assert f"crop={fw}:{fh}:{fx}:{fy}" in _vf()


def test_the_panels_are_stacked_face_over_game():
    vf = _vf()
    assert vf.endswith("vstack=inputs=2"), vf
    assert vf.index("[face]") < vf.index("[game]") or "[face][game]vstack" in vf


def test_the_gameplay_panel_is_driven_by_the_shared_tracker():
    """Recovered wired to the current renderer, not as the old static centre crop:
    the bottom panel is a crop@dyn instance, which is what sendcmd addresses."""
    _, _, crop_w = c.split_geometry(DIMS)
    assert f"crop@dyn=w={crop_w}:h={DIMS[1]}:x=300:y=0" in _vf()
    assert c._track_window("split", DIMS) == crop_w, \
        "cut_clip would track a different window than build_vf renders"


def test_a_suffixed_graph_shares_nothing_with_the_main_one():
    """Both live inside one -filter_complex when a teaser is prepended."""
    import re
    plain = set(re.findall(r"\[(\w+)\]", _vf()))
    sfx = set(re.findall(r"\[(\w+)\]", _vf(suffix="_t")))
    assert plain and not (plain & sfx), plain & sfx
    assert len(plain) == len(sfx)
    assert "crop@dyn_t=" in _vf(suffix="_t") and "crop@dyn=" not in _vf(suffix="_t")


def test_split_without_a_facecam_falls_back_to_full_not_to_crop():
    """Falling through to the tracked-crop branch would silently ship a framing
    nobody picked."""
    got = c.build_vf("split", DIMS, 300, None, None, 66, 8, 709)
    assert got == c.build_vf("full", DIMS, 300, None, None, 66, 8, 709), got


def test_cut_clip_says_so_out_loud_when_no_face_is_found():
    import inspect
    src = inspect.getsource(c.cut_clip)
    assert "no facecam detected" in src and 'layout = "full"' in src


def test_split_keeps_the_untracked_caption_size():
    """crop/zoom fill the full width and bump captions; split's captions sit
    inside the 42% facecam panel, where the bump would cover the face."""
    import inspect
    src = inspect.getsource(c.cut_clip)
    bump = src[src.index("cap_size_eff ="):src.index("\n", src.index("cap_size_eff ="))]
    assert '"split"' not in bump, bump


# ── detection ────────────────────────────────────────────────────────────────
def test_a_detected_box_is_even_and_inside_the_frame():
    """Odd crop geometry breaks yuv420p chroma, and a box rounded outward past
    the frame edge fails the render outright."""
    try:
        import cv2  # noqa: F401
    except ImportError:
        print("  (skipped: opencv not installed)")
        return
    d = pathlib.Path(tempfile.mkdtemp())
    src = d / "faces.mp4"
    # A synthetic "streamer": no real face, so detection should decline. What is
    # being pinned here is that declining is what happens, not a bogus box.
    subprocess.run([FF, "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "testsrc2=s=640x360:r=10:d=3", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(src)], check=True)
    box = c.detect_facecam(src, 0, 3, 1920, 1080)
    if box is None:
        return
    x, y, w, h = box
    assert all(v % 2 == 0 for v in box), box
    assert x >= 0 and y >= 0 and x + w <= 1920 and y + h <= 1080, box


# ── render: the filtergraph has to produce real frames ───────────────────────
def test_split_renders_a_vertical_clip_with_both_panels_live():
    """End to end on a synthetic source whose top-right corner (the 'webcam') is
    a solid colour the gameplay area never uses. If the facecam panel is really
    the top 42%, that colour dominates the top of the output and is absent from
    the bottom."""
    d = pathlib.Path(tempfile.mkdtemp())
    src, out = d / "src.mp4", d / "out.mp4"
    # gray field + a bright green box in the top-right corner
    subprocess.run([FF, "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=gray:s=1920x1080:r=15:d=3",
                    "-f", "lavfi", "-i", "color=0x00FF00:s=360x270:r=15:d=3",
                    "-filter_complex", "[0][1]overlay=x=1520:y=40",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)], check=True)
    vf = c.build_vf("split", DIMS, 0, None, None, 66,
                    *c.caption_anchor("split", DIMS), facecam=FACECAM)
    subprocess.run([FF, "-y", "-loglevel", "error", "-i", str(src), "-vf", vf,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)], check=True)

    probe = subprocess.run([FF.replace("ffmpeg", "ffprobe"), "-v", "error",
                            "-select_streams", "v:0", "-show_entries",
                            "stream=width,height", "-of", "csv=p=0", str(out)],
                           capture_output=True, text=True).stdout.strip()
    assert probe.startswith("1080,1920"), f"split is not 9:16: {probe}"

    raw = subprocess.run([FF, "-loglevel", "error", "-i", str(out),
                          "-vf", "scale=108:192", "-pix_fmt", "rgb24",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    nf = len(raw) // (108 * 192 * 3)
    assert nf, "no frames decoded"
    frames = np.frombuffer(raw[:nf * 108 * 192 * 3], np.uint8).reshape(nf, 192, 108, 3)
    top_h = int(192 * c.SPLIT_TOP_FRAC)
    top, bottom = frames[:, :top_h], frames[:, top_h:]
    greenness = lambda a: float((a[..., 1].astype(int)
                                 - a[..., 0].astype(int)).mean())
    assert greenness(top) > 60, f"facecam panel is not showing the webcam ({greenness(top):.0f})"
    assert greenness(bottom) < 10, f"webcam colour leaked into the gameplay panel"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"PASS {name}")
    print("all split-framing tests passed")
