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


def test_aim_targets_center_bias_prefers_central_action():
    # equal-magnitude action at center and off-center; the width-`win` window
    # can only capture one, so the center bias must make the CENTRAL one win.
    # (Replaces a symmetric two-blob "tie" test whose loose OR-assertion passed
    # even when the aim landed at the far edge — it did not verify the bias.)
    SW = c.SW
    win = 40
    center = SW // 2
    prof = np.zeros((1, SW))
    prof[0, center - 3:center + 3] = 100.0     # central action
    prof[0, 10:16] = 100.0                      # equal off-center action (far left)
    left = int(c._aim_targets(prof, win)[0])
    center_left = (SW - win) // 2
    assert abs(left - center_left) <= win // 2, (
        f"center bias should pick the central window (got left={left}, "
        f"expected ~{center_left})")


def test_ease_deadzone_holds():
    src_w, crop_w = 1920, 600
    base = 500.0
    targets = base + np.array([0, 20, -15, 10, -20, 5], dtype=float)  # jitter < deadzone
    xs = c._ease(targets, src_w, crop_w, deadzone_px=0.06 * src_w, max_step_px=1e9)
    assert np.ptp(xs) < 1.0, "small jitter inside the deadzone must not move the crop"


def test_ease_velocity_capped():
    src_w, crop_w = 1920, 600
    targets = np.array([0, 1320, 1320, 1320], dtype=float)  # a huge jump then hold
    max_step = 100.0
    xs = c._ease(targets, src_w, crop_w, deadzone_px=0.06 * src_w, max_step_px=max_step)
    assert np.all(np.abs(np.diff(xs)) <= max_step + 1e-6), "per-step motion must be capped"


def test_ease_clamps_out_of_range_targets():
    # targets far outside the valid crop range must never push x past the edges,
    # regardless of the velocity cap. Isolates the clamp guard: without np.clip,
    # x would run away to the raw target value (9999 / -9999).
    src_w, crop_w = 1920, 600
    hi = src_w - crop_w
    xs_hi = c._ease(np.full(60, 9999.0), src_w, crop_w, deadzone_px=0.0, max_step_px=1e9)
    assert xs_hi.max() <= hi + 1e-6, "x must never exceed the upper bound"
    assert abs(xs_hi[-1] - hi) < 1.0, "x settles at the clamped upper bound"
    xs_lo = c._ease(np.full(60, -9999.0), src_w, crop_w, deadzone_px=0.0, max_step_px=1e9)
    assert xs_lo.min() >= -1e-6, "x must never go below 0"
    assert abs(xs_lo[-1]) < 1.0, "x settles at the clamped lower bound"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"PASS {name}")
    print("all tracking-unit tests passed")
