# Graph Report - youtube-shorts-clipper  (2026-07-29)

## Corpus Check
- 25 files · ~29,959 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 345 nodes · 544 edges · 25 communities (22 shown, 3 thin omitted)
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `8f37d414`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- db.py
- test_hook.py
- Motion Tracking and Path Decoding
- clipper.py
- caption_anchor
- cut_clip
- Design — Clipper viral upgrade (hero thumbnails, max quality, viral captions)
- Video Downloading and Media Selection System
- Path
- Sample Generation for Comparison Sets
- make_clips
- Tracked Crop Following Moving Subject in Video Frames
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
- _js_runtime_args

## God Nodes (most connected - your core abstractions)
1. `cut_clip()` - 17 edges
2. `_compose_thumb()` - 15 edges
3. `connect()` - 15 edges
4. `PROJECT_MAP — youtube-shorts-clipper` - 14 edges
5. `track_path()` - 12 edges
6. `build_vf()` - 11 edges
7. `Global Constraints` - 11 edges
8. `Design — Framing overhaul: eased tracking + lower-third captions + zoom sampling` - 10 edges
9. `caption_anchor()` - 9 edges
10. `make_clips()` - 9 edges

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

## Communities (25 total, 3 thin omitted)

### Community 0 - "db.py"
Cohesion: 0.11
Nodes (42): Connection, get, post, Row, account(), clip(), current_user(), index() (+34 more)

### Community 1 - "test_hook.py"
Cohesion: 0.11
Nodes (23): build_vf(), _esc(), Source window the cold-open flash is cut from: (t0, length). Ends TEASER_DUR-…, Pick the liveliest opening second near `nominal` (HPC's "H"). `start = peak -…, Build the 9:16 reframing filter chain for one segment. `suffix` is appended to…, _refine_start(), _teaser_window(), HPC hook-overhaul unit tests. Run: .venv/bin/python tests/test_hook.py Covers… (+15 more)

### Community 2 - "Motion Tracking and Path Decoding"
Cohesion: 0.12
Nodes (24): _aim_targets(), _column_motion(), _ease(), _motion_profile(), Per-column motion over time, with distractor regions masked out. `frames` is…, Decode a downscaled gray clip; return (times, per-column motion over time). One…, Per frame, the left column of the width-`win` window with the most center-…, Turn a noisy per-sample target into a smooth crop-x path. Holds position while… (+16 more)

### Community 3 - "clipper.py"
Cohesion: 0.11
Nodes (23): _burst(), _compose_thumb(), _cover(), _emoji_img(), _paste_emoji(), _pick_emoji(), _pick_encoder(), _radial() (+15 more)

### Community 4 - "caption_anchor"
Cohesion: 0.18
Nodes (12): caption_anchor(), _even(), full_video_height(), Recover a 16:9 action canvas from a rendered 9:16 clip frame. zoom clips have a…, Band plan for the 'zoom' layout (the Korean solo-queue Short look): [black…, Height (px) of the source frame when scaled to the full 1080 width., Where captions belong for each layout, as (ASS alignment, margin px). Alignment…, _uncrop_916() (+4 more)

### Community 5 - "cut_clip"
Cohesion: 0.15
Nodes (13): _ass_ts(), cut_clip(), detect_facecam(), has_existing_captions(), _hashtags(), make_dynamic_captions(), _ollama_metadata(), Transcribe spoken words and write an .ass with word-by-word reveal where the… (+5 more)

### Community 6 - "Design — Clipper viral upgrade (hero thumbnails, max quality, viral captions)"
Cohesion: 0.09
Nodes (21): Acceptance, Acceptance, Acceptance, Current behaviour, Current behaviour, Current behaviour, Design — Clipper viral upgrade (hero thumbnails, max quality, viral captions), Feature 1 — One hero thumbnail per URL (+13 more)

### Community 7 - "Video Downloading and Media Selection System"
Cohesion: 0.29
Nodes (11): _cut(), _duration(), _luma_at(), Path, End-to-end: the cold-open teaser really is prepended, and really is the climax.…, Black everywhere except a bright window straddling the spike, plus audio. Audio…, Mean brightness of the output frame at `t` seconds., The teaser doubles the reframing graph inside one -filter_complex. The tracked… (+3 more)

### Community 8 - "Path"
Cohesion: 0.23
Nodes (13): download_video(), _find_media(), main(), Path, yt-dlp is installed in this venv, but the venv's bin isn't on PATH when the…, Largest finished media file for this id (any container), ignoring partials., Download the best video+audio up to 1080p (any container), merged., Parse a sidecar .txt back into {'TITLE': ..., 'CAPTION': ..., 'TAGS': ...} so… (+5 more)

### Community 9 - "Sample Generation for Comparison Sets"
Cohesion: 0.31
Nodes (8): AsyncFunctionDef, FunctionDef, describe(), first_line(), main(), Path, Generate PROJECT_MAP.md — a deterministic, no-LLM, no-network code map. Part of…, signature()

### Community 10 - "make_clips"
Cohesion: 0.20
Nodes (11): _dims(), ff(), find_hype_moments(), make_clips(), make_sample(), Full local pipeline on a downloaded video. Shared by CLI + web. Returns…, Render one framing sample (no captions) with a fast encode. `eased` renders the…, Render a labeled framing comparison set to clips/samples/ so the user can pick… (+3 more)

### Community 11 - "Tracked Crop Following Moving Subject in Video Frames"
Cohesion: 0.33
Nodes (8): _bright_frac(), Path, End-to-end: sendcmd-driven crop follows a moving subject. Run: .venv/bin/python…, A bright vertical bar sweeping across the frame on black. Built by overlaying a…, Fraction of output frames that still contain the bright subject., _render(), _render_source(), test_tracked_crop_follows_moving_bar()

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
Cohesion: 0.13
Nodes (14): clipper.py, Dependencies, PROJECT_MAP — youtube-shorts-clipper, scripts/build_memory.py, serve.py, tests/test_captions.py, tests/test_hook.py, tests/test_sample.py (+6 more)

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

## Knowledge Gaps
- **95 isolated node(s):** `setup.sh script`, `1. What you're selling`, `2. Pricing (credit-based SaaS)`, `3. From local script to hosted SaaS`, `4. Cost & margin reality check` (+90 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **3 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `build_vf()` connect `test_hook.py` to `clipper.py`, `caption_anchor`, `cut_clip`, `make_clips`, `Tracked Crop Following Moving Subject in Video Frames`?**
  _High betweenness centrality (0.014) - this node is a cross-community bridge._
- **Why does `_teaser_window()` connect `test_hook.py` to `clipper.py`, `cut_clip`, `Video Downloading and Media Selection System`?**
  _High betweenness centrality (0.013) - this node is a cross-community bridge._
- **Why does `track_path()` connect `Motion Tracking and Path Decoding` to `clipper.py`, `cut_clip`, `Path`, `make_clips`, `Tracked Crop Following Moving Subject in Video Frames`?**
  _High betweenness centrality (0.013) - this node is a cross-community bridge._
- **What connects `setup.sh script`, `1. What you're selling`, `2. Pricing (credit-based SaaS)` to the rest of the system?**
  _95 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `db.py` be split into smaller, more focused modules?**
  _Cohesion score 0.10909090909090909 - nodes in this community are weakly interconnected._
- **Should `test_hook.py` be split into smaller, more focused modules?**
  _Cohesion score 0.10869565217391304 - nodes in this community are weakly interconnected._
- **Should `Motion Tracking and Path Decoding` be split into smaller, more focused modules?**
  _Cohesion score 0.12333333333333334 - nodes in this community are weakly interconnected._