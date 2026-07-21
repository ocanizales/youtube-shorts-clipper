# Design — Clipper viral upgrade (hero thumbnails, max quality, viral captions)

_Date: 2026-07-21 · Branch: `viral-upgrade` · Repo: youtube-shorts-clipper_

## Goal

Three standing changes to the clipper, requested as new defaults ("from now on"),
not opt-in flags:

1. **One hero thumbnail per URL**, generated automatically and displayed in the web UI.
2. **Higher output video quality** (slower renders accepted).
3. **Punchier, viral-style burned-in captions** per clip, tuned for retention.

Scope is the `youtube-shorts-clipper` repo only: `clipper.py` (CLI + shared
pipeline) and its Flask web app under `web/`. The separate project-dashboard
`/clipper` page is out of scope for this pass.

## Non-goals

- No per-clip thumbnails (explicitly dropped in favour of one hero per URL).
- No caption emoji (VPS font support is unreliable).
- No metadata/SEO changes (titles, tags, hashtags stay as-is).
- No changes to the download path, highlight detection, or layout geometry.
- No touching the project-dashboard integration in this pass.

---

## Feature 1 — One hero thumbnail per URL

### Current behaviour
`cut_clip(..., thumbs=True)` calls `make_thumbnail` for every clip, producing
`short_XX_<t>s_thumb.jpg` (5 per job). None of these are ever surfaced in the web
UI (`grep` for "thumb" in `web/` returns nothing).

### New behaviour
- Per-clip thumbnails are **no longer generated**. `cut_clip`'s `thumbs` default
  becomes `False`, and `make_clips` no longer requests per-clip thumbs.
- After all clips are cut, `make_clips` generates **exactly one** hero thumbnail
  for the whole source, from the single strongest hype moment
  (`find_hype_moments` returns moments ranked by audio energy; take rank 0).
  - Frame selection reuses `_pick_frame` on the CLEAN source (no burned
    captions) sampled around that peak.
  - Prefer a frame containing a detected face when `detect_facecam` finds one at
    that moment (more compelling thumbnail); otherwise use the plain best frame.
  - Compose with the hook text of the corresponding best clip via the existing
    `_compose_thumb` / `_hook_text`.
- Saved as `clips/hero_<source-stem>.jpg` (deterministic, one per source video).
- `make_thumbnail`, `_compose_thumb`, `_hook_text`, `rethumb_all` are retained
  and reused; a new thin helper `make_hero_thumbnail(video, moments, dims, title,
  transcript, out_path)` wraps frame-pick + compose for the job-level thumb.

### Plumbing
- `make_clips` return type changes from `list[Path]` to `(clips: list[Path],
  hero: Path | None)`. Two call sites updated:
  - `web/worker.py::process` — unpack, store `hero.name` in the job.
  - `clipper.py::main` (CLI) — unpack, ignore or print hero path.
- `web/db.py` — add a nullable `thumb` column to the `jobs` table (idempotent
  `ALTER TABLE ... ADD COLUMN` guarded in `init_db`), and include it in
  `get_job` / `update_job` handling.
- `web/worker.py` — on success: `db.update_job(jid, ..., thumb=hero.name if hero
  else None)`.
- `web/app.py::status` — return `thumb=job["thumb"]` in the JSON payload. The
  existing `/clips/<path:name>` route already serves `.jpg` from `CLIPS`.
- `web/templates/index.html::poll` — when `s.thumb` is present on `done`, render
  an `<img src="/clips/"+s.thumb>` prominently at the top of `#results` with a
  "Download thumbnail" link, before the per-clip video list. Alt text set for a11y.

### Acceptance
- A completed job produces exactly one `hero_*.jpg` in `clips/` and zero
  `*_thumb.jpg`.
- The hero image renders at the top of the results panel and downloads correctly.

---

## Feature 2 — Max video quality (slower renders accepted)

### Current behaviour
Encode tier via `SHORTS_QUALITY` env (default `high`). Tiers:
`fast`/`high`/`max` map to x264 (crf/preset/maxrate/bufsize) and NVENC cq.
Web/worker inherits the default `high` (crf 17, maxrate 24M).

### New behaviour
- **Web/worker renders default to the `max` tier.** Implemented by the worker/
  service running with `SHORTS_QUALITY=max` (set in `serve.py` and the systemd
  unit) so CLI previews can still choose `fast`/`high`. `high` remains the
  code-level default for ad-hoc CLI use.
- **Strengthen the `max` tier** in `clipper.py`:
  - x264: `crf 15`, preset `slow`→`slower`, maxrate `40M`→`48M`, bufsize
    `80M`→`96M`.
  - NVENC: add `-multipass fullres -b_ref_mode middle` (cq stays `16`).
- **Add a light `unsharp`** (`unsharp=5:5:0.4:5:5:0.0`) applied **only at the
  `max` tier**, inserted in `cut_clip` immediately before the `_BT709` tag in the
  filter chain. Rationale: the pipeline does large upscales onto the 1080x1920
  canvas and YouTube's re-encode softens edges; a mild unsharp preserves
  perceived HUD/text detail without visible haloing. Amount kept low to avoid
  ringing.
- 60fps is already preserved (no fps downscale in the chain) — no change needed.

### Trade-off
A 15s clip render goes from ~33s (current `high`) to noticeably slower at `max`
`slower` preset on the CPU path. This is explicitly accepted and conflicts with
open defect #2 (speed); `SHORTS_QUALITY=fast` remains the fast preview path.

### Acceptance
- Web renders run at the `max` tier by default (log line shows `quality=max`).
- Output is tagged `bt709` (transfer/primaries/matrix) and carries a higher
  bitrate than the previous `high` default on the same clip.
- The `max` filter graph contains `unsharp`; `high`/`fast` do not.

---

## Feature 3 — Punchier viral captions (per clip)

### Current behaviour
`make_dynamic_captions` writes an `.ass` with word-by-word cumulative reveal:
Arial bold, `fontsize` from caller, active spoken word coloured gold
(`&H00D4FF`), Outline 4 / Shadow 2. Placement decided by `caption_anchor`
(per layout). Words clear ~0.3–0.5s after they are spoken (silence-safe).

### New behaviour (MrBeast/Hormozi style)
- **ALL-CAPS** word text (`.upper()`), applied to displayed text only.
- **Thicker outline + stronger shadow**: Outline 4→6, Shadow 2→3, so big caps
  stay legible over busy gameplay.
- **Pop animation** on the active word: ASS transform
  `{\fscx118\fscy118\t(0,100,\fscx100\fscy100)}` — a brief ~100ms scale-down
  from 118% to 100%. Pop magnitude kept modest so the caption never spills past
  its reserved band.
- **Active-word emphasis**: active spoken word rendered in the accent gold + pop;
  already-revealed words in the same phrase stay plain white (cumulative reveal
  preserved).
- No emoji.

### Invariants preserved (open defect #3)
- Placement/anchor logic (`caption_anchor` per layout) is unchanged — captions
  stay in their reserved zone and do not cover faces/gameplay.
- The end-timing logic that clears captions during silence is unchanged.
- `has_existing_captions` skip path is unchanged (no duplicate caption layer).

### Acceptance
- Generated `.ass` contains uppercased text and a `\t(` scale transform on active
  words.
- A synthetic render with speech shows animated caps captions; a silent stretch
  shows no caption.

---

## Standing behaviour

All three land as **defaults**, not flags:
- Hero thumbnail always generated per job; per-clip thumbs never generated.
- Web/worker always renders at `max`.
- Captions always use the viral style.

Recorded in `HANDOFF.md` (new dated entry) and `PROJECT_MAP.md` regenerated via
`scripts/build_memory.py`.

## Testing / verification

Follow the repo's existing synthetic-render verify pattern (testsrc + ffprobe),
no new framework:

1. **Hero thumb:** run `make_clips` on a short synthetic video with an audio
   spike → assert exactly one `hero_*.jpg` exists and zero `*_thumb.jpg`.
2. **Captions:** assert the emitted `.ass` contains uppercase text and `\t(`; a
   silent tail produces no `Dialogue` line.
3. **Quality:** render one clip at `SHORTS_QUALITY=max`; `ffprobe` confirms
   `color_transfer=bt709` and bitrate above the `high` baseline; assert the
   built ffmpeg command for `max` includes `unsharp` and the strengthened encoder
   args, and that `high` omits `unsharp`.
4. **Web:** unit-check that `/status` payload includes `thumb`, and that a job
   row round-trips the new `thumb` column.

## Files touched

- `clipper.py` — encode tiers (`_X264`, `_NVENC_CQ`, `_pick_encoder`),
  `cut_clip` (thumbs default, unsharp at max), `make_dynamic_captions` (viral
  style), `make_clips` (hero thumb + return tuple), new `make_hero_thumbnail`,
  CLI `main` call site.
- `web/db.py` — `jobs.thumb` column + accessors.
- `web/worker.py` — unpack `(clips, hero)`, store `thumb`.
- `web/app.py` — `status` returns `thumb`.
- `web/templates/index.html` — render hero thumbnail in results.
- `serve.py` + `deploy/shorts-clipper.service` — `SHORTS_QUALITY=max`.
- `HANDOFF.md`, `PROJECT_MAP.md` — docs.
