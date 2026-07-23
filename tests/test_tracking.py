"""Standalone tracking-logic tests. Run: .venv/bin/python tests/test_tracking.py"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import numpy as np
import clipper as c


def test_column_motion_masks_minimap_corner():
    nf, SH, SW = 3, c.SH, c.SW
    frames = np.zeros((nf, SH, SW), dtype=np.int16)
    # a blinking blob in the bottom-right minimap corner (should be masked out)
    frames[1, SH - 5:, SW - 5:] = 200
    # a real subject blob mid-frame that moves (should survive)
    frames[1, SH // 2, SW // 2] = 200
    prof = c._column_motion(frames)
    assert prof.shape == (nf - 1, SW)
    assert prof[:, SW - 3:].sum() == 0, "minimap corner motion must be masked"
    assert prof[:, SW // 2].sum() > 0, "center subject motion must survive"


def test_column_motion_masks_hud_strip():
    frames = np.zeros((3, c.SH, c.SW), dtype=np.int16)
    frames[1, c.SH - 2, c.SW // 4] = 200  # motion inside the bottom HUD strip
    prof = c._column_motion(frames)
    assert prof.sum() == 0, "bottom HUD-strip motion must be masked"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"PASS {name}")
    print("all tracking-unit tests passed")
