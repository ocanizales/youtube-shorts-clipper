# Graph Report - youtube-shorts-clipper  (2026-07-29)

## Corpus Check
- 26 files · ~32,102 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 363 nodes · 574 edges · 24 communities (22 shown, 2 thin omitted)
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `82037a44`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- db.py
- test_hook.py
- track_path
- _compose_thumb
- build_vf
- cut_clip
- Design — Clipper viral upgrade (hero thumbnails, max quality, viral captions)
- test_endcard.py
- Path
- Sample Generation for Comparison Sets
- make_clips
- _pick_frame
- Design — Framing overhaul: eased tracking + lower-third captions + zoom sampling
- Selling the LoL Shorts Clipper
- PROJECT_MAP — youtube-shorts-clipper
- Local Development Server Launcher
- serve.py
- Global Constraints
- Affiliate strategy: the Pro Setup Index
- Plan — HPC hook overhaul (own the first five seconds)
- Plan — Clipper viral upgrade
- HANDOFF — youtube-shorts-clipper
- youtube-shorts-clipper
- clipper.py

## God Nodes (most connected - your core abstractions)
1. `cut_clip()` - 19 edges
2. `_compose_thumb()` - 15 edges
3. `connect()` - 15 edges
4. `PROJECT_MAP — youtube-shorts-clipper` - 15 edges
5. `build_vf()` - 14 edges
6. `track_path()` - 12 edges
7. `Global Constraints` - 11 edges
8. `Design — Framing overhaul: eased tracking + lower-third captions + zoom sampling` - 10 edges
9. `caption_anchor()` - 9 edges
10. `_teaser_window()` - 9 edges

## Surprising Connections (you probably didn't know these)
- `process()` --calls--> `download_video()`  [EXTRACTED]
  web/worker.py → clipper.py
- `test_refine_start_is_a_noop_on_flat_audio()` --calls--> `_refine_start()`  [EXTRACTED]
  tests/test_hook.py → clipper.py
- `test_refine_start_moves_to_the_liveliest_second()` --calls--> `_refine_start()`  [EXTRACTED]
  tests/test_hook.py → clipper.py
- `test_refine_start_never_runs_past_the_video()` --calls--> `_refine_start()`  [EXTRACTED]
  tests/test_hook.py → clipper.py
- `test_column_motion_masks_hud_strip()` --calls--> `_column_motion()`  [EXTRACTED]
  tests/test_tracking.py → clipper.py

## Import Cycles
- None detected.

## Communities (24 total, 2 thin omitted)

### Community 0 - "db.py"
Cohesion: 0.11
Nodes (42): Connection, get, post, Row, account(), clip(), current_user(), index() (+34 more)

### Community 1 - "test_hook.py"
Cohesion: 0.11
Nodes (26): Source window the cold-open flash is cut from: (t0, length). Ends TEASER_DUR-…, Pick the liveliest opening second near `nominal` (HPC's "H"). `start = peak -…, _refine_start(), _teaser_window(), HPC hook-overhaul unit tests. Run: .venv/bin/python tests/test_hook.py Covers…, A guard on the tuning itself, not the code: a cold open that runs long enough…, The loud second before the fight must not be allowed to drag the start so late…, test_refine_start_is_a_noop_on_flat_audio() (+18 more)

### Community 2 - "track_path"
Cohesion: 0.12
Nodes (24): _aim_targets(), _column_motion(), _ease(), _motion_profile(), Per-column motion over time, with distractor regions masked out. `frames` is…, Decode a downscaled gray clip; return (times, per-column motion over time). One…, Per frame, the left column of the width-`win` window with the most center-…, Turn a noisy per-sample target into a smooth crop-x path. Holds position while… (+16 more)

### Community 3 - "_compose_thumb"
Cohesion: 0.11
Nodes (18): _burst(), _compose_thumb(), _cover(), _emoji_img(), _paste_emoji(), _pick_emoji(), _radial(), A color emoji as an RGBA image ~px tall (None if the font is missing). (+10 more)

### Community 4 - "build_vf"
Cohesion: 0.07
Nodes (32): build_vf(), caption_anchor(), _esc(), _even(), full_video_height(), Recover a 16:9 action canvas from a rendered 9:16 clip frame. zoom clips have a…, Band plan for the 'zoom' layout (the Korean solo-queue Short look): [black…, Height (px) of the source frame when scaled to the full 1080 width. (+24 more)

### Community 5 - "cut_clip"
Cohesion: 0.20
Nodes (10): cut_clip(), detect_facecam(), has_existing_captions(), _hashtags(), _ollama_metadata(), Best-effort: find a streamer facecam box via face detection on sampled frames.…, True if the source already has captions, so we don't add a duplicate layer: 1)…, Title/description/tags for one clip via the local Ollama instance. Strictly… (+2 more)

### Community 6 - "Design — Clipper viral upgrade (hero thumbnails, max quality, viral captions)"
Cohesion: 0.09
Nodes (21): Acceptance, Acceptance, Acceptance, Current behaviour, Current behaviour, Current behaviour, Design — Clipper viral upgrade (hero thumbnails, max quality, viral captions), Feature 1 — One hero thumbnail per URL (+13 more)

### Community 7 - "test_endcard.py"
Cohesion: 0.26
Nodes (10): _band_luma(), Path, End-to-end: the affiliate end card appears only over the final seconds. Run:…, Mean brightness of the bottom ENDCARD_BAND strip of the frame at `t`., With a teaser prepended the card must still close the OUTPUT, not fire early at…, The flash must never carry the CTA., test_endcard_is_absent_from_the_teaser_branch(), test_endcard_lands_at_the_end_of_the_finished_short() (+2 more)

### Community 8 - "Path"
Cohesion: 0.23
Nodes (13): download_video(), _find_media(), main(), Path, yt-dlp is installed in this venv, but the venv's bin isn't on PATH when the…, Largest finished media file for this id (any container), ignoring partials., Download the best video+audio up to 1080p (any container), merged., Parse a sidecar .txt back into {'TITLE': ..., 'CAPTION': ..., 'TAGS': ...} so… (+5 more)

### Community 9 - "Sample Generation for Comparison Sets"
Cohesion: 0.31
Nodes (8): AsyncFunctionDef, FunctionDef, describe(), first_line(), main(), Path, Generate PROJECT_MAP.md — a deterministic, no-LLM, no-network code map. Part of…, signature()

### Community 10 - "make_clips"
Cohesion: 0.20
Nodes (11): _dims(), ff(), find_hype_moments(), make_clips(), make_sample(), Full local pipeline on a downloaded video. Shared by CLI + web. Returns…, Render one framing sample (no captions) with a fast encode. `eased` renders the…, Render a labeled framing comparison set to clips/samples/ so the user can pick… (+3 more)

### Community 12 - "_pick_frame"
Cohesion: 0.14
Nodes (16): _frame_at(), _frame_score(), _hook_text(), make_hero_thumbnail(), make_thumbnail(), _ollama_thumb_hook(), _pick_frame(), AI thumbnail for a freshly cut clip, taken from the CLEAN source video (no… (+8 more)

### Community 13 - "Design — Framing overhaul: eased tracking + lower-third captions + zoom sampling"
Cohesion: 0.12
Nodes (15): 1. Dynamic eased tracking — `focus_x` → `track_path()`, 2. Driving the moving crop — `build_vf` integration (`sendcmd`), 3. Caption placement rework — `caption_anchor`, 4. Zoom sampling harness — `--sample`, 5. Readability, Data flow, Design, Design — Framing overhaul: eased tracking + lower-third captions + zoom sampling (+7 more)

### Community 14 - "Selling the LoL Shorts Clipper"
Cohesion: 0.13
Nodes (13): 1. What you're selling, 2. Pricing (credit-based SaaS), 3. From local script to hosted SaaS, 4. Cost & margin reality check, 5. Legal / ToS (read before charging money), 6. Go-to-market (first 30 days), Selling the LoL Shorts Clipper, Command line (+5 more)

### Community 15 - "PROJECT_MAP — youtube-shorts-clipper"
Cohesion: 0.12
Nodes (15): clipper.py, Dependencies, PROJECT_MAP — youtube-shorts-clipper, scripts/build_memory.py, serve.py, tests/test_captions.py, tests/test_endcard.py, tests/test_hook.py (+7 more)

### Community 18 - "Global Constraints"
Cohesion: 0.14
Nodes (13): Framing Overhaul Implementation Plan, Global Constraints, Self-Review, Task 0: Isolate pre-existing thumbnail WIP, create working branch, Task 1: Framing tunables + `_column_motion`, Task 2: `_aim_targets` (center-biased sliding window), Task 3: `_ease` (deadzone + low-pass + velocity cap), Task 4: `_write_sendcmd` (densified command script) (+5 more)

### Community 19 - "Affiliate strategy: the Pro Setup Index"
Cohesion: 0.15
Nodes (12): Affiliate program selection, Affiliate strategy: the Pro Setup Index, Architecture, Attribution, Component 1 — Pro Setup Index (the owned asset), Component 2 — Clip-side end card, Component 3 — Comment workflow (semi-automated), Goal (+4 more)

### Community 20 - "Plan — HPC hook overhaul (own the first five seconds)"
Cohesion: 0.18
Nodes (10): 1. Cold-open teaser (the headline change), 2. Score the opening frame, 3. `clip_len` 45 → 30, 4. `peak_pos` 0.65 → 0.72 (trim the tail), Out of scope, Plan — HPC hook overhaul (own the first five seconds), Risk, Tasks (+2 more)

### Community 21 - "Plan — Clipper viral upgrade"
Cohesion: 0.33
Nodes (5): Commit 1 — Max video quality (spec Feature 2), Commit 2 — Punchier viral captions (spec Feature 3), Commit 3 — One hero thumbnail per URL (spec Feature 1), Docs, Plan — Clipper viral upgrade

### Community 22 - "HANDOFF — youtube-shorts-clipper"
Cohesion: 0.33
Nodes (5): Current state, Gotchas, HANDOFF — youtube-shorts-clipper, How to resume cold, Open defects (user has flagged these repeatedly — highest priority)

### Community 23 - "youtube-shorts-clipper"
Cohesion: 0.40
Nodes (4): Continuity protocol (do this first), Hard rules, Run / test, youtube-shorts-clipper

### Community 24 - "clipper.py"
Cohesion: 0.17
Nodes (11): _ass_ts(), _js_runtime_args(), make_dynamic_captions(), _pick_encoder(), LoL YouTube Shorts Clipper — turn a YouTube VOD into 9:16 highlight clips.…, Use NVIDIA NVENC if it actually works (much faster); else quality x264. Both…, # NOTE: `-sws_flags` used to live here and was a proven no-op (see SCALE_FLAGS)., YouTube signature solving needs a JS runtime + the yt-dlp-ejs solver (in… (+3 more)

## Knowledge Gaps
- **96 isolated node(s):** `setup.sh script`, `1. What you're selling`, `2. Pricing (credit-based SaaS)`, `3. From local script to hosted SaaS`, `4. Cost & margin reality check` (+91 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **2 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `build_vf()` connect `build_vf` to `clipper.py`, `make_clips`, `cut_clip`, `test_endcard.py`?**
  _High betweenness centrality (0.025) - this node is a cross-community bridge._
- **Why does `cut_clip()` connect `cut_clip` to `test_hook.py`, `track_path`, `build_vf`, `test_endcard.py`, `Path`, `make_clips`, `_pick_frame`, `clipper.py`?**
  _High betweenness centrality (0.020) - this node is a cross-community bridge._
- **Why does `_teaser_window()` connect `test_hook.py` to `clipper.py`, `cut_clip`, `test_endcard.py`?**
  _High betweenness centrality (0.015) - this node is a cross-community bridge._
- **What connects `setup.sh script`, `1. What you're selling`, `2. Pricing (credit-based SaaS)` to the rest of the system?**
  _96 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `db.py` be split into smaller, more focused modules?**
  _Cohesion score 0.10909090909090909 - nodes in this community are weakly interconnected._
- **Should `test_hook.py` be split into smaller, more focused modules?**
  _Cohesion score 0.10846560846560846 - nodes in this community are weakly interconnected._
- **Should `track_path` be split into smaller, more focused modules?**
  _Cohesion score 0.12333333333333334 - nodes in this community are weakly interconnected._