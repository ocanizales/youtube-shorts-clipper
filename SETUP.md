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
| `--max-clips N` | 5 | how many clips to produce |
| `--clip-len SEC` | 45 | length of each clip |
| `--peak-pos FRAC` | 0.65 | spike position 0–1; higher = longer build-up |
| `--layout fit\|crop` | fit | **fit** = whole frame + blurred bars (keeps minimap/facecam); **crop** = zoom that **tracks the on-screen action** |
| `--caption "TEXT"` | – | burns a headline onto every clip |
| `--cap-size N` | 66 | caption font size |
| `--cap-pos top\|middle\|bottom` | top | caption position |
| `--subtitles` | off | auto-caption spoken words (needs faster-whisper) |
| `--draft` | off | ALSO upload clips as PRIVATE drafts to your channel |
| `--list-channels` | – | show which channel `--draft` would use, then exit |

**Examples:**
```
python clipper.py "URL" --max-clips 8 --clip-len 50 --layout fit
python clipper.py "URL" --caption "INSANE PENTAKILL" --peak-pos 0.7
python clipper.py "URL" --subtitles
```

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
