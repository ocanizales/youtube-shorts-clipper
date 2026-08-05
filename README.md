# youtube-shorts-clipper

Turn a long League of Legends VOD into vertical highlight Shorts — automatically.

It downloads the source, finds the loud moments (teamfights, pentakills, aces),
cuts them into 9:16 clips that track the action, burns in captions, writes
titles and descriptions with a local model, and can push each clip to YouTube as
a **private draft** you review before publishing.

No cloud AI bill. Highlight detection is local audio analysis, captions are
local Whisper, and metadata is a local Ollama model.

```
VOD ──► yt-dlp ──► audio-energy peaks ──► ffmpeg 9:16 cut ──► captions ──► title/desc ──► private draft
        1080p      teamfights & aces      motion tracking     Whisper       Ollama         YouTube
```

## Quick start

```bash
pip install -r requirements.txt
python clipper.py "https://youtu.be/VIDEO_ID"
```

Clips land in `clips/` with a sidecar `.txt` holding the title, caption and tags.
`ffmpeg` must be on your PATH; everything else is pip-installable.

```bash
python clipper.py "URL" --max-clips 8 --clip-len 50 --layout zoom
python clipper.py "URL" --subtitles          # Korean broadcast -> English captions
python clipper.py "URL" --layout whole       # the entire VOD as 61s 16:9 parts
```

Full flag reference and setup: **[SETUP.md](SETUP.md)**.

## Four framings

| | |
|---|---|
| **`full`** | Whole frame centered on a blurred fill, captions underneath. The safe default. |
| **`zoom`** | Punched into the playfield and **tracking the action**, with the game HUD re-stacked at the bottom. |
| **`split`** | Streamer facecam on top, tracked gameplay below. Needs opencv; falls back to `full` when no face is found. |
| **`whole`** | Not a Short — re-cuts the *entire* VOD into consecutive 61-second **landscape 16:9** parts, in order, nothing dropped. |

## What makes the output usable

**Highlight detection is free and local.** Audio RMS energy over the whole
track; the peaks are where the casters shout. No model, no API, no per-video cost.

**Captions survive esports vocabulary.** Whisper is biased at decode time toward
the real roster and game terms, and a repair pass fixes what still slips
through. Korean broadcasts are captioned in English via Whisper's translate
task. Ambiguous words a caster genuinely says — "Korea", "career", "gang" — are
deliberately never rewritten into proper nouns.

**Titles are checked, not just generated.** A 3B local model will happily invent
"the 2023 finals" or attribute a clip to the wrong org. Every generated title,
hook and description is verified against the transcript and a knowledge base
(`lol_kb.py`): any player, team or event that appears in the text but not in the
sources triggers a retry naming what was invented, then a fallback. Prompting
alone was not enough — the residue is silent and confident.

**Nothing is ever auto-published.** `privacyStatus: private` is hardcoded and
deliberately takes no argument, so "publish publicly" cannot become one wrong
parameter away. A test asserts the signature stays closed.

## Three ways to run it

**CLI** — `python clipper.py "URL"`. Fastest, scriptable.

**Web app** — `python serve.py`, then <http://localhost:5000>. Drag-drop or paste
a URL, watch the progress bar, download each clip or send it straight to
YouTube. Flask front end plus a separate worker process, with SQLite as the
queue; run more workers to scale.

**Dashboard page** — a `/clipper` page served by
[project-dashboard](https://github.com/ocanizales/project-dashboard), which
shells out to this repo's venv.

## Uploading

Connect a channel once and every finished clip gets a one-click send. On a
headless box use the device flow — no browser, no tunnel, no redirect URI:

```bash
.venv/bin/python scripts/connect_youtube.py
```

It prints a short code; enter it at <https://google.com/device> on any device.
The resulting `token.pickle` is what `clipper.py --draft` reads, so both paths
work from one connection. See [SETUP.md](SETUP.md#upload-as-private-drafts) for
the Google Cloud Console side.

## Requirements

- Python 3.11+, `ffmpeg` on PATH
- `opencv-python-headless<5` — **the pin is load-bearing**; 5.x removed
  `cv2.CascadeClassifier` and ships no Haar cascades, turning a graceful
  fallback into a mid-render crash
- Optional: `faster-whisper` for captions, Ollama for titles, NVIDIA GPU for
  NVENC encoding (falls back to libx264)

## Notes

Source is capped at **1080p H.264** — downloading a 4K/AV1 file to make a
1080-wide Short is the single biggest waste of time in the pipeline. Audio is
normalized to −14 LUFS, YouTube's loudness target. Downloads are cached and
reused, so a second run on the same URL skips straight to cutting.

## License

Not yet licensed — all rights reserved by default. If you want to use this,
open an issue.
