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
  and only the one authorized channel. **Two independent paths now:**
  `clipper.py --draft` (CLI, `InstalledAppFlow` + `token.pickle`, needs a
  *Desktop* OAuth client) and the web app's **Connect YouTube** button
  (`web/youtube.py`, server-side `Flow` + creds in SQLite, needs a *Web
  application* client + a registered redirect URI). Same filename, different
  client types — see SETUP.md. Connecting one does not connect the other.
- **`privacyStatus: private` is hardcoded and takes no argument.** Neither
  `upload_draft` may grow a privacy parameter: making it callable would put
  "publish publicly" one wrong argument away, and a test asserts the signature
  stays closed. Nothing auto-publishes, ever.
- **`web/youtube.py` credentials are live channel access.** `youtube_accounts.creds`
  holds a refresh token — anyone who can read `data.db` can post to that channel.
  `data.db`, `client_secrets.json` and `.flask_secret` are all gitignored; keep
  it that way. (CLAUDE.md previously claimed a pre-push hook guards this — there
  is no `.git/hooks/pre-push` on this box, so the ignore list is the only guard.)
- **Never move credentials through GitHub's web upload UI — it does not read
  `.gitignore`.** On 2026-08-05 `client_secret_*.json` was uploaded that way and
  landed on `master` as `d937bfa` despite matching two ignore rules; the ignore
  list only applies to `git add` in a clone. Force-pushed away and the secret
  rotated. Note what the force-push did *not* do: GitHub kept serving the
  orphaned commit by SHA afterwards, so **rotation is the fix and history
  rewriting is only hygiene**. Get secrets here with `scp`, or paste into a
  heredoc over your own SSH session — never through a git remote.
- The web app needs `flask` (`web/requirements.txt`); it was missing from `.venv`
  until 2026-08-05. `.venv/bin/pip install -r web/requirements.txt`.
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
- **`split` needs opencv, and it must be 4.x.** It is the only thing that does.
  Without it `detect_facecam` finds nothing, the clip falls back to `full`, and
  the only hint is a printed line. `pip install "opencv-python-headless<5"` —
  **the pin is not cosmetic.** 5.x dropped top-level `cv2.CascadeClassifier` and
  ships no Haar cascades, so the `import cv2` guard passes and the *next* line
  raises `AttributeError`: a mid-render crash where the missing-opencv path
  would have degraded quietly. Installed 2026-08-01 at 4.14.0.94.
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
  otherwise date a clip to "the 2023 finals" on its own. **Names are checked, not
  just forbidden:** `_ollama_metadata` runs `lol_kb.ungrounded_names` +
  `ungrounded_claims` over the title, hook and description, retries once naming
  what was invented, then falls back to the hook title. Prompting alone was not
  enough — it lowers the rate and the residue is silent and confident.
- **An empty briefing means name nobody.** When `context_brief` comes back empty
  the prompt must tell the model to title the *play*, never a player or team.
  The old prompt still said "lead with the player or team name" with nothing to
  lead from, and a 3B model fills that gap with the two names it knows: T1 and
  Faker. That was the wrong-team bug, not a model defect.
- **Non-T1 players carry no team in `lol_kb.PLAYERS`, deliberately.** A player
  implies their team in `detect_entities`, so a stale roster entry does not sit
  quietly — it manufactures a false attribution and feeds it to the model as
  briefing fact. Record the role (durable); let the commentary supply the team
  (volatile). T1 is the exception because the user maintains it.
- **An IGN that is also an ordinary English word needs two signals.**
  `AMBIGUOUS_IGNS` / `AMBIGUOUS_TEAM_FORMS` require League context *and* the
  original capitalisation. Without it, "what a bang, the wolf pack collapses"
  detected Bang + Wolf and attributed the clip to T1. Same reasoning as the
  SAFE/CONTEXTUAL split on captions — it was just never applied to detection.
- Commit + push every meaningful change with well-commented code (standing rule).
- `BUSINESS.md` covers the sellable-product angle. **The owner's unlimited promo
  code is deliberately not written down here** — this repo went public on
  2026-08-05 and a whitelist code in a public file is a free-access coupon for
  anyone who reads it. Seed codes at runtime (`db.create_promo(...)`) and keep
  the values in `data.db`, which is gitignored.
