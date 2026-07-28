# HANDOFF — youtube-shorts-clipper
_Last updated: 2026-07-24 (framing overhaul). Update me before every session end._

## Current state
- **2026-07-28 (HPC hook overhaul, branch `hpc-hook` off `framing-overhaul`):**
  clips opened on their least interesting second. Root cause: `find_hype_moments`
  sets `start = peak − clip_len × peak_pos`, so the first 5s were a pure
  **arithmetic offset, never scored** — in a pro VOD that's farming/warding/
  walking. Climax placement was already fine (`peak_pos`); the **hook was
  unowned**. Four changes, from the HPC formula (Hook/Progression/Climax):
  - **Cold-open teaser.** A ~1.8s flash of the moment before the spike, hard-cut
    back to the build-up. `_teaser_window(start, dur, peak_pos)` → the flash ends
    **0.4s after** the spike (`TEASER_LEAD`=1.4 < `TEASER_DUR`=1.8), so it shows
    the engage and the caster *starting* to yell but never the outcome — that
    shortness is why a cold open doesn't break "never pay off early", and
    `test_teaser_is_short_enough_not_to_resolve` guards the tuning itself.
    Rendered as a **second `-i` on the same ffmpeg call** and concatenated in the
    graph, NOT as a separate file: one `loudnorm` (`_LOUDNORM`) then covers both
    segments — normalizing 1.8s of the loudest audio alone would flatten exactly
    the punch the teaser exists to deliver — and there's no concat-seam A/V drift.
    Captions/headline and the `sendcmd` pan stay on the **main branch before the
    concat**, so `.ass` timings need no shifting. Tracked layouts re-run
    `track_path` over the teaser window so the flash frames the *fight*, not the
    walk-in. Grade + BT.709 (`tail`) moved to after the concat so the two
    segments can never be tagged differently. Off with `--no-teaser`.
  - **`build_vf(..., suffix="")`.** Two copies of the reframing graph in one
    `-filter_complex` collided on hard-coded link labels (`[a][b]`, `[bg][fg]`,
    `[pf][hs]`, `[cam][game]`) **and** on the named `crop@dyn` instance that
    `_write_sendcmd` addresses. `suffix` renames both; the teaser uses `_t`,
    production keeps `""`. **`suffix=""` is byte-identical to the pre-change
    output** — that golden test is the regression guard for the framing work.
  - **`_refine_start`** — scored opening second. Scans ±`HOOK_SEARCH` (4s) of the
    per-second energy array `find_hype_moments` already has (no extra decode) and
    takes the liveliest second, constrained to keep the spike in
    `[HOOK_PEAK_MIN, HOOK_PEAK_MAX]` = 0.55–0.90 of the clip. Ties break toward
    nominal — no movement without a reason.
  - **Defaults: `clip_len` 45 → 30, `peak_pos` 0.65 → 0.72.** 29s of build-up was
    a long ask and ~35% of every clip ran *after* the peak. New shape: 1.8s
    teaser + 21.6s build + 8.4s payoff. Changed in **three places that must
    agree**: `clipper.py` (argparse + `make_clips`/`cut_clip`), `web/app.py`,
    `web/templates/index.html` (+ a "Cold open" checkbox; the JS sets `teaser`
    explicitly because an unchecked box is simply absent from FormData).
  - **Tests:** `tests/test_hook.py` (11 unit: golden byte-identity for all five
    layouts, label/instance collision, refine-start band + bounds + no-op,
    teaser-window clamps), `tests/test_teaser_render.py` (end-to-end on a source
    that is black except around the spike — so a clip that merely got longer, or
    flashed the wrong moment, cannot pass; plus a render of all four layouts to
    guard the doubled graph). Whole suite green.
  - **Not done on purpose:** `PROJECT_MAP.md` NOT regenerated — it has
    uncommitted WIP in the working tree and `scripts/build_memory.py` would
    clobber it. Run it once that WIP lands. Plan:
    `docs/superpowers/plans/2026-07-28-hpc-hook.md`.
  - **Still open from the previous branch:** the zoom-level pick from
    `clips/samples/*.mp4` is untouched here, and `framing-overhaul` is still
    unmerged. **Nothing pushed.**
- **2026-07-24 (framing overhaul, branch `framing-overhaul`):** the 9:16 reframing
  "looked at no one" and felt sluggish, captions read as far from the action.
  Root cause: `focus_x()` returned **one** static crop-x for the whole clip, scored
  by raw pixel-motion (minions/particles/**minimap** attract it, not the champion).
  - **Static `focus_x` → eased `track_path()`.** Same single downscaled-gray decode,
    but the time axis is kept: a per-column motion profile over time →
    center-biased sliding-window aim (a broad Gaussian, σ=`CENTER_SIGMA_FRAC`,
    exploits that the game camera already centers the champion) with the
    **minimap corner** and **bottom HUD strip** masked out (`MINIMAP_FRAC`,
    `HUD_MASK_FRAC`) → an eased path (deadzone hold `DEADZONE_FRAC` + exponential
    low-pass `EASE_ALPHA` + velocity cap `MAX_PAN_PX_PER_S`) → an ffmpeg **`sendcmd`
    script** that drives a named **`crop@dyn`** filter's `x` live (densified to
    `CMD_FPS` — sendcmd is stepwise, not interpolated, so we interpolate in Python).
    Returns `(x0, script_path)`; `script_path is None` = constant path (short/blank
    clip or action never leaves the deadzone) → caller renders a plain static crop.
    Decomposed helpers: `_motion_profile` / `_column_motion` / `_aim_targets` /
    `_ease` / `_write_sendcmd` / `track_path`. Applies to **both** tracked layouts
    (`crop` and `zoom`, the latter with its narrower `zoom_geometry` window).
    `build_vf` gained `sendcmd=None`; `cut_clip` calls `track_path`, threads the
    script, and unlinks it after render (like the `.ass` cleanup).
  - **Captions → lower-third safe band, bigger.** `caption_anchor` now returns
    bottom-anchored (an=2) lower-third margins for `crop` (`H*0.16`) and `zoom`
    (just above the HUD strip, `hud_out_h + H*0.03`) — zoom was in the **top black
    bar**, physically far from the action. `cut_clip` bumps tracked-layout caption
    size to `CAP_SIZE_TRACKED` (84). `split`/`full`/`fit` anchors unchanged; the
    HUD is not "key gameplay" so captioning over it honors the hard rule.
  - **`--sample` zoom harness.** `python clipper.py --sample <URL_or_FILE> [--at S]`
    renders a labeled comparison set to `clips/samples/` — **static vs eased** across
    `SAMPLE_ZOOMS` (1.0/1.25/1.5× punch-in), `SAMPLE_DUR`=12s each, so the user picks
    the zoom by eye. `build_vf`'s crop branch gained an optional `crop_w` that keeps
    9:16 and re-centers vertically (a real punch-in, not a horizontal stretch);
    `crop_w=None` is byte-identical to the old production crop (no regression).
    `make_sample`/`_render_sample` do the work.
  - **Tests** (repo standalone-script convention, no pytest):
    `.venv/bin/python tests/test_tracking.py` (10 unit tests: masks, aim center-bias,
    ease deadzone/velocity/clamp, sendcmd densify+ramp, track_path fallback/script),
    `tests/test_tracking_render.py` (end-to-end: sendcmd pan keeps a moving subject
    on screen AND beats a static crop — differential, so a frozen crop can't pass),
    `tests/test_captions.py`, `tests/test_sample.py`. All green; `import clipper` ok.
  - **Awaiting user:** pick a zoom level from the delivered `clips/samples/*.mp4`,
    then bake the winner as the crop default. **Not pushed / not merged to master.**
    Design + plan under `docs/superpowers/{specs,plans}/2026-07-23-*`.
- **2026-07-22 (creative vertical thumbnails):** thumbnails were landscape
  1280x720 screenshots with a text overlay; user wanted them "creative, in short
  format, actual edited pictures." `_compose_thumb` rewritten to build a designed
  **vertical 9:16 (1080x1920)** composite from the clean landscape source frame:
  ambient blurred+dimmed backdrop, warm spotlight + gold light-ray burst, the
  graded/punched-in frame as a white-bordered, glowing, drop-shadowed, tilted
  **sticker card**, a themed **color emoji badge** (🔥/💥/⚔️/🐉, picked from the
  hook via `_pick_emoji`; NotoColorEmoji `embedded_color=True`, degrades to none
  if missing), a big wrapped ALL-CAPS hook (gold accent word + underline) over a
  bottom scrim, then film grain + vignette. Per-clip variation (tilt/ray-phase/
  grain) is md5-seeded from the output name so a batch never looks copy-pasted.
  New PIL/numpy helpers: `_cover` `_rounded_mask` `_radial` `_wrap_hook` `_burst`
  `_pick_emoji`/`_emoji_img`/`_paste_emoji`. Hero card is clamped to landscape
  (crop to 16:9 when the source frame is tall) so a portrait input can't blow it
  up. `THUMB_W,THUMB_H` now `1080,1920`. Verified: `--rethumb` on the 3 live clips
  + a direct `make_thumbnail` call, each 1080x1920 ~0.6 MB, viewed by eye.
  - **Re-enabled PER-CLIP thumbnails.** The viral upgrade had switched to ONE
    `hero_<source>.jpg` per URL and `cut_clip(thumbs=False)`; but the
    project-dashboard `/clipper` lists every clip as its own downloadable Short and
    keys on `<stem>_thumb.jpg`, so fresh renders showed NO thumbnail there.
    `make_clips` now passes `thumbs=thumbs` to `cut_clip` (each clip gets its own
    9:16 cover) **and** still writes the hero (the Flask `web/app.py` uses it);
    `--no-thumbs` disables both. This reverses the "per-clip thumbs dropped" default
    — flagged for the user in case the hero-only design was intentional.
  - Dashboard side (project-dashboard `app.py`, separate repo): `/clipper` now
    serves `.jpg` from `/clips/` (guard + content-type), lists each clip's `thumb`,
    and uses it as the `<video poster>` (both 9:16) plus a "⬇ Thumbnail" button.
- **2026-07-21 (viral upgrade, branch `viral-upgrade`):** three standing
  defaults, each verified with the repo's synthetic-render style.
  - **One hero thumbnail per URL** (not per clip). Per-clip `*_thumb.jpg` are gone
    (`cut_clip(thumbs=False)` default). `make_clips` now returns `(clips, hero)`
    and, after cutting, builds ONE `clips/hero_<source>.jpg` via new
    `make_hero_thumbnail(...)`: best-scoring frame across every hype moment's peak
    off the CLEAN source, composed with the same grade/hook as before. **Note:** it
    picks by frame score across moments, not raw audio rank-0 — `find_hype_moments`
    returns moments *time-sorted*, so "rank 0" would be the earliest clip, not the
    strongest; visual score better predicts a good thumbnail. Surfaced in the web
    UI: `jobs.thumb` column (idempotent ALTER), `/status` returns `thumb`, and the
    poll renders it prominently above the clip grid (`.hero`, download link).
  - **Max quality by default for web renders.** `serve.py` + systemd set
    `SHORTS_QUALITY=max`; CLI default stays `high`. `max` tier strengthened:
    x264 crf15 / preset **slower** / 48M / 96M; NVENC adds
    `-multipass fullres -b_ref_mode middle`; plus a mild `unsharp=5:5:0.4:5:5:0.0`
    inserted before the BT709 tag **only** at max. Verified: bt709 tags on a real
    render, `unsharp` present at max and absent at high.
  - **Viral captions** (MrBeast/Hormozi): ALL-CAPS displayed text, Outline 4→6 /
    Shadow 2→3, and a ~100ms 118%→100% **pop** + gold accent on the active spoken
    word; revealed words stay plain white. Placement/anchor, silence-clear timing,
    and the `has_existing_captions` skip are unchanged.
  - **Not yet pushed / not merged to master** — awaiting user. Flask isn't
    installed in this box's venv, so the web app couldn't be run here; web edits
    were verified by `py_compile` + `node --check` + DB round-trip, not a live boot.

- **2026-07-20 (upload quality):** clips were going out at ~5.8 Mbps, which
  YouTube's own re-encode then crushed further. Fixed end to end:
  - Source: `FMT_SORT` now prefers **VP9** over h264 at 1080p (more detail at
    YouTube's serving bitrate); merge container is mkv so VP9+opus is remuxed
    losslessly. AV1 deliberately not preferred — too slow to decode on the VPS.
  - Encode: `SHORTS_QUALITY` env tier (`fast`/`high`/`max`, default **high**) =
    x264 crf 17 / preset medium / capped VBR 24M, NVENC p7 cq 19 where a GPU
    exists. Measured: 5.8 → **9.3 Mbps** on a 1080x1920@60 clip.
  - Scaling: `-sws_flags lanczos+accurate_rnd+full_chroma_int` (the pipeline does
    big upscales onto the 1080x1920 canvas; default bicubic softened HUD text).
  - Color: chain now ends in `setparams=...bt709...` so clips are tagged Rec.709.
    Untagged h264 gets guessed as BT.601 by YouTube → the washed-out look.
    **Gotcha:** `-color_primaries`/`-color_trc` do *nothing* on this ffmpeg build
    (8.0.1, verified transfer=unknown in the output) — the filter is required.
  - Cost: render is slower than the old `veryfast` (a 15s clip = ~33s wall on the
    8-core VPS, ~2.2x realtime). This trades against open defect #2 (speed) —
    `SHORTS_QUALITY=fast` restores the old fast path for previews.
- **2026-07-16 (zoom layout):** new `--layout zoom`, the Korean solo-queue Short
  look the user referenced (youtube.com/shorts/l6lhaJ5Sh4Q): black caption bar on
  top (captions render inside it), ~1.8x motion-tracked punch-in of the playfield
  (source minus its HUD) filling the middle edge-to-edge (no blur), and the
  source's own HUD strip rescaled full-width as a bottom band. Band plan lives in
  `zoom_geometry()` (knobs: `ZOOM_TOP_FRAC`, `ZOOM_HUD_FRAC`); `focus_x` is passed
  zoom's narrower window so tracking punches toward the fight. Verified with a
  synthetic magenta-HUD testsrc (bands pixel-exact: 202/1602/116 at 1080x1920) and
  a real 2x30s VOD render. **Gotcha found:** project-dashboard's `/api/clips/new`
  has a server-side layout allow-list that silently coerces unknown layouts to
  "full" — the first "zoom" render actually rendered full until app.py's tuple
  (line ~1610) gained "zoom"; dashboard dropdown now has "Zoom · stacked HUD".
- **2026-07-16:** fixed `FileNotFoundError: 'yt-dlp'` when launched from
  project-dashboard: clipper now resolves yt-dlp next to `sys.executable`
  (venv bin isn't on PATH when the venv python is invoked directly) and passes
  `--js-runtimes node:~/.local/bin/node`. Installed `yt-dlp-ejs` in the venv —
  without it YouTube signature solving fails and 1080p formats go missing
  (would violate the 1080p hard rule). Verified format list shows 1080p again.
- **2026-07-16 (later):** "full" layout now centers the video vertically in
  the 9:16 frame (was pinned to the top edge — user request); captions moved
  to sit under the centered video. Verified via synthetic testsrc render.
- **2026-07-16 (MoneyPrinter integration):** per-clip AI metadata — the Whisper
  transcript is fed to local Ollama (llama3.2:3b, pulled for this; ~10-16s/clip)
  to produce title/description/tags. Sidecar .txt gained a TAGS section;
  `--draft` uploads now reuse sidecar metadata; `--no-ai-meta` opts out and
  everything degrades to hook titles when Ollama is down. Dashboard got a
  cancel button + restart-safe job journal (instance/clip_jobs.json).
- **2026-07-05:** added `deploy/` (Linux `setup.sh` + optional
  `shorts-clipper.service`) for Ubuntu VPS deployment via
  `ocanizales/vps-setup`. Uploads need `client_secret_*.json` scp'd by hand.
- Working tree clean. Last commit: full-video layout, layout-aware captions,
  and per-platform metadata (2026-06-27).
- Web app (Flask :5000 via `serve.py`, which also spawns `web/worker.py`).

## Open defects (user has flagged these repeatedly — highest priority)
1. **Zoom/motion tracking quality** — "does not track anything" (flagged twice).
2. **Speed** — a clip render took ~8 minutes; user wants it much faster.
3. Caption placement — must never cover faces/gameplay; no hanging captions in
   silence (rules exist, verify they hold on the full-video layout).
4. UI polish — user called the site "very AI"-looking; one redesign happened,
   sensibility bar remains high.

## Gotchas
- Whisper via HuggingFace unauthenticated hits rate-limit warnings.
- Old clips/downloads must be cleaned per session (there's history of the full
  VOD re-appearing and stale clips accumulating).
- The project was moved here from `C:\Users\Bullet\Videos\.YOUTUBE_SHORTS` —
  old Claude session history lives under that path's transcript folder.

## How to resume cold
Read CLAUDE.md, then this file, then PROJECT_MAP.md.
