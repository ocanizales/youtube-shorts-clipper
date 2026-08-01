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
- **Four framings, no more: `full`, `whole`, `split`, `zoom`** — `clipper.LAYOUTS`
  is the single source of truth and everything else derives from it: `--layout`'s
  argparse choices, `web/app.py`'s POST validation, `web/templates/index.html`'s
  `<select>`, and the dashboard's `/clipper` page (stdlib-only, so it regex-reads
  the tuple out of `clipper.py` in `clipper_layouts()`). Offering a framing
  `LAYOUTS` doesn't contain is how every `crop`/`split` job died in argparse for
  a week. `crop`/`fit` stay internal-only `build_vf` branches (`crop` is what
  `--sample` renders); don't promote either without being asked.
- **`whole` is landscape and everything else is not.** `canvas(layout)` decides —
  16:9 1920x1080 for `whole`, 9:16 1080x1920 for the rest — and captions, the ASS
  `PlayRes`, the caption size and the end-card band are all sized against it. A
  9:16 number used on the `whole` canvas is a bug, not a rounding difference.
- **`whole` cuts ALL of the video, in order.** Consecutive `WHOLE_PART_LEN` (61s)
  parts titled `Part 1`..`Part N`, no highlight detection, no cold-open teaser
  (it would replay footage the next part is about to show), no 9:16 thumbnail. A
  trailing remainder under `WHOLE_TAIL_MIN` (15s) is merged into the last part —
  never dropped. `--max-clips`/`--clip-len` don't apply and say so.
- **`split` needs opencv.** It is the only thing that does. Without it
  `detect_facecam` finds nothing, the clip falls back to `full`, and the only
  hint is a printed line. `pip install opencv-python-headless`.
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
