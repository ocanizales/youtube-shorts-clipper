# Plan — Clipper viral upgrade

Executes `docs/superpowers/specs/2026-07-21-viral-upgrade-design.md`. Three
commits, each self-verifying (synthetic ffmpeg render + ffprobe / monkeypatched
whisper — the repo's existing verify style, no new framework). CPU x264 path on
this box (no NVENC); NVENC arg change is code-reviewed, not run.

## Commit 1 — Max video quality (spec Feature 2)
`clipper.py`, `serve.py`, `deploy/shorts-clipper.service`.
- Strengthen `_X264["max"]`: `("15","slower","48M","96M")` (was `slow/40M/80M`).
- NVENC: at `max` only, append `-multipass fullres -b_ref_mode middle` (cq 16).
- `cut_clip`: at `max` only, insert `unsharp=5:5:0.4:5:5:0.0` right before `_BT709`.
- Web/worker defaults to `max`: `os.environ.setdefault("SHORTS_QUALITY","max")` at
  the top of `serve.py` (before it spawns worker/app); `Environment=SHORTS_QUALITY=max`
  in the systemd unit. Code-level default stays `high` for ad-hoc CLI.
- **Verify:** render one synthetic clip at `SHORTS_QUALITY=max` → `ffprobe` shows
  `color_transfer=bt709`; assert the built `max` filter chain contains `unsharp`
  and `high` does not; assert `_X264["max"]` strengthened.

## Commit 2 — Punchier viral captions (spec Feature 3)
`clipper.py::make_dynamic_captions`.
- Displayed word text `.upper()` (ALL-CAPS).
- Style: Outline 4→6, Shadow 2→3.
- Active word: accent gold **+ pop** transform `{\fscx118\fscy118\t(0,100,\fscx100\fscy100)}`;
  already-revealed words plain white; cumulative reveal + silence-clear timing unchanged.
- Placement/anchor, end-timing, and `has_existing_captions` skip untouched.
- **Verify:** monkeypatch `clipper._WHISPER` with a fake returning canned words →
  call `make_dynamic_captions` → assert the `.ass` text is uppercase and every
  active-word line carries `\t(`; a silent tail (gap) still clears (no Dialogue
  lingering past speech).

## Commit 3 — One hero thumbnail per URL (spec Feature 1)
`clipper.py` (thumbs default, `make_hero_thumbnail`, `make_clips` returns
`(clips, hero)`, CLI call site), `web/db.py` (`jobs.thumb` col), `web/worker.py`
(unpack + store), `web/app.py::status` (return `thumb`), `web/templates/index.html`
(render hero at top of results).
- `cut_clip` `thumbs` default `True`→`False`; `make_clips` no longer passes per-clip thumbs.
- After clips are cut, `make_clips` builds ONE hero from the rank-0 hype moment via
  new `make_hero_thumbnail(video, moments, dims, title, transcript, out_path)`
  (reuses `_pick_frame` on the clean source + `_compose_thumb`/`_hook_text`;
  prefers a `detect_facecam` frame when present). Saves `clips/hero_<stem>.jpg`.
- `make_clips` returns `(clips, hero|None)`; both call sites unpack.
- DB: idempotent `ALTER TABLE jobs ADD COLUMN thumb TEXT` in `init_db`; `get_job`
  returns it; `update_job` already generic.
- `status` payload adds `thumb`; UI renders `<img src=/clips/<thumb>>` + download
  link above the clip list when present (alt text; no `#000` bg).
- **Verify:** `make_clips` on a synthetic spike video (max_clips=1, short) → exactly
  one `hero_*.jpg`, zero `*_thumb.jpg`; `/status` payload round-trips `thumb`.

## Docs
Update `HANDOFF.md` (dated entry) + regenerate `PROJECT_MAP.md`. Leave the
pre-existing uncommitted `PROJECT_MAP.md` WIP reconciled by the regenerate step.
Commit + push per repo rule is the user's call (surface, don't auto-push).
