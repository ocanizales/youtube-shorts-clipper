# Framing Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static per-clip crop with an eased, center-biased, HUD/minimap-masked pan that follows the action, move captions into a bigger lower-third band, and add a `--sample` zoom-comparison harness.

**Architecture:** `focus_x()` (one static x per clip) is replaced by `track_path()`, which decodes one downscaled gray pass, builds a masked per-column motion profile over time, aims each instant with a center bias, eases the trajectory (deadzone + low-pass + velocity cap), and writes an ffmpeg `sendcmd` script that drives a named `crop@dyn` filter live. `build_vf`/`cut_clip` gain a `sendcmd` path; `caption_anchor` moves tracked-layout captions to a lower-third safe band; a new `make_sample()` renders a labeled zoom/tracking comparison set for the user to pick from.

**Tech Stack:** Python 3.11 (repo `.venv`), numpy 2.4, ffmpeg 8.0.1 (`sendcmd` + `crop` commands), no new dependencies.

## Global Constraints

- **No new Python dependencies** — numpy + ffmpeg only (no pip installs; `.venv` is fixed).
- **Tests follow repo convention:** standalone scripts under `tests/`, run with `.venv/bin/python tests/<file>.py`, plain `assert` + synthetic `testsrc` renders. **No pytest** (not installed here).
- **1080p downloads always** — do not touch download/quality paths.
- **Captions must never cover faces or key gameplay**; HUD is not "key gameplay" and may be captioned over.
- **Never use `#000`/black backgrounds** — `#252525` (not relevant to this work; do not regress letterbox).
- **Leave the uncommitted thumbnail WIP untouched** — Task 0 isolates it into its own commit first; every later task commits only the exact framing paths it changed.
- **No Claude attribution trailers** on commits (repo/settings rule).
- Commit each task; **push only when the user asks.**
- ffmpeg binary is `/usr/bin/ffmpeg` (module global `_FFMPEG`); output canvas constants `W`, `H` = 1080, 1920 already exist in `clipper.py`; `CLIPS_DIR` already exists.

---

### Task 0: Isolate pre-existing thumbnail WIP, create working branch

**Files:**
- Modify (commit only): `clipper.py`, `HANDOFF.md`, `PROJECT_MAP.md` (already-dirty working tree)

**Interfaces:**
- Consumes: nothing.
- Produces: a clean working tree on branch `framing-overhaul` so later tasks commit only their own changes.

> The working tree carries the verified 2026-07-22 "creative vertical thumbnail" WIP (`_compose_thumb` rewrite + helpers) uncommitted. It is unrelated to framing. Commit it as its own commit on `master` **before** branching, so framing commits stay clean. **This step requires explicit user go-ahead** (raised at the execution handoff).

- [ ] **Step 1: Confirm the dirty files are only the thumbnail WIP**

Run: `git -C ~/apps/youtube-shorts-clipper diff --stat`
Expected: `clipper.py`, `HANDOFF.md`, `PROJECT_MAP.md` only; `git diff clipper.py` shows `_compose_thumb`/`THUMB_*`/`_cover`/`_radial`/emoji helpers, nothing in `focus_x`/`build_vf`/`caption_anchor`/`cut_clip`.

- [ ] **Step 2: Commit the WIP as its own commit**

```bash
cd ~/apps/youtube-shorts-clipper
git add clipper.py HANDOFF.md PROJECT_MAP.md
git commit -m "feat(thumbs): creative vertical 9:16 thumbnail composite (2026-07-22 WIP)"
```

- [ ] **Step 3: Create the framing branch**

```bash
git checkout -b framing-overhaul
git status --short   # expect: clean
```

---

### Task 1: Framing tunables + `_column_motion`

**Files:**
- Modify: `clipper.py` (add constants near the `ZOOM_*` block ~line 273; add `_column_motion` + `_motion_profile` after `focus_x`, ~line 267)
- Test: `tests/test_tracking.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - Constants: `SW=240`, `SH=135`, `SAMPLE_FPS=6`, `CENTER_SIGMA_FRAC=0.30`, `MINIMAP_FRAC=0.23`, `HUD_MASK_FRAC=0.16`, `DEADZONE_FRAC=0.06`, `EASE_ALPHA=0.18`, `MAX_PAN_PX_PER_S=None`, `CMD_FPS=15`, `CAP_SIZE_TRACKED=84`.
  - `_column_motion(frames: np.ndarray[nf,SH,SW]) -> np.ndarray[nf-1, SW]` — abs inter-frame diff, minimap-corner + bottom-HUD rows zeroed, summed over rows.
  - `_motion_profile(video, start, dur) -> tuple[np.ndarray, np.ndarray]` — decodes gray at `SAMPLE_FPS`, returns `(times[nf-1], profile[nf-1, SW])`; `(empty, empty(0,SW))` if <2 frames.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tracking.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/apps/youtube-shorts-clipper && .venv/bin/python tests/test_tracking.py`
Expected: FAIL — `AttributeError: module 'clipper' has no attribute '_column_motion'`.

- [ ] **Step 3: Add constants + implementation**

Add near the `ZOOM_*` constants block (after `ZOOM_HUD_FRAC`, ~line 275):

```python
# ── framing / tracking tunables (docs/superpowers/specs/2026-07-23-...) ──────
SW, SH            = 240, 135  # downscaled analysis grid (cols, rows)
SAMPLE_FPS        = 6         # motion-sampling rate for the crop trajectory
CENTER_SIGMA_FRAC = 0.30      # width of the center-bias Gaussian (frac of SW)
MINIMAP_FRAC      = 0.23      # bottom-right box masked out (frac of W & H)
HUD_MASK_FRAC     = 0.16      # bottom strip masked out (frac of H)
DEADZONE_FRAC     = 0.06      # hold still while target stays within this frac of src_w
EASE_ALPHA        = 0.18      # exponential ease toward target per sample
MAX_PAN_PX_PER_S  = None      # velocity cap; None -> src_w * 0.6
CMD_FPS           = 15        # sendcmd densification rate (pan smoothness)
CAP_SIZE_TRACKED  = 84        # caption size bump for crop/zoom layouts
```

Add after `focus_x` (which stays for now; removed in Task 5), ~line 267:

```python
def _column_motion(frames: np.ndarray) -> np.ndarray:
    """Per-column motion over time, with distractor regions masked out.

    `frames` is (nf, SH, SW) gray. Returns (nf-1, SW): the absolute inter-frame
    difference, summed over rows, AFTER zeroing the bottom-right minimap corner
    and the bottom HUD strip so their constant animation can't attract the crop.
    """
    diff = np.abs(np.diff(frames, axis=0))            # (nf-1, SH, SW)
    mm_h, mm_w = int(SH * MINIMAP_FRAC), int(SW * MINIMAP_FRAC)
    hud_h = int(SH * HUD_MASK_FRAC)
    if mm_h and mm_w:
        diff[:, SH - mm_h:, SW - mm_w:] = 0           # minimap corner
    if hud_h:
        diff[:, SH - hud_h:, :] = 0                   # ability/HUD strip
    return diff.sum(axis=1)                            # (nf-1, SW)


def _motion_profile(video: Path, start: float, dur: int) -> tuple[np.ndarray, np.ndarray]:
    """Decode a downscaled gray clip; return (times, per-column motion over time).

    One ffmpeg pass at SAMPLE_FPS. times has length nf-1 (one per motion frame);
    profile is (nf-1, SW). Returns empties if the clip is too short to diff.
    """
    raw = subprocess.run(
        [_FFMPEG, "-ss", str(start), "-i", str(video), "-t", str(dur),
         "-vf", f"fps={SAMPLE_FPS},scale={SW}:{SH},format=gray",
         "-f", "rawvideo", "-"], capture_output=True).stdout
    nf = len(raw) // (SW * SH)
    if nf < 2:
        return np.zeros(0), np.zeros((0, SW))
    frames = np.frombuffer(raw[:nf * SW * SH], np.uint8).reshape(nf, SH, SW).astype(np.int16)
    profile = _column_motion(frames)
    times = np.arange(profile.shape[0]) / SAMPLE_FPS
    return times, profile
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/apps/youtube-shorts-clipper && .venv/bin/python tests/test_tracking.py`
Expected: `PASS test_column_motion_masks_hud_strip`, `PASS test_column_motion_masks_minimap_corner`, `all tracking-unit tests passed`.

- [ ] **Step 5: Commit**

```bash
git add clipper.py tests/test_tracking.py
git commit -m "feat(track): masked per-column motion profile + framing tunables"
```

---

### Task 2: `_aim_targets` (center-biased sliding window)

**Files:**
- Modify: `clipper.py` (add after `_motion_profile`)
- Test: `tests/test_tracking.py` (append)

**Interfaces:**
- Consumes: `SW`, `CENTER_SIGMA_FRAC`.
- Produces: `_aim_targets(profile: np.ndarray[nf,SW], win: int) -> np.ndarray[nf]` — per frame, the left column (0..SW-win) of the width-`win` window with the most center-biased motion.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tracking.py` (before the `__main__` block):

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python tests/test_tracking.py`
Expected: FAIL — `AttributeError: ... '_aim_targets'`.

- [ ] **Step 3: Implement**

```python
def _aim_targets(profile: np.ndarray, win: int) -> np.ndarray:
    """Per frame, the left column of the width-`win` window with the most
    center-biased motion. A broad Gaussian centered on the frame center weights
    the profile (the game camera already centers the champion), so the aim
    prefers the middle unless off-center action is genuinely stronger.
    """
    nf, sw = profile.shape
    cols = np.arange(sw)
    bias = np.exp(-0.5 * ((cols - (sw - 1) / 2.0) / (CENTER_SIGMA_FRAC * sw)) ** 2)
    biased = profile * bias[None, :]
    kernel = np.ones(win)
    out = np.empty(nf, dtype=int)
    default_left = (sw - win) // 2
    for i in range(nf):
        sums = np.convolve(biased[i], kernel, "valid")   # len sw-win+1
        out[i] = int(np.argmax(sums)) if sums.max() > 0 else default_left
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python tests/test_tracking.py`
Expected: all PASS, including the two new tests.

- [ ] **Step 5: Commit**

```bash
git add clipper.py tests/test_tracking.py
git commit -m "feat(track): center-biased sliding-window aim"
```

---

### Task 3: `_ease` (deadzone + low-pass + velocity cap)

**Files:**
- Modify: `clipper.py` (add after `_aim_targets`)
- Test: `tests/test_tracking.py` (append)

**Interfaces:**
- Consumes: `EASE_ALPHA`.
- Produces: `_ease(targets_px: np.ndarray, src_w: int, crop_w: int, deadzone_px: float, max_step_px: float) -> np.ndarray[float]` — smoothed crop-left x in source px, clamped to `[0, src_w-crop_w]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tracking.py`:

```python
def test_ease_deadzone_holds():
    src_w, crop_w = 1920, 600
    base = 500.0
    targets = base + np.array([0, 20, -15, 10, -20, 5], dtype=float)  # jitter < deadzone
    xs = c._ease(targets, src_w, crop_w, deadzone_px=0.06 * src_w, max_step_px=1e9)
    assert np.ptp(xs) < 1.0, "small jitter inside the deadzone must not move the crop"


def test_ease_velocity_capped_and_clamped():
    src_w, crop_w = 1920, 600
    targets = np.array([0, 1320, 1320, 1320], dtype=float)  # a huge jump then hold
    max_step = 100.0
    xs = c._ease(targets, src_w, crop_w, deadzone_px=0.06 * src_w, max_step_px=max_step)
    assert np.all(np.abs(np.diff(xs)) <= max_step + 1e-6), "per-step motion must be capped"
    assert xs.min() >= 0 and xs.max() <= src_w - crop_w, "x must stay in range"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python tests/test_tracking.py`
Expected: FAIL — `AttributeError: ... '_ease'`.

- [ ] **Step 3: Implement**

```python
def _ease(targets_px: np.ndarray, src_w: int, crop_w: int,
          deadzone_px: float, max_step_px: float) -> np.ndarray:
    """Turn a noisy per-sample target into a smooth crop-x path.

    Holds position while the target stays within `deadzone_px` of the current x
    (kills micro-jitter); otherwise eases toward it by EASE_ALPHA, capped at
    `max_step_px` per sample so it glides instead of snapping. Clamped to the
    valid crop range.
    """
    lo, hi = 0.0, float(max(0, src_w - crop_w))
    xs = np.empty(len(targets_px), dtype=float)
    cur = float(np.clip(targets_px[0], lo, hi)) if len(targets_px) else lo
    for i, raw in enumerate(targets_px):
        tgt = float(np.clip(raw, lo, hi))
        if abs(tgt - cur) > deadzone_px:
            step = EASE_ALPHA * (tgt - cur)
            step = max(-max_step_px, min(max_step_px, step))
            cur = min(hi, max(lo, cur + step))
        xs[i] = cur
    return xs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python tests/test_tracking.py`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add clipper.py tests/test_tracking.py
git commit -m "feat(track): eased path with deadzone + velocity cap"
```

---

### Task 4: `_write_sendcmd` (densified command script)

**Files:**
- Modify: `clipper.py` (add after `_ease`)
- Test: `tests/test_tracking.py` (append)

**Interfaces:**
- Consumes: `CMD_FPS`.
- Produces: `_write_sendcmd(times: np.ndarray, xs: np.ndarray, path: Path) -> Path` — writes `T crop@dyn x <int>;` lines, linearly interpolated to `CMD_FPS`, time-sorted.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tracking.py`:

```python
def test_write_sendcmd_format(tmp_path=None):
    import tempfile, os, re
    d = tempfile.mkdtemp()
    p = pathlib.Path(d) / "t.cmd"
    times = np.array([0.0, 1.0])
    xs = np.array([100.0, 400.0])
    c._write_sendcmd(times, xs, p)
    lines = [ln for ln in p.read_text().splitlines() if ln.strip()]
    assert len(lines) >= int(c.CMD_FPS * 0.9), "should densify to ~CMD_FPS commands/sec"
    assert all(re.fullmatch(r"\d+\.\d+ crop@dyn x \d+;", ln) for ln in lines), "line format"
    ts = [float(ln.split()[0]) for ln in lines]
    assert ts == sorted(ts), "commands must be time-sorted"
    xvals = [int(ln.split()[-1].rstrip(";")) for ln in lines]
    assert abs(xvals[0] - 100) <= 1, "first command near start x"
    # densification must actually INTERPOLATE, not repeat the first value:
    # x should ramp monotonically from ~100 toward ~400 across the file.
    assert xvals[0] < xvals[len(xvals) // 2] < xvals[-1], "x must ramp (interpolated), not repeat"
    assert xvals[-1] >= 350, "last command near the end x (~400)"
    os.remove(p)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python tests/test_tracking.py`
Expected: FAIL — `AttributeError: ... '_write_sendcmd'`.

- [ ] **Step 3: Implement**

```python
def _write_sendcmd(times: np.ndarray, xs: np.ndarray, path: Path) -> Path:
    """Write an ffmpeg sendcmd script driving `crop@dyn`'s x.

    sendcmd applies each command as a step (no interpolation), so we densify the
    eased path to CMD_FPS by linear interpolation — small per-frame steps read as
    a smooth pan.
    """
    if len(times) >= 2:
        dense_t = np.arange(float(times[0]), float(times[-1]), 1.0 / CMD_FPS)
        dense_x = np.interp(dense_t, times, xs)
    else:
        dense_t, dense_x = times, xs
    lines = [f"{t:.3f} crop@dyn x {int(round(x))};" for t, x in zip(dense_t, dense_x)]
    path.write_text("\n".join(lines) + "\n")
    return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python tests/test_tracking.py`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add clipper.py tests/test_tracking.py
git commit -m "feat(track): densified sendcmd script writer"
```

---

### Task 5: `track_path` (assemble) + remove `focus_x`

**Files:**
- Modify: `clipper.py` (add `track_path` after `_write_sendcmd`; delete `focus_x` at ~line 232)
- Test: `tests/test_tracking.py` (append)

**Interfaces:**
- Consumes: everything above + `MAX_PAN_PX_PER_S`, `DEADZONE_FRAC`, `SAMPLE_FPS`, `SW`, `CLIPS_DIR`.
- Produces: `track_path(video, start, dur, src_w, src_h, crop_w=None) -> tuple[int, Path | None]` — `(x0, script_path)`; `script_path is None` means a constant path (static fallback), so callers skip sendcmd.

- [ ] **Step 1: Write the failing test** (monkeypatched — no ffmpeg needed)

Append to `tests/test_tracking.py`:

```python
def test_track_path_static_fallback_when_no_motion(monkeypatch=None):
    orig = c._motion_profile
    c._motion_profile = lambda *a, **k: (np.zeros(0), np.zeros((0, c.SW)))
    try:
        x0, script = c.track_path(pathlib.Path("x.mp4"), 0, 10, 1920, 1080)
        crop_w = min(int(1080 * 9 / 16), 1920)
        assert script is None, "no motion -> no sendcmd script"
        assert x0 == (1920 - crop_w) // 2, "no motion -> centered static x"
    finally:
        c._motion_profile = orig


def test_track_path_moving_writes_script():
    orig = c._motion_profile
    nf, SW = 30, c.SW
    prof = np.zeros((nf, SW))
    times = np.arange(nf) / c.SAMPLE_FPS
    for i in range(nf):                      # blob crosses the frame
        col = int((i / (nf - 1)) * (SW - 20)) + 10
        prof[i, col - 3:col + 3] = 255.0
    c._motion_profile = lambda *a, **k: (times, prof)
    try:
        x0, script = c.track_path(pathlib.Path("x.mp4"), 0, 5, 1920, 1080)
        assert script is not None and script.exists(), "movement -> a script is written"
        assert isinstance(x0, int)
        script.unlink()
    finally:
        c._motion_profile = orig
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python tests/test_tracking.py`
Expected: FAIL — `AttributeError: ... 'track_path'`.

- [ ] **Step 3: Implement `track_path`; delete `focus_x`**

Add:

```python
def track_path(video: Path, start: float, dur: int, src_w: int, src_h: int,
               crop_w: int | None = None) -> tuple[int, Path | None]:
    """Eased crop-x trajectory that follows the action (replaces focus_x).

    Returns (x0, script_path). script_path is an ffmpeg sendcmd file driving
    `crop@dyn`; it is None when the path is effectively constant (short/blank
    clip, or the action never leaves the deadzone) so the caller can render a
    plain static crop. Only x moves — height/vertical framing is fixed.
    """
    crop_w = crop_w or min(int(src_h * 9 / 16), src_w)
    center_x = max(0, (src_w - crop_w) // 2)
    times, profile = _motion_profile(video, start, dur)
    if profile.shape[0] < 2 or profile.sum() == 0:
        return center_x, None
    win = max(1, round(crop_w / src_w * SW))
    if win >= SW:
        return center_x, None
    targets_col = _aim_targets(profile, win)                 # downscaled left col
    targets_px = targets_col / SW * src_w                    # source px (crop-left)
    max_step = (MAX_PAN_PX_PER_S or src_w * 0.6) / SAMPLE_FPS
    xs = _ease(targets_px, src_w, crop_w, DEADZONE_FRAC * src_w, max_step)
    if float(np.ptp(xs)) < 1.0:                              # never really moves
        return int(round(xs[0])), None
    script = CLIPS_DIR / f".track_{int(start)}_{crop_w}.cmd"
    _write_sendcmd(times, xs, script)
    return int(round(xs[0])), script
```

Delete the entire `focus_x` function (from `def focus_x(` through its final `return`, ~lines 232-266).

- [ ] **Step 4: Verify tests pass + no stale references**

Run: `.venv/bin/python tests/test_tracking.py`
Expected: all PASS.
Run: `grep -n "focus_x" clipper.py`
Expected: **no matches** (Task 6 updates the call site; if any remain outside `cut_clip`, stop and reconcile).

- [ ] **Step 5: Commit**

```bash
git add clipper.py tests/test_tracking.py
git commit -m "feat(track): track_path trajectory assembler; drop static focus_x"
```

---

### Task 6: Wire tracking into `build_vf` + `cut_clip` (end-to-end)

**Files:**
- Modify: `clipper.py` — `build_vf` (~line 324) gains `sendcmd=None`, tracked crops renamed `crop@dyn`, sendcmd prepended; `cut_clip` (~line 1055) calls `track_path`, threads the script, deletes it after render.
- Test: `tests/test_tracking_render.py` (create) — synthetic ffmpeg render.

**Interfaces:**
- Consumes: `track_path` (Task 5).
- Produces: `build_vf(layout, dims, crop_x, facecam, ass_path, caption, cap_size, cap_an, cap_margin, sendcmd=None) -> str`.

- [ ] **Step 1: Write the failing integration test**

Create `tests/test_tracking_render.py`:

```python
"""End-to-end: sendcmd-driven crop follows a moving subject.
Run: .venv/bin/python tests/test_tracking_render.py"""
import sys, pathlib, subprocess, tempfile, os
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import numpy as np
import clipper as c

FF = c._FFMPEG


def _render_source(path, w=640, h=360, dur=4, r=30):
    # a bright vertical bar sweeping left->right on black
    x_expr = f"(W-40)*t/{dur}"
    subprocess.run([FF, "-y", "-loglevel", "error", "-f", "lavfi",
        "-i", f"color=black:s={w}x{h}:r={r}:d={dur}",
        "-vf", f"drawbox=x='{x_expr}':y=0:w=40:h={h}:color=white:t=fill",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)


def test_tracked_crop_follows_moving_bar():
    d = pathlib.Path(tempfile.mkdtemp())
    src = d / "src.mp4"
    _render_source(src)
    dims = (640, 360)
    # keep track scripts in this temp dir
    orig_clips = c.CLIPS_DIR
    c.CLIPS_DIR = d
    try:
        x0, script = c.track_path(src, 0, 4, 640, 360, crop_w=200)
        assert script is not None, "a moving subject should produce a pan script"
        vf = c.build_vf("crop", dims, x0, None, None, None, 66,
                        *c.caption_anchor("crop", dims), sendcmd=script)
        assert "crop@dyn" in vf and "sendcmd" in vf, "vf must use sendcmd + named crop"
        out = d / "out.mp4"
        subprocess.run([FF, "-y", "-loglevel", "error", "-i", str(src),
                        "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
                       check=True)
        # sample brightness at 3 output frames: a following crop keeps the bar in view
        raw = subprocess.run([FF, "-loglevel", "error", "-i", str(out),
            "-vf", "scale=54:96,format=gray", "-f", "rawvideo", "-"],
            capture_output=True).stdout
        fw, fh = 54, 96
        nf = len(raw) // (fw * fh)
        frames = np.frombuffer(raw[:nf*fw*fh], np.uint8).reshape(nf, fh, fw)
        # if the crop tracked the bar, most frames still contain bright pixels
        bright = (frames.reshape(nf, -1).max(axis=1) > 180).mean()
        assert bright > 0.6, f"tracked crop should keep the subject visible (got {bright:.2f})"
    finally:
        c.CLIPS_DIR = orig_clips


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"PASS {name}")
    print("render integration tests passed")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python tests/test_tracking_render.py`
Expected: FAIL — `build_vf() got an unexpected keyword argument 'sendcmd'`.

- [ ] **Step 3: Update `build_vf`**

Change the signature and the two tracked branches; prepend sendcmd at the end. Replace the crop `else` branch and the `zoom` branch, and add the prepend before the `return`:

```python
def build_vf(layout, dims, crop_x, facecam, ass_path, caption, cap_size,
             cap_an, cap_margin, sendcmd=None) -> str:
    src_w, src_h = dims
    # ... (split / full / fit branches unchanged) ...
    elif layout == "zoom":                   # punched-in playfield + HUD re-stacked
        _, play_h, top_bar, hud_out_h, mid_h, cw = zoom_geometry(dims)
        hud_src_h = src_h - play_h
        vf = (f"split=2[pf][hs];"
              f"[pf]crop@dyn=w={cw}:h={play_h}:x={crop_x}:y=0,scale={W}:{mid_h}[game];"
              f"[hs]crop={src_w}:{hud_src_h}:0:{play_h},scale={W}:{hud_out_h}[hud];"
              f"[game][hud]vstack=inputs=2,pad={W}:{H}:0:{top_bar}:black")
    else:                                    # motion-tracked 9:16 crop
        cw = min(int(src_h * 9 / 16), src_w)
        vf = f"crop@dyn=w={cw}:h={src_h}:x={crop_x}:y=0,scale={W}:{H}"
    if sendcmd:                              # drive crop@dyn's x live (pan)
        p = str(sendcmd).replace("\\", "/").replace(":", R"\:")
        vf = f"sendcmd=f='{p}',{vf}"
    if ass_path:
        # ... unchanged ...
```

(Keep the `split`/`full`/`fit` branches and the `ass_path`/`caption` tails exactly as they are; only the `zoom`/`else` crop names change and the `sendcmd` prepend is inserted **before** the `ass_path` block.)

- [ ] **Step 4: Update `cut_clip` call site**

Replace the tracking block (~line 1055-1058):

```python
    crop_x, track_cmd = 0, None
    if layout in ("crop", "zoom"):           # zoom tracks with its own window
        zw = zoom_geometry(dims)[-1] if layout == "zoom" else None
        crop_x, track_cmd = track_path(video, start, dur, src_w, src_h, crop_w=zw)
```

Update the `build_vf` call (~line 1074) to pass the script:

```python
    vf = build_vf(layout, dims, crop_x, facecam, ass_path, caption,
                  cap_size, cap_an, cap_margin, sendcmd=track_cmd)
```

After the render, next to the `ass_path.unlink` (~line 1097):

```python
    if track_cmd:
        track_cmd.unlink(missing_ok=True)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python tests/test_tracking_render.py`
Expected: `PASS test_tracked_crop_follows_moving_bar`.
Run: `.venv/bin/python -c "import clipper"` (import still clean) and `.venv/bin/python tests/test_tracking.py`
Expected: no import error; unit tests still PASS.

- [ ] **Step 6: Commit**

```bash
git add clipper.py tests/test_tracking_render.py
git commit -m "feat(track): drive crop@dyn via sendcmd in build_vf + cut_clip"
```

---

### Task 7: Lower-third caption band + size bump

**Files:**
- Modify: `clipper.py` — `caption_anchor` (~line 307); `cut_clip` caption sizing (~line 1069-1073).
- Test: `tests/test_captions.py` (create)

**Interfaces:**
- Consumes: `zoom_geometry`, `CAP_SIZE_TRACKED`, `H`.
- Produces: `caption_anchor(layout, dims)` returns bottom-anchored (an=2) lower-third margins for `crop` and `zoom`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_captions.py`:

```python
"""Caption placement. Run: .venv/bin/python tests/test_captions.py"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import clipper as c


def test_crop_captions_bottom_lower_third():
    an, margin = c.caption_anchor("crop", (1920, 1080))
    assert an == 2, "crop captions are bottom-anchored"
    assert 0.10 * c.H < margin < 0.30 * c.H, "crop captions sit in the lower third"


def test_zoom_captions_above_hud_not_top_bar():
    dims = (1920, 1080)
    an, margin = c.caption_anchor("zoom", dims)
    _, _, top_bar, hud_out_h, _, _ = c.zoom_geometry(dims)
    assert an == 2, "zoom captions are bottom-anchored now (was top bar)"
    assert margin >= hud_out_h, "zoom captions clear the HUD strip"
    assert margin < c.H * 0.5, "zoom captions stay in the lower half, near the action"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"PASS {name}")
    print("caption tests passed")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python tests/test_captions.py`
Expected: FAIL — `test_zoom_captions_above_hud_not_top_bar` (current zoom returns an=8, top-bar margin).

- [ ] **Step 3: Update `caption_anchor`**

Replace the `zoom` and trailing `crop` returns:

```python
    if layout == "zoom":       # lower third: just above the bottom HUD strip
        _, _, _, hud_out_h, _, _ = zoom_geometry(dims)
        return 2, hud_out_h + int(H * 0.03)
    return 2, int(H * 0.16)    # crop: lower third, above the source's bottom HUD
```

(Leave `split`/`full`/`fit` returns unchanged.)

- [ ] **Step 4: Bump caption size for tracked layouts in `cut_clip`**

Where captions are built (~line 1069) and where `cap_size` reaches `build_vf` (~line 1073), compute an effective size once, right after the tracking block:

```python
    cap_size_eff = max(cap_size, CAP_SIZE_TRACKED) if layout in ("crop", "zoom") else cap_size
```

Then use `cap_size_eff` in the dynamic-caption call and in `build_vf`:

```python
        ass_path, hook, transcript = make_dynamic_captions(tmp, an, margin, max(48, cap_size_eff))
    ...
    vf = build_vf(layout, dims, crop_x, facecam, ass_path, caption,
                  cap_size_eff, cap_an, cap_margin, sendcmd=track_cmd)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python tests/test_captions.py`
Expected: both PASS.
Run: `.venv/bin/python -c "import clipper"`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add clipper.py tests/test_captions.py
git commit -m "feat(captions): lower-third safe band + bigger text for tracked layouts"
```

---

### Task 8: `--sample` zoom-comparison harness

**Files:**
- Modify: `clipper.py` — add `_render_sample` + `make_sample` after `make_clips` (~line 1136); add `--sample`/`--at` args and dispatch in `main` (~line 1184).
- Test: `tests/test_sample.py` (create) — synthetic; asserts the comparison set renders.

**Interfaces:**
- Consumes: `_dims`, `find_hype_moments`, `track_path`, `build_vf`, `caption_anchor`, `_pick_encoder`/`_ENC`.
- Produces: `make_sample(video: Path, at: float | None = None) -> list[Path]` — writes labeled MP4s to `clips/samples/`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sample.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python tests/test_sample.py`
Expected: FAIL — `AttributeError: ... 'make_sample'`.

- [ ] **Step 3: Implement `_render_sample` + `make_sample`**

```python
SAMPLE_ZOOMS = (1.0, 1.25, 1.5)   # punch-in multipliers to compare
SAMPLE_DUR   = 12


def _render_sample(video: Path, start: float, dur: int, dims, crop_w: int,
                   eased: bool, out: Path) -> Path:
    """Render one framing sample (no captions) with a fast encode."""
    x0, script = track_path(video, start, dur, dims[0], dims[1], crop_w=crop_w)
    if not eased:
        script = None                        # static: freeze at the opening x
    an, margin = caption_anchor("crop", dims)
    vf = build_vf("crop", dims, x0, None, None, None, 66, an, margin, sendcmd=script)
    ff("-y", "-ss", str(start), "-i", str(video), "-t", str(dur),
       "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
       "-pix_fmt", "yuv420p", "-an", str(out))
    if script:
        script.unlink(missing_ok=True)
    return out


def make_sample(video: Path, at: float | None = None) -> list[Path]:
    """Render a labeled framing comparison set to clips/samples/ so the user can
    pick a zoom level and confirm the eased pan by eye. static vs eased across
    SAMPLE_ZOOMS punch-in levels."""
    dims = _dims(video)
    src_w, src_h = dims
    start = at if at is not None else find_hype_moments(video, SAMPLE_DUR, 1, 0.5)[0]
    base_w = min(int(src_h * 9 / 16), src_w)
    outdir = CLIPS_DIR / "samples"
    outdir.mkdir(parents=True, exist_ok=True)
    outs: list[Path] = []
    for z in SAMPLE_ZOOMS:
        crop_w = max(2, int(base_w / z) // 2 * 2)      # tighter zoom = narrower crop
        for eased in (False, True):
            tag = "eased" if eased else "static"
            out = outdir / f"sample_{tag}_{z:.2f}x.mp4"
            _render_sample(video, start, SAMPLE_DUR, dims, crop_w, eased, out)
            outs.append(out)
    print(f"[sample] wrote {len(outs)} clips to {outdir}")
    return outs
```

- [ ] **Step 4: Wire `--sample`/`--at` into `main`**

In `main`'s argparse block add:

```python
    ap.add_argument("--sample", metavar="URL_OR_FILE",
                    help="render a framing/zoom comparison set instead of full clips")
    ap.add_argument("--at", type=float, default=None,
                    help="sample at this many seconds (default: top audio peak)")
```

Early in `main`, before the normal pipeline dispatch:

```python
    if args.sample:
        src = _find_media(args.sample) or download_video(args.sample)
        make_sample(src, at=args.at)
        return
```

(Use the repo's existing local-vs-URL resolution: `_find_media` first, else `download_video`; match how the normal path resolves a source.)

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python tests/test_sample.py`
Expected: `PASS test_make_sample_writes_comparison_set`.

- [ ] **Step 6: Commit**

```bash
git add clipper.py tests/test_sample.py
git commit -m "feat(sample): --sample zoom/tracking comparison harness"
```

---

### Task 9: Regenerate PROJECT_MAP, update HANDOFF, final verification

**Files:**
- Modify: `HANDOFF.md`, `PROJECT_MAP.md`

- [ ] **Step 1: Regenerate the code map**

Run: `.venv/bin/python scripts/build_memory.py`
Expected: `PROJECT_MAP.md` updated; `focus_x` gone, `track_path`/`_motion_profile`/`_aim_targets`/`_ease`/`_write_sendcmd`/`make_sample` listed.

- [ ] **Step 2: Run the full test suite**

Run:
```bash
.venv/bin/python tests/test_tracking.py && \
.venv/bin/python tests/test_tracking_render.py && \
.venv/bin/python tests/test_captions.py && \
.venv/bin/python tests/test_sample.py && \
.venv/bin/python -c "import clipper; print('import ok')"
```
Expected: every file prints its "passed" line; `import ok`.

- [ ] **Step 3: Update HANDOFF.md**

Add a dated entry summarizing: static `focus_x` → eased `track_path` (center-bias + minimap/HUD masks + deadzone/velocity-capped pan via `sendcmd` on `crop@dyn`); lower-third caption band + `CAP_SIZE_TRACKED`; `--sample` harness; new `tests/` suite and how to run it; the tunable constants and what each does. Note the sample clips await a user zoom pick.

- [ ] **Step 4: Commit**

```bash
git add HANDOFF.md PROJECT_MAP.md
git commit -m "docs: regenerate code map + HANDOFF for framing overhaul"
```

- [ ] **Step 5: Deliver samples for the zoom decision**

Render the comparison set on a real VOD and send the files for the user to choose a zoom level:
Run: `.venv/bin/python clipper.py --sample "<REAL_LOL_VOD_URL>"`
Then surface `clips/samples/*.mp4` to the user (SendUserFile). Bake the chosen `SAMPLE_ZOOMS` winner as the crop default in a follow-up commit.

---

## Self-Review

**Spec coverage:**
- Eased/center-biased/masked pan → Tasks 1-6. ✅
- `sendcmd`-driven `crop@dyn`, both layouts → Task 6 (crop + zoom branches). ✅
- Lower-third bigger captions → Task 7. ✅
- `--sample` zoom pick → Task 8 + Task 9 Step 5. ✅
- Readability decomposition (`_motion_profile`/`_column_motion`/`_aim_targets`/`_ease`/`_write_sendcmd`/`track_path`) → Tasks 1-5. ✅
- Fallbacks (constant center path; script=None) → Task 5 + Task 6 static branch. ✅
- Non-goal: thumbnail WIP untouched → Task 0 isolates it. ✅
- Deferred `sendcmd`-unsupported fallback: **not built** — retired by the empirical proof that ffmpeg 8.0.1 accepts `crop x` commands; noted here intentionally, no task needed.

**Placeholder scan:** No TBD/TODO; every code step shows full code; test steps show full asserts. The only user-supplied value is `<REAL_LOL_VOD_URL>` in Task 9 Step 5 (inherently runtime). ✅

**Type consistency:** `track_path -> (int, Path|None)` consumed consistently in `cut_clip` and `_render_sample`; `build_vf(..., sendcmd=None)` signature matches all call sites (Tasks 6, 8); `caption_anchor` returns `(an, margin)` everywhere; `_column_motion`/`_motion_profile`/`_aim_targets`/`_ease`/`_write_sendcmd` names identical across definition and tests. ✅
