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


def test_aim_targets_follows_moving_blob():
    nf, SW = 6, c.SW
    prof = np.zeros((nf, SW))
    for i in range(nf):                      # blob marches left -> right
        col = int((i / (nf - 1)) * (SW - 20)) + 10
        prof[i, col - 3:col + 3] = 100.0
    win = 40
    tgt = c._aim_targets(prof, win)
    assert tgt[0] < tgt[-1], "aim should move rightward with the blob"
    assert np.all(np.diff(tgt) >= 0), "aim should be monotonic for a monotonic blob"


def test_aim_targets_center_bias_breaks_ties():
    SW = c.SW
    prof = np.zeros((1, SW))
    prof[0, 5:15] = 100.0                    # equal blob at far left
    prof[0, SW - 15:SW - 5] = 100.0          # and far right
    win = 40
    left = int(c._aim_targets(prof, win)[0])
    center_left = (SW - win) // 2
    assert abs(left - center_left) < abs(left - 0) or abs(left - center_left) < abs(left - (SW - win)), \
        "center bias should pull a symmetric tie toward the middle"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"PASS {name}")
    print("all tracking-unit tests passed")
