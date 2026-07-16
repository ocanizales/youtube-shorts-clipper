# HANDOFF — youtube-shorts-clipper
_Last updated: 2026-07-16 (yt-dlp resolution fix). Update me before every session end._

## Current state
- **2026-07-16:** fixed `FileNotFoundError: 'yt-dlp'` when launched from
  project-dashboard: clipper now resolves yt-dlp next to `sys.executable`
  (venv bin isn't on PATH when the venv python is invoked directly) and passes
  `--js-runtimes node:~/.local/bin/node`. Installed `yt-dlp-ejs` in the venv —
  without it YouTube signature solving fails and 1080p formats go missing
  (would violate the 1080p hard rule). Verified format list shows 1080p again.
- **2026-07-16 (later):** "full" layout now centers the video vertically in
  the 9:16 frame (was pinned to the top edge — user request); captions moved
  to sit under the centered video. Verified via synthetic testsrc render.
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
