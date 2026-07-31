# youtube-shorts-clipper

League-of-Legends YouTube Shorts auto-clipper: yt-dlp download → audio-spike
highlight detection → ffmpeg 9:16 cut with motion tracking and Whisper captions.
CLI (`clipper.py`) + Flask web app.

## Reference notes (read before touching captions or descriptions)
- `docs/reference/lol-database.md` — game, pro scene, T1 roster. The machine-
  readable half is `lol_kb.py`; **a roster change edits both.**
- `docs/reference/shorts-description-seo.md` — the description rules
  `build_description` implements.

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
- **Two framings only: `full` and `zoom` (stacked HUD)** — `clipper.LAYOUTS`.
  The user cut `crop` and `split` on 2026-07-31; `split` and its facecam
  detection are deleted outright, `crop`/`fit` survive as internal-only
  `build_vf` branches (`crop` is what `--sample` renders). Don't re-add either
  to `LAYOUTS`, or a layout dropdown, without being asked.
- **Korean speech is captioned in English** (Whisper's translate task), gated on
  `TRANSLATE_LANGS` + `LANG_MIN_PROB`. A source that already has burned-in
  captions still gets our layer when the speech is Korean — the "already
  captioned" skip is about duplicates, and a translation isn't one.
- **Never rewrite an ambiguous word into a proper noun.** `lol_kb` splits its
  repair table SAFE / CONTEXTUAL for this: "Korea", "career", "gang" and friends
  stay untouched because a caster genuinely says them. Mishears of those are
  prevented at decode time by `whisper_prompt()`, not patched after.
- **Hashtags go in the description, never the title**; the clip index is `[3]`,
  not `(#3)`, because YouTube parses `#` in a title as a hashtag.
- **Don't let the metadata model invent facts.** The prompt forbids years,
  scores, stages, and results that aren't in the commentary — a 3B model will
  otherwise date a clip to "the 2023 finals" on its own.
- Commit + push every meaningful change with well-commented code (standing rule).
- `BUSINESS.md` covers the sellable-product angle; unlimited promo code is "bullet".
