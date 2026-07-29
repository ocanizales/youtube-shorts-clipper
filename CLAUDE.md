# youtube-shorts-clipper

League-of-Legends YouTube Shorts auto-clipper: yt-dlp download → audio-spike
highlight detection → ffmpeg 9:16 cut with motion tracking, facecam
split-screen, and Whisper captions. CLI (`clipper.py`) + Flask web app.

## Continuity protocol (do this first)
1. Read `HANDOFF.md` — live work state, open defects, gotchas.
2. Read `PROJECT_MAP.md` — code map. Regenerate: `python scripts/build_memory.py`.
3. Ask `/graphify` before grepping — `graphify-out/GRAPH_REPORT.md` is a
   queryable code **and docs** graph (god nodes; `graphify affected "X"` gives
   blast radius). Refreshed by the 6h cron; rebuild now with `graphify update .`.
4. Before session end / when context fills: update `HANDOFF.md`, commit + push.

## Run / test
```powershell
python serve.py        # web app + worker -> http://localhost:5000
python clipper.py ...  # CLI path
```

## Hard rules
- Downloads must be **1080p always** (user caught 480p once — never regress).
- Uploads are staged as **hidden/draft** in YouTube Studio, never auto-published,
  and only the one authorized channel.
- Delete the full downloaded VOD at session end — don't let `downloads/` accumulate.
- `client_secret_*.json` (Google OAuth) is gitignored — keep it that way; the
  pre-push hook guards. Repo is private (`ocanizales/youtube-shorts-clipper`).
- Captions must never cover faces or key gameplay; no captions during silence.
- Commit + push every meaningful change with well-commented code (standing rule).
- `BUSINESS.md` covers the sellable-product angle; unlimited promo code is "bullet".
