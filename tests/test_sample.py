"""Sample harness renders a labeled comparison set.
Run: .venv/bin/python tests/test_sample.py"""
import sys, pathlib, subprocess, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import clipper as c

FF = c._FFMPEG


def test_make_sample_writes_comparison_set():
    d = pathlib.Path(tempfile.mkdtemp())
    src = d / "vod.mp4"
    subprocess.run([FF, "-y", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=s=640x360:r=30:d=6", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", str(src)], check=True)
    orig = c.CLIPS_DIR
    c.CLIPS_DIR = d
    try:
        outs = c.make_sample(src, at=1.0)
        assert len(outs) >= 6, "expect static+eased across >=3 zoom levels"
        assert all(p.exists() and p.stat().st_size > 0 for p in outs), "all samples render"
        assert any("eased" in p.name for p in outs) and any("static" in p.name for p in outs)
    finally:
        c.CLIPS_DIR = orig


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"PASS {name}")
    print("sample harness test passed")
