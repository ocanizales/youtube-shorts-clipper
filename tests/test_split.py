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
    """Named `crop@face` since 2026-08-06 so sendcmd can move it — the webcam is
    not in one place for the whole clip when the stream switches scenes."""
    fx, fy, fw, fh = FACECAM
    assert f"crop@face=w={fw}:h={fh}:x={fx}:y={fy}" in _vf()


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


# ── following the webcam across a scene change ───────────────────────────────
# One median box for the whole clip is what shipped, and it framed the game
# client for half of short_01_94s.mp4 because the stream switched scenes. The
# box moves now; what follows pins the parts that make that safe.
def _fake_samples(monkey_boxes):
    """Install a canned detection result so the tracking maths can be tested
    without a real face — Haar on synthetic video finds nothing by design."""
    times = np.arange(len(monkey_boxes), dtype=float) / c.FACECAM_FPS
    return lambda *a, **k: (times, list(monkey_boxes))


def _track_with(boxes, monkeypatch, src=(1920, 1080)):
    monkeypatch.setattr(c, "_facecam_samples", _fake_samples(boxes))
    return c.facecam_track(pathlib.Path("unused.mp4"), 0, len(boxes), *src)


# A face parked in the detection frame's top-right, then top-left: the scene cut.
RIGHT = (520, 30, 60, 60)
LEFT = (60, 30, 60, 60)


def test_facecam_track_follows_a_scene_change(monkeypatch):
    box, script = _track_with([RIGHT] * 8 + [LEFT] * 8, monkeypatch)
    assert box is not None and script is not None, "a moving webcam produced no path"
    xs = [int(l.split("crop@face x ")[1].split(",")[0])
          for l in script.read_text().splitlines() if l.strip()]
    assert max(xs) - min(xs) > 500, f"the box barely moved: {min(xs)}-{max(xs)}"
    script.unlink(missing_ok=True)


def test_facecam_track_gives_no_script_for_a_static_scene(monkeypatch):
    """Most streams never move the webcam. Those must render as the plain static
    crop they always did — no script, no per-frame commands."""
    box, script = _track_with([RIGHT] * 12, monkeypatch)
    assert box is not None and script is None, script


def test_facecam_track_holds_through_a_dropped_detection(monkeypatch):
    """A blink or a head turn drops the detection for a frame. Holding is right
    for both; snapping to origin would be a visible twitch."""
    box, script = _track_with([RIGHT, None, None, RIGHT] * 3, monkeypatch)
    assert script is None, "a dropped detection moved the box"


def test_facecam_track_declines_when_faces_are_too_sparse(monkeypatch):
    """The original conservatism must survive: a wrong facecam is worse than
    none, and None is what makes the caller fall back to `full`."""
    box, script = _track_with([RIGHT] + [None] * 15, monkeypatch)
    assert box is None and script is None


def test_the_facecam_script_never_commands_width_or_height(monkeypatch):
    """w/h set the frame size the vstack is built around. Commanding them
    mid-stream does not just look wrong, it fails the render."""
    _, script = _track_with([RIGHT] * 6 + [LEFT] * 6, monkeypatch)
    body = script.read_text()
    assert "crop@face w" not in body and "crop@face h" not in body, body[:200]
    script.unlink(missing_ok=True)


def test_the_facecam_script_moves_both_axes_in_one_interval(monkeypatch):
    """Two intervals at the same timestamp would render a diagonal as two
    axis-aligned jumps."""
    _, script = _track_with([RIGHT] * 6 + [(60, 300, 60, 60)] * 6, monkeypatch)
    line = next(l for l in script.read_text().splitlines() if l.strip())
    assert "crop@face x " in line and "crop@face y " in line, line
    script.unlink(missing_ok=True)


def test_the_facecam_script_only_emits_even_coordinates(monkeypatch):
    """Odd crop offsets break yuv420p chroma siting."""
    _, script = _track_with([RIGHT] * 6 + [LEFT] * 6, monkeypatch)
    for line in script.read_text().splitlines():
        if not line.strip():
            continue
        x = int(line.split("crop@face x ")[1].split(",")[0])
        y = int(line.split("crop@face y ")[1].rstrip(";"))
        assert x % 2 == 0 and y % 2 == 0, line
    script.unlink(missing_ok=True)


def test_a_facecam_script_reaches_the_filtergraph():
    vf = _vf(facecam_cmd=pathlib.Path("/tmp/face.cmd"))
    assert vf.startswith("sendcmd=f="), vf[:60]
    assert "crop@face=" in vf


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


def _split_render(d, vf, seconds=3):
    """Render a source whose 'webcam' (a green box) JUMPS from the top-right to
    the top-left halfway through, and return per-frame greenness of the facecam
    panel. This is the scene change that broke the static box."""
    src, out = d / "moving.mp4", d / "moving_out.mp4"
    subprocess.run([FF, "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"color=gray:s=1920x1080:r=15:d={seconds}",
                    "-f", "lavfi", "-i", f"color=0x00FF00:s=360x270:r=15:d={seconds}",
                    "-filter_complex",
                    f"[0][1]overlay=x='if(lt(t,{seconds/2}),1520,40)':y=40",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)], check=True)
    subprocess.run([FF, "-y", "-loglevel", "error", "-i", str(src), "-vf", vf,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)], check=True)
    raw = subprocess.run([FF, "-loglevel", "error", "-i", str(out),
                          "-vf", "scale=108:192", "-pix_fmt", "rgb24",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    nf = len(raw) // (108 * 192 * 3)
    assert nf, "no frames decoded"
    frames = np.frombuffer(raw[:nf * 108 * 192 * 3], np.uint8).reshape(nf, 192, 108, 3)
    top = frames[:, :int(192 * c.SPLIT_TOP_FRAC)]
    return [float((f[..., 1].astype(int) - f[..., 0].astype(int)).mean())
            for f in top]


def test_a_static_box_loses_the_webcam_when_the_scene_changes():
    """The bug, reproduced as a test so the fix below means something."""
    d = pathlib.Path(tempfile.mkdtemp())
    vf = c.build_vf("split", DIMS, 0, None, None, 66,
                    *c.caption_anchor("split", DIMS), facecam=FACECAM)
    green = _split_render(d, vf)
    assert green[0] > 60, f"never found the webcam to begin with ({green[0]:.0f})"
    assert green[-1] < 10, \
        f"static box unexpectedly kept the webcam ({green[-1]:.0f})"


def test_a_commanded_facecam_panel_follows_the_webcam_across_the_cut():
    """The fix, end to end: ffmpeg really does honour x/y commands on a named
    crop nested inside split/vstack. A well-formed script that the filtergraph
    ignores would pass every unit test above and change nothing on screen."""
    d = pathlib.Path(tempfile.mkdtemp())
    script = d / "face.cmd"
    script.write_text("0.000 crop@face x 1520, crop@face y 40;\n"
                      "1.500 crop@face x 40, crop@face y 40;\n")
    vf = c.build_vf("split", DIMS, 0, None, None, 66,
                    *c.caption_anchor("split", DIMS), facecam=FACECAM,
                    facecam_cmd=script)
    green = _split_render(d, vf)
    assert green[0] > 60, f"facecam panel missed the webcam at the start ({green[0]:.0f})"
    assert green[-1] > 60, \
        f"facecam panel did not follow the scene change ({green[-1]:.0f})"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"PASS {name}")
    print("all split-framing tests passed")
