# Design — Framing overhaul: eased tracking + lower-third captions + zoom sampling

_Date: 2026-07-23 · Status: approved (design), pre-implementation_

## Problem

The 9:16 reframing "looks at no one" and feels sluggish, and captions read as
far from the action. Root causes, confirmed in `clipper.py`:

1. **Static crop per clip.** `focus_x()` (clipper.py:232) returns **one** crop-x
   for the *entire* clip (called once in `cut_clip`, clipper.py:1058). During a
   45s highlight the frame never moves — if the fight roams, the crop is frozen
   on empty ground. This is the "way too slow / looking at no one."
2. **Wrong attractor.** `focus_x` scores columns by raw frame-differencing motion
   at 240×135/4fps. In LoL the strongest pixel-motion is minion waves, spell
   particles, floating combat text, and the animated **minimap** — not the
   champion. So even the single position it picks is often off the player.
3. **Captions far from the eye.** `caption_anchor()` (clipper.py:307) puts crop
   captions at `H*0.24` (~460px up) and **zoom** captions in a **top black bar**,
   physically distant from the center action the viewer is watching.

## Goals

- Frame **follows the action** within a clip, smoothly (eased, no jitter, no
  snapping), and **holds still** when the action is roughly centered.
- Frame aims at the **champion**, not minion/particle/minimap noise.
- Captions sit in a **consistent, bigger lower-third safe band** across layouts.
- Let the user **pick the zoom level from real sample renders**, not guesswork.
- Make the framing code **legible** — small, named, documented units.

## Non-goals (YAGNI)

- No champion/health-bar ML or object detection. Center-bias + region masking
  gets ~90% of the benefit at a fraction of the risk/cost.
- No facecam/split-layout changes. No new Python dependencies.
- No change to highlight detection, download, encode/quality, or thumbnails
  (the uncommitted thumbnail WIP on `master` is untouched by this work).

## Research summary

- **Reference repo `jipraks/yt-short-clipper`** is a talking-head/podcast clipper
  (Haar/MediaPipe face + active-speaker tracking), but its *framing philosophy*
  is the borrowable part: it does **not** pan continuously — it **stabilizes the
  crop within a "shot"** (~210 frames ≈ 7s min) and cuts between subjects. Its
  captions live in the **lower third (~400px from bottom)**, ~65px Arial Black,
  white with **yellow word-by-word** highlight, 4px outline, semi-transparent bg.
- **LoL / vertical-short convention:** *center-cut punch-in* for locked-camera
  play; *pan-and-scan* (animate the frame to follow the subject), smoothed/eased,
  only when the action actually moves. Keep the subject in the middle safe zone.
- **Key insight:** the LoL game camera already centers the player's champion, so
  it is near frame-center most of the time. The current code fights this by
  chasing off-center minion motion. Biasing toward center + masking the HUD and
  minimap corrects the attractor cheaply.

## Design

### 1. Dynamic eased tracking — `focus_x` → `track_path()`

Replace the single-value `focus_x` with a trajectory producer. Same single
downscaled-gray decode as today (one ffmpeg pass); we keep the time axis instead
of collapsing it, so **analysis cost is unchanged**.

Pipeline (each step a small, documented helper):

- `_motion_profile(video, start, dur, crop_w) -> (times, profile)`
  Decode `fps=SAMPLE_FPS, scale=240:135, format=gray`; per-frame absolute inter-
  frame diff summed over rows → a `(nframes, 240)` column-motion matrix; keep the
  time dimension (do **not** collapse). `SAMPLE_FPS ≈ 6`.

- `_aim(profile_row, src_w, crop_w) -> target_x`  (per sampled instant)
  1. **Center bias:** multiply the column-motion row by a broad Gaussian centered
     on frame center (σ ≈ 0.30·width). Pulls the aim toward the champion unless
     off-center action is overwhelming.
  2. **Region masks:** zero the **minimap corner** (default bottom-right box,
     `MINIMAP_FRAC` of width/height) and the **bottom HUD strip** columns so
     their animation cannot attract the crop. (Masks are on the downscaled grid,
     controlled by the `MINIMAP_FRAC` / `HUD_MASK_FRAC` constants; set either to 0
     to disable that mask.)
  3. Slide the crop-width window across the biased/masked profile; the window with
     the most captured motion gives `target_x` (same "most-action window" idea as
     today, but per-instant and center-aware).

- `_ease(targets, times) -> xs`  (turn noisy targets into a smooth path)
  - **Deadzone:** while `target_x` stays within `DEADZONE_FRAC` of the current
    held x, do not move (kills micro-jitter; the reference repo's "stabilize
    within a shot", made continuous).
  - **Low-pass + velocity limit:** when the target leaves the deadzone, move the
    crop toward it with an exponential ease (`EASE_ALPHA`) capped at
    `MAX_PAN_PX_PER_S` so it glides and never snaps.
  - Clamp every x to `[0, src_w - crop_w]`.

- `_write_sendcmd(xs, times, path)` → an ffmpeg `sendcmd` script:
  `T crop@dyn x <val>;` lines at each sample time (densified/interpolated toward
  ~15fps for smoothness). Returns the script path.

Fallbacks: if `nframes < 2` or total motion is 0, return a constant center path
(current behavior). Any decode failure → constant center x.

### 2. Driving the moving crop — `build_vf` integration (`sendcmd`)

Name the crop instance `crop@dyn` and attach a `sendcmd` source that feeds it the
trajectory. Applies to **both** tracked layouts:

- **crop** layout: `sendcmd=f='<script>',crop@dyn=w=CW:h=SRC_H:x=<x0>:y=0,scale=W:H`
- **zoom** layout: same, with zoom's narrower `crop_w` (from `zoom_geometry`) as
  `CW` and `play_h` as the crop height, feeding the existing playfield band.

`cut_clip` calls `track_path(...)` (returning `(x0, script_path)`) instead of
`focus_x(...)`; passes `x0`/`script_path` into `build_vf`; deletes the script
after render (like the `.ass` cleanup at clipper.py:1097). **Mechanism chosen:
`sendcmd` over a baked `crop=x='if(between…)'` expression** (unreadable at ~100+
segments) and over an opencv per-frame pass (extra decode/encode, slower).

_Impl note to verify first:_ confirm this ffmpeg build (8.0.1) accepts `x` as a
runtime **command** on `crop` (it is documented as command-capable). If not, fall
back to a piecewise `crop=x='<expr>'` generated from the same trajectory.

### 3. Caption placement rework — `caption_anchor`

One consistent, bigger **lower-third safe band** for the tracked layouts:

- **crop:** alignment 2 (bottom-anchored), margin tuned to sit in the lower third
  but **above the ability HUD** at the source's bottom edge (start ≈ `H*0.16`,
  finalized against a real render).
- **zoom:** move captions **out of the top black bar** down to the
  playfield→HUD boundary / over the HUD strip. The HUD is not "key gameplay", so
  this honors the hard rule *"captions must never cover faces or key gameplay."*
- Bump default caption size for the tracked layouts; keep `split`/`full`/`fit`
  anchors as-is. Word-by-word reveal, silence-clear, and `has_existing_captions`
  skip are unchanged.

### 4. Zoom sampling harness — `--sample`

New CLI path: `python clipper.py --sample <VOD_url_or_file> [--at SECONDS]`.
Cuts one ~12s moment (audio-peak if `--at` omitted) and renders a small labeled
**comparison set** to `clips/samples/`:

- old static tracking vs new eased tracking, and
- ~3 punch-in levels: `1.0×` (current), `1.25×`, `1.5×` tighter,

each a separate MP4 named `sample_<layout>_<track>_<zoom>.mp4`. The chosen
`(zoom, layout)` becomes the baked default. No GUI needed — files are delivered
for eyeball comparison (SendUserFile).

### 5. Readability

The framing logic lands as small single-purpose helpers, each with a one-line
docstring: `_motion_profile`, `_aim`, `_ease`, `_write_sendcmd`, `track_path`.
`focus_x` is removed (or kept as a thin `track_path`-based shim if other callers
appear). New tunables collected as named module constants near the existing
`ZOOM_*`/`SPLIT_TOP_FRAC` block: `SAMPLE_FPS`, `MINIMAP_FRAC`, `HUD_MASK_FRAC`,
`DEADZONE_FRAC`, `EASE_ALPHA`, `MAX_PAN_PX_PER_S`, `CENTER_SIGMA_FRAC`.

## Data flow

```
cut_clip
  └─ track_path(video, start, dur, src_w, src_h, crop_w, spike_frac)
       ├─ _motion_profile → (times, column-motion matrix)   [one gray decode]
       ├─ _aim per instant  → target_x[]   (center-bias + HUD/minimap mask)
       ├─ _ease             → xs[]          (deadzone + low-pass + vel-limit)
       └─ _write_sendcmd    → script.txt
     returns (x0, script_path)
  └─ build_vf(..., crop_x=x0, sendcmd=script_path)
       → "sendcmd=f=script,crop@dyn=...:x=x0,scale=W:H[,ass][,drawtext]"
  └─ ff(... -vf VF ...)     [one render pass; script deleted after]
```

## Error handling / fallbacks

- Analysis failure / <2 frames / zero motion → constant center path (today's
  behavior). Never crash a render on tracking.
- `sendcmd`-command unsupported on this ffmpeg → generated piecewise expression.
- Masks never remove all columns (guard: if masking zeroes the whole profile,
  drop the masks for that instant and fall back to center-biased only).

## Testing plan

- **Synthetic (repo style):** `testsrc`/moving-box source; assert (a) the emitted
  `sendcmd` x follows a box that moves left→right, (b) x holds flat when the box
  is centered (deadzone), (c) per-frame velocity ≤ `MAX_PAN_PX_PER_S`, (d) all x
  within `[0, src_w-crop_w]`, (e) a centered minimap-corner blob does **not** pull
  x into the corner.
- **Caption band:** render synthetic; assert caption y lands in the intended band
  for crop and zoom (no overlap with playfield center).
- **Real VOD:** the `--sample` set on a genuine LoL VOD, viewed by eye, to pick
  zoom and confirm the pan reads as smooth and champion-centered.
- **Regression:** `full`/`fit`/`split` layouts and `has_existing_captions` skip
  unchanged; `py_compile` clean.

## Out of scope / deferred

- Champion detection via health-bar/template matching (future, if center-bias
  proves insufficient on unlocked-camera footage).
- Vertical (y) tracking — tracked layouts keep full height / fixed bands, so only
  x moves.
- Any change to the uncommitted thumbnail WIP currently on `master`.
