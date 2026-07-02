# HANDOFF — youtube-shorts-clipper
_Last updated: 2026-07-02 (continuity-protocol setup). Update me before every session end._

## Current state
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
