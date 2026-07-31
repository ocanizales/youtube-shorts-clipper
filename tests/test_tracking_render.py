"""End-to-end: sendcmd-driven crop follows a moving subject.
Run: .venv/bin/python tests/test_tracking_render.py"""
import sys, pathlib, subprocess, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import numpy as np
import clipper as c

FF = c._FFMPEG


def _render_source(path, w=640, h=360, dur=4, r=30, bar=40, x0f=0.2, x1f=0.8):
    """A bright vertical bar sweeping across the frame on black.

    Built by overlaying a white strip onto a black bg with a time-varying x —
    reliable across ffmpeg builds (drawbox's expression fill rendered all-black
    on this one). The bar sweeps between `x0f`..`x1f` of the available travel;
    the default central band (0.2..0.8) models a LoL champion that roams while
    the game camera keeps it near center — the case this tracker is tuned for,
    not an adversarial screen-edge-to-edge sweep.
    """
    xe = f"(main_w-overlay_w)*({x0f}+({x1f}-{x0f})*t/{dur})"
    subprocess.run([FF, "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=black:s={w}x{h}:r={r}:d={dur}",
        "-f", "lavfi", "-i", f"color=white:s={bar}x{h}:r={r}:d={dur}",
        "-filter_complex", f"[0][1]overlay=x='{xe}':y=0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)


def _bright_frac(out: pathlib.Path) -> float:
    """Fraction of output frames that still contain the bright subject."""
    raw = subprocess.run([FF, "-loglevel", "error", "-i", str(out),
        "-vf", "scale=54:96,format=gray", "-f", "rawvideo", "-"],
        capture_output=True).stdout
    fw, fh = 54, 96
    nf = len(raw) // (fw * fh)
    frames = np.frombuffer(raw[:nf * fw * fh], np.uint8).reshape(nf, fh, fw)
    return float((frames.reshape(nf, -1).max(axis=1) > 180).mean())


def _render(src, dims, x0, script, out):
    vf = c.build_vf("crop", dims, x0, None, None, 66,
                    *c.caption_anchor("crop", dims), sendcmd=script)
    subprocess.run([FF, "-y", "-loglevel", "error", "-i", str(src),
                    "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
                   check=True)
    return vf


def test_tracked_crop_follows_moving_bar():
    d = pathlib.Path(tempfile.mkdtemp())
    src = d / "src.mp4"
    _render_source(src)
    dims = (640, 360)
    orig_clips = c.CLIPS_DIR
    c.CLIPS_DIR = d                              # keep track scripts in this temp dir
    try:
        x0, script = c.track_path(src, 0, 4, 640, 360, crop_w=200)
        assert script is not None, "a moving subject should produce a pan script"

        eased_vf = _render(src, dims, x0, script, d / "eased.mp4")
        assert "crop@dyn" in eased_vf and "sendcmd" in eased_vf, \
            "vf must use sendcmd + named crop"
        # Same source, same opening x, but frozen (no pan) — the tracking baseline.
        _render(src, dims, x0, None, d / "static.mp4")

        eased = _bright_frac(d / "eased.mp4")
        static = _bright_frac(d / "static.mp4")
        # A crop that actually follows the subject keeps it on screen most of the
        # clip, and does so markedly better than freezing at the same opening x.
        assert eased > 0.6, f"tracked crop should keep the subject visible (got {eased:.2f})"
        assert eased > static + 0.15, \
            f"the pan must beat a static crop (eased={eased:.2f} static={static:.2f})"
    finally:
        c.CLIPS_DIR = orig_clips


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"PASS {name}")
    print("render integration tests passed")
