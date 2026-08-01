# LoL YouTube Shorts Clipper

Turn a YouTube VOD into 9:16 Shorts-ready highlight clips by finding the most
exciting moments (audio spikes = teamfights, pentakills). **Clips are saved
locally by default — nothing is uploaded unless you explicitly ask.**

Two ways to use it:
- **Command line** — `clipper.py` (fast, scriptable, your own channel)
- **Web app** — drag-drop a file or paste a URL in your browser (`web/app.py`)

See [BUSINESS.md](BUSINESS.md) for the plan to sell the web app online.

## One-time setup

```
pip install -r requirements.txt          # core
pip install -r web/requirements.txt      # only if you want the web app
```
ffmpeg is already installed via `winget install Gyan.FFmpeg`; the script
auto-finds it. Spoken-word subtitles are optional: `pip install faster-whisper`.

## Command line

```
python clipper.py "https://youtu.be/VIDEO_ID"
```
Clips land in `clips/`. Then drag your favorites into YouTube Studio.

**Options:**
| Flag | Default | What it does |
|------|---------|--------------|
| `--max-clips N` | 5 | how many clips to produce (not used by `--layout whole`) |
| `--clip-len SEC` | 30 | length of each clip (not used by `--layout whole`) |
| `--peak-pos FRAC` | 0.72 | spike position 0–1; higher = longer build-up |
| `--layout full\|whole\|split\|zoom` | full | **full** = whole video centered with a blurred fill, captions under it; **whole** = the ENTIRE video re-cut into consecutive 61s **landscape 16:9** parts (see below); **split** = streamer facecam on top, tracked gameplay below (needs opencv, falls back to `full` when no face is found); **zoom** = punched-in playfield that **tracks the action**, game HUD re-stacked at the bottom |
| `--caption "TEXT"` | – | burns a headline onto every clip |
| `--cap-size N` | 66 | caption font size (tracked layouts bump it to 84) |
| `--subtitles` | off | auto-caption spoken words (needs faster-whisper) |
| `--no-translate` | off | caption foreign speech verbatim; by default **Korean speech is captioned in English** |
| `--endcard "TEXT"` | – | affiliate CTA burned over the final 1.5s |
| `--no-teaser` | off | skip the cold-open flash of the moment before the spike |
| `--no-ai-meta` | off | skip Ollama title/description generation |
| `--no-thumbs` | off | skip the hero thumbnail |
| `--rethumb` | – | regenerate thumbnails for existing clips, then exit |
| `--sample URL_OR_FILE` | – | render a framing/zoom comparison set, then exit |
| `--draft` | off | ALSO upload clips as PRIVATE drafts to your channel |
| `--list-channels` | – | show which channel `--draft` would use, then exit |

**Examples:**
```
python clipper.py "URL" --max-clips 8 --clip-len 50 --layout zoom
python clipper.py "URL" --layout whole              # the whole VOD as 61s 16:9 parts
python clipper.py "URL" --caption "INSANE PENTAKILL" --peak-pos 0.7
python clipper.py "URL" --subtitles                 # LCK VOD -> English captions
python clipper.py "URL" --subtitles --no-translate  # keep the original language
```

**Whole-video mode (`--layout whole`).** The only framing that is not a 9:16
Short and not a highlight picker. It cuts the entire source into consecutive
**61-second** parts, chronologically — Part 1 is 0:00–1:01, Part 2 is 1:01–2:02,
on to the end — and renders them **landscape 16:9** (1920x1080). Every frame of
the source ends up in exactly one part; nothing is selected and nothing is
dropped. The sidecar titles are literally `Part 1` … `Part N`.

- A trailing remainder shorter than 15s is **merged into the last part** rather
  than shipped as its own (a 3-second "Part 8" is an accident, not a video), so
  the fattest possible part is 75s — still inside YouTube's 3-minute Shorts
  ceiling. A remainder of 15s or more becomes its own final part.
- `--max-clips` and `--clip-len` do not apply: the length is fixed and the count
  is whatever the duration divides into. Pass them anyway and the run says which
  ones it is ignoring.
- No cold-open teaser (it would replay footage the next seconds show in order)
  and no per-part thumbnail (the cover composer makes 1080x1920 Shorts covers).
- A long VOD makes a lot of parts and takes a long time: a 3-hour source is ~177
  parts.

**Facecam mode (`--layout split`).** Streamer webcam on the top 42% of the
canvas, gameplay below it, panning with the same tracker `zoom` uses. Detection
is best-effort and needs opencv (`pip install opencv-python-headless`); when no
face is found consistently, the clip renders as `full` and says so. A wrong
facecam box is far worse than none, so the detector is deliberately shy.

**Captions on Korean sources.** With `--subtitles`, speech detected as Korean is
decoded through Whisper's translate task, so the burned-in captions come out in
English. Esports proper nouns are protected twice: the decoder is biased toward
the real roster and vocabulary, and a repair pass fixes what still slips through
(`lol_kb.py`, `docs/reference/lol-database.md`). A Korean broadcast that already
carries burned-in Korean text still gets our English layer — the
"already captioned, skip" rule is about duplicates, and a translation isn't one.

## Web app

```
python serve.py
```
Open <http://localhost:5000>. `serve.py` starts both the web server and the job
worker. Sign in with an email (creates an account), then drag-drop a video or
paste a YouTube URL, pick options, watch the progress bar, and download each clip.

Architecture (see also [BUSINESS.md](BUSINESS.md)):
- `web/app.py` (Flask) is stateless: it enqueues jobs into SQLite (`data.db`).
- `web/worker.py` is a separate process that does the encoding and writes status
  back. Run more workers to scale. Atomic `claim_next_job` prevents double-claims.
- `web/db.py` holds all durable state: users, plans, promo codes, usage, jobs.

Accounts, plans, and codes:
- A "video dissection" = one processed source video. Monthly caps: free = 3,
  basic = 30, whitelisted = unlimited.
- Promo codes (seed them in a Python shell): `db.create_promo("FRIEND","whitelist")`
  for unlimited friends/creators, or `db.create_promo("CODE","plan",plan="basic")`.
- Email signups feed the newsletter table for re-engagement.

Production path: swap SQLite for Postgres and the poll-loop queue for Redis + RQ;
run the web tier under gunicorn. The `db.py` function signatures are the seam.

## Optional: upload as PRIVATE drafts

Only if you want clips to land in YouTube Studio as private drafts to review/publish:
1. Google Cloud Console → enable **YouTube Data API v3** → create an **OAuth
   Desktop** client → download JSON → rename to `client_secrets.json` here.
2. `python clipper.py --list-channels` — sign in and **pick the channel**; it
   prints the channel name+ID so you can confirm. That's how access is limited
   to just that one channel.
3. `python clipper.py "URL" --draft` — uploads each clip as **private** (never
   public) and prints a Studio link.

> Free quota ≈ 10,000 units/day; each upload ≈ 1,600 units (~6/day). Downloading
> and local cutting are free.

## Speed & quality notes
- **Source capped at 1080p + H.264** so you don't download a 4K/AV1 file just to
  make a 1080-wide Short (this was the main slowdown — a 3.5 hr 4K VOD is several GB).
- **Encoding**: auto-uses NVIDIA **NVENC** (GPU) if available, otherwise libx264
  `veryfast`. Parallel download fragments (`-N 8`).
- Audio normalized to −14 LUFS (YouTube's loudness target), 192 kbps AAC.
- `+faststart` for instant web playback; output 1080×1920.
- Detection: local audio-energy analysis — no AI API cost.
- Source videos cache in `downloads/` and are reused on re-runs (so a second run
  on the same URL skips the download entirely).

> Biggest speed lever: the download. A multi-hour VOD is a big file no matter
> what — for fastest results, clip shorter source videos or the same one twice
> (the second run reuses the cached download).
