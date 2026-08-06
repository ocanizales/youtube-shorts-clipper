"""
LoL YouTube Shorts Clipper — turn a YouTube VOD into 9:16 highlight clips.

Four framings (`LAYOUTS`): `full`, `split` and `zoom` are 9:16 Shorts cut from
the audio-spike highlights; `--layout whole` is the exception in both respects —
it renders 16:9 and cuts the ENTIRE video into consecutive 61s parts instead of
selecting anything.

Clips are saved locally by default; nothing is uploaded unless you pass --draft
(which uploads as PRIVATE, never public). Run with -h for all options.
"""

import argparse
import collections
import os
import pickle
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

import lol_kb   # League domain knowledge: whisper biasing, mishear repair, tags

DOWNLOADS_DIR = Path("downloads")
CLIPS_DIR = Path("clips")
W, H = 1080, 1920

# Scaler flags for every upscale onto the 1080x1920 canvas.
#
# These MUST ride on the scale filter itself (`scale=...:flags=...`). The global
# `-sws_flags` CLI option was used here until 2026-07-29 and is silently ignored
# by this ffmpeg build: encoding the same clip with global `lanczos` vs global
# `bicubic` produced a **byte-identical** decoded stream (md5 bd7b0c47…), while
# explicit per-filter flags changed it. So every clip rendered before that date
# used the default scaler, not lanczos, despite the comment claiming otherwise.
# Exactly the trap already recorded for `-color_primaries` (dropped into the VUI,
# hence the `setparams` filter): global options here are advisory at best.
#
# Measured honestly, lanczos vs bicubic is worth ~9e-5 SSIM against a lossless
# reference — i.e. nothing visible. This is a correctness fix so the code does
# what it says, not a quality win. The real speed lever is the x264 preset.
SCALE_FLAGS = "lanczos"


def _resolve_font() -> str:
    """First bold TTF that exists, as a drawtext-safe path (colon escaped).
    Windows Arial when present; else the Linux DejaVu/Liberation bolds on the VPS."""
    candidates = [
        R"C:/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    path = next((c for c in candidates if os.path.exists(c)), candidates[1])
    return path.replace("\\", "/").replace(":", R"\:")  # drawtext escapes the drive colon


FONT = _resolve_font()
# Always grab the best video up to 1080p, ANY codec (1080p on YouTube is usually
# VP9/webm, not mp4 — restricting to mp4 silently drops you to 720p or lower).
FMT = "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b"
# Format sort: resolution 1080 first, then 60fps, then VP9 — at a given YouTube
# resolution the VP9 rendition carries noticeably more detail than the h264 one,
# and we re-encode everything anyway so the source container doesn't matter.
# (AV1 is left at default rank: better still, but too slow to decode on a CPU VPS.)
FMT_SORT = "res:1080,fps,vcodec:vp9,acodec:opus"
_MEDIA_EXTS = (".mp4", ".mkv", ".webm")

_WINGET_FFMPEG = (
    Path(os.environ.get("LOCALAPPDATA", "")) /
    r"Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\ffmpeg-8.1.1-full_build\bin"
)


# ── ffmpeg discovery + encoder selection ─────────────────────────────────────
def _resolve_ffmpeg() -> tuple[str, str | None]:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return "ffmpeg", None
    except (FileNotFoundError, subprocess.CalledProcessError):
        if (_WINGET_FFMPEG / "ffmpeg.exe").exists():
            return str(_WINGET_FFMPEG / "ffmpeg.exe"), str(_WINGET_FFMPEG)
        sys.exit("[error] ffmpeg not found. Install with: winget install Gyan.FFmpeg")


_FFMPEG, _FFMPEG_DIR = _resolve_ffmpeg()


# Encode quality tier. YouTube re-encodes every upload with a lossy VP9/AV1 pass,
# so whatever we hand it is the *ceiling* — a lean upload gets crushed twice.
# We deliberately upload far above YouTube's own recommended 1080p bitrate
# (~8–12 Mbps) so the transcoder has clean detail to work from.
#   fast = old behaviour (quick previews), high = default, max = archival.
QUALITY = os.environ.get("SHORTS_QUALITY", "high").lower()
_X264 = {                       # crf, preset, target maxrate, bufsize
    "fast": ("21", "veryfast", "12M", "24M"),
    # high: preset was `medium` until 2026-07-29. Benchmarked on 20s of real
    # gameplay against a LOSSLESS lanczos reference: medium 15.3s @ SSIM 0.975409,
    # faster 10.3s @ 0.975347 — a 32% encode saving for 6e-5 SSIM, which is orders
    # of magnitude below anything visible. `veryfast` saved 51% and still measured
    # 0.975232, but `faster` keeps motion-search headroom for busy teamfights,
    # which is where a weak preset would actually show and where SSIM is least
    # trustworthy. Encode was 63% of per-clip wall time before this change.
    "high": ("17", "faster",   "24M", "48M"),
    # max: lowest CRF the upload pipeline benefits from + the slowest preset we'll
    # accept, with headroom on the rate cap so a busy teamfight keeps its detail.
    "max":  ("15", "slower",   "48M", "96M"),
}
_NVENC_CQ = {"fast": "23", "high": "19", "max": "16"}
# Appended to every output filter chain so the file is tagged Rec.709 (see cut_clip).
_BT709 = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"
# One loudness reference for the whole Short. With a cold-open teaser this MUST run
# after the concat: normalizing 1.8s of the loudest audio in the clip on its own
# would flatten exactly the punch the teaser exists to deliver.
_LOUDNORM = "loudnorm=I=-14:TP=-1.5:LRA=11"


def _pick_encoder() -> list[str]:
    """Use NVIDIA NVENC if it actually works (much faster); else quality x264.

    Both paths are capped-VBR: a low CRF/CQ for detail plus a maxrate ceiling so
    a single busy teamfight can't balloon the file past what upload can handle."""
    crf, preset, maxrate, bufsize = _X264.get(QUALITY, _X264["high"])
    try:
        subprocess.run([_FFMPEG, "-hide_banner", "-f", "lavfi",
                        "-i", "color=black:s=64x64:d=0.1",
                        "-c:v", "h264_nvenc", "-f", "null", "-"],
                       capture_output=True, check=True)
        cq = _NVENC_CQ.get(QUALITY, "19")
        print(f"[encoder] NVIDIA NVENC (GPU) — quality={QUALITY} cq={cq} cap={maxrate}")
        args = ["-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq",
                "-rc", "vbr", "-cq", cq, "-maxrate", maxrate, "-bufsize", bufsize,
                "-rc-lookahead", "32", "-spatial-aq", "1", "-temporal-aq", "1",
                "-profile:v", "high"]
        if QUALITY == "max":                 # squeeze the GPU path harder (slower, better)
            args += ["-multipass", "fullres", "-b_ref_mode", "middle"]
        return args
    except (FileNotFoundError, subprocess.CalledProcessError):
        print(f"[encoder] libx264 (CPU) — quality={QUALITY} crf={crf} preset={preset}")
        return ["-c:v", "libx264", "-crf", crf, "-preset", preset,
                "-maxrate", maxrate, "-bufsize", bufsize,
                "-profile:v", "high", "-level", "4.2"]


_ENC = _pick_encoder()


def ff(*args):
    subprocess.run([_FFMPEG, *args], capture_output=True, check=True)


# ── yt-dlp discovery ─────────────────────────────────────────────────────────
def _resolve_ytdlp() -> str:
    """yt-dlp is installed in this venv, but the venv's bin isn't on PATH when
    the interpreter is invoked directly (dashboard/systemd do exactly that) —
    prefer the copy sitting next to the running python, else fall back to PATH."""
    for name in ("yt-dlp", "yt-dlp.exe"):
        sibling = Path(sys.executable).with_name(name)
        if sibling.exists():
            return str(sibling)
    return "yt-dlp"


def _js_runtime_args() -> list[str]:
    """YouTube signature solving needs a JS runtime + the yt-dlp-ejs solver
    (in requirements). node isn't on the dashboard's stripped PATH, so pass
    its location explicitly; without it formats go missing and 1080p regresses."""
    node = shutil.which("node") or str(Path.home() / ".local" / "bin" / "node")
    return ["--js-runtimes", f"node:{node}"] if os.path.exists(node) else []


_YTDLP = [_resolve_ytdlp(), *_js_runtime_args()]


# ── 1. download (with progress callback) ─────────────────────────────────────
_PCT = re.compile(r"(\d{1,3}\.\d)%")


def _find_media(vid: str) -> Path | None:
    """Largest finished media file for this id (any container), ignoring partials."""
    files = [p for p in DOWNLOADS_DIR.glob(f"{vid}.*")
             if p.suffix.lower() in _MEDIA_EXTS and p.stat().st_size > 1_000_000]
    return max(files, key=lambda p: p.stat().st_size) if files else None


def download_video(url: str, on_progress=None) -> Path:
    """Download the best video+audio up to 1080p (any container), merged."""
    vid = subprocess.run([*_YTDLP, "--no-playlist", "--print", "id", url],
                         capture_output=True, text=True, check=True).stdout.strip()
    cached = _find_media(vid)
    if cached:
        print(f"[download] reusing {cached}")
        return cached

    cmd = [*_YTDLP, "--no-playlist", "-N", "8", "--newline", "-f", FMT,
           # mkv merges VP9+opus losslessly; mp4 would force an extra remux.
           "-S", FMT_SORT, "--merge-output-format", "mkv",
           "-o", str(DOWNLOADS_DIR / f"{vid}.%(ext)s")]
    if _FFMPEG_DIR:
        cmd += ["--ffmpeg-location", _FFMPEG_DIR]
    proc = subprocess.Popen(cmd + [url], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    # Keep the tail of yt-dlp's own output. stderr is merged into stdout here,
    # so the only record of WHY a download died arrives on this loop — and every
    # line that is not a progress percentage used to be read and dropped,
    # leaving a bare "yt-dlp failed" with the diagnosis discarded. A ring buffer
    # costs nothing on success and is the whole story on failure.
    tail = collections.deque(maxlen=12)
    for line in proc.stdout:                      # stream + parse % as it downloads
        m = _PCT.search(line)
        if m:
            pct = float(m.group(1))
            (on_progress or (lambda p: print(f"\r[download] {p:5.1f}%", end="")))(pct)
        elif line.strip():
            tail.append(line.rstrip())
    proc.wait()
    print()
    if proc.returncode != 0:
        detail = "\n".join(f"  {ln}" for ln in tail) or "  (no output captured)"
        sys.exit(f"[error] yt-dlp failed (exit {proc.returncode}). Its last "
                 f"output:\n{detail}\n"
                 f"[hint] A failure partway through a download is usually "
                 f"transient — YouTube throttles datacenter IPs. Retrying "
                 f"generally works, and the partial file is reused.")

    media = _find_media(vid)
    if not media:
        sys.exit(f"[error] download produced no media file for {vid}.")
    print(f"[download] {media}  ({media.stat().st_size // 1_000_000} MB)")
    return media


# ── 2. detect hype moments via audio energy ──────────────────────────────────
# HPC tuning — see docs/superpowers/plans/2026-07-28-hpc-hook.md. The formula is
# Hook / Progression / Climax: `peak_pos` already placed the climax well, but the
# opening seconds were a pure arithmetic offset and so were never chosen at all.
HOOK_SEARCH   = 4      # seconds either side of the nominal start to scan for a livelier open
HOOK_PEAK_MIN = 0.55   # after refining, the spike must still land this far into the clip...
HOOK_PEAK_MAX = 0.90   # ...and no later than this, so the payoff never opens the clip
TEASER_DUR    = 1.8    # cold-open flash length — short enough not to resolve the payoff
TEASER_LEAD   = 1.4    # flash starts this far before the spike, so it ENDS 0.4s after it


def _refine_start(energy: np.ndarray, nominal: int, peak: int,
                  clip_len: int, n: int) -> int:
    """Pick the liveliest opening second near `nominal` (HPC's "H").

    `start = peak - clip_len*peak_pos` lands wherever the arithmetic puts it —
    in a pro VOD that is usually farming, warding or walking, i.e. the clip opens
    on its least interesting second. Scan +-HOOK_SEARCH around it and take the
    second with the most audio energy (a shout, an engage call, a ping), so the
    Short opens on *something*.

    Guarded so the fix can't undo the climax placement: candidates must keep the
    spike inside [HOOK_PEAK_MIN, HOOK_PEAK_MAX] of the clip and keep the clip
    inside the video. Ties break toward `nominal` — no movement without a reason.
    Operates on the per-second energy array find_hype_moments already has, so
    this costs no extra decode.
    """
    fallback = max(0, min(nominal, n - clip_len))
    lo, hi = max(0, nominal - HOOK_SEARCH), min(n - clip_len, nominal + HOOK_SEARCH)
    cands = [s for s in range(lo, hi + 1)
             if HOOK_PEAK_MIN <= (peak - s) / clip_len <= HOOK_PEAK_MAX]
    if not cands:
        return fallback
    return max(cands, key=lambda s: (float(energy[s]), -abs(s - nominal)))


def find_hype_moments(video: Path, clip_len: int, top_n: int, peak_pos: float) -> list[float]:
    import librosa
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    try:
        print("[detect] extracting + analyzing audio...")
        ff("-y", "-i", str(video), "-ac", "1", "-ar", "16000", tmp.name)
        y, sr = librosa.load(tmp.name, sr=16000, mono=True)
    finally:
        os.unlink(tmp.name)

    n = len(y) // sr
    if n == 0:
        sys.exit("[error] audio track too short or empty.")
    energy = np.sqrt(np.mean(y[:n * sr].reshape(n, sr) ** 2, axis=1))
    smoothed = np.convolve(energy, np.ones(5) / 5, mode="same")

    lead_in = int(clip_len * peak_pos)
    chosen: list[float] = []
    for frame in np.argsort(smoothed)[::-1]:
        peak = int(frame)
        start = max(0, peak - lead_in)
        if start + clip_len > n:
            continue
        start = _refine_start(smoothed, start, peak, clip_len, n)   # own the hook
        if all(abs(start - s) >= clip_len for s in chosen):
            chosen.append(float(start))
        if len(chosen) >= top_n:
            break
    chosen.sort()
    print(f"[detect] {len(chosen)} moments at {[f'{t/60:.1f}min' for t in chosen]}")
    return chosen


# ── 3. motion-aware crop + caption/subtitle compositing ──────────────────────
def _dims(video: Path) -> tuple[int, int]:
    probe = _FFMPEG.replace("ffmpeg.exe", "ffprobe.exe") if _FFMPEG_DIR else "ffprobe"
    out = subprocess.run([probe, "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height", "-of", "csv=p=0",
                          str(video)], capture_output=True, text=True).stdout
    nums = re.findall(r"\d+", out)
    return int(nums[0]), int(nums[1])


def _duration(video: Path) -> float:
    """Length of a media file in seconds (0.0 if ffprobe can't say)."""
    probe = _FFMPEG.replace("ffmpeg.exe", "ffprobe.exe") if _FFMPEG_DIR else "ffprobe"
    out = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(video)],
                         capture_output=True, text=True).stdout
    try:
        return float(out.strip())
    except ValueError:
        return 0.0


def _column_motion(frames: np.ndarray) -> np.ndarray:
    """Per-column motion over time, with distractor regions masked out.

    `frames` is (nf, SH, SW) gray. Returns (nf-1, SW): the absolute inter-frame
    difference, summed over rows, AFTER zeroing the bottom-right minimap corner
    and the bottom HUD strip so their constant animation can't attract the crop.
    """
    diff = np.abs(np.diff(frames, axis=0))            # (nf-1, SH, SW)
    mm_h, mm_w = int(SH * MINIMAP_FRAC), int(SW * MINIMAP_FRAC)
    hud_h = int(SH * HUD_MASK_FRAC)
    if mm_h and mm_w:
        diff[:, SH - mm_h:, SW - mm_w:] = 0           # minimap corner
    if hud_h:
        diff[:, SH - hud_h:, :] = 0                   # ability/HUD strip
    return diff.sum(axis=1)                            # (nf-1, SW)


def _motion_profile(video: Path, start: float, dur: int) -> tuple[np.ndarray, np.ndarray]:
    """Decode a downscaled gray clip; return (times, per-column motion over time).

    One ffmpeg pass at SAMPLE_FPS. times has length nf-1 (one per motion frame);
    profile is (nf-1, SW). Returns empties if the clip is too short to diff.
    """
    raw = subprocess.run(
        [_FFMPEG, "-ss", str(start), "-i", str(video), "-t", str(dur),
         "-vf", f"fps={SAMPLE_FPS},scale={SW}:{SH},format=gray",
         "-f", "rawvideo", "-"], capture_output=True).stdout
    nf = len(raw) // (SW * SH)
    if nf < 2:
        return np.zeros(0), np.zeros((0, SW))
    frames = np.frombuffer(raw[:nf * SW * SH], np.uint8).reshape(nf, SH, SW).astype(np.int16)
    profile = _column_motion(frames)
    times = np.arange(profile.shape[0]) / SAMPLE_FPS
    return times, profile


def _aim_targets(profile: np.ndarray, win: int) -> np.ndarray:
    """Per frame, the left column of the width-`win` window with the most
    center-biased motion. A broad Gaussian centered on the frame center weights
    the profile (the game camera already centers the champion), so the aim
    prefers the middle unless off-center action is genuinely stronger.
    """
    nf, sw = profile.shape
    cols = np.arange(sw)
    bias = np.exp(-0.5 * ((cols - (sw - 1) / 2.0) / (CENTER_SIGMA_FRAC * sw)) ** 2)
    biased = profile * bias[None, :]
    kernel = np.ones(win)
    out = np.empty(nf, dtype=int)
    default_left = (sw - win) // 2
    for i in range(nf):
        sums = np.convolve(biased[i], kernel, "valid")   # len sw-win+1
        out[i] = int(np.argmax(sums)) if sums.max() > 0 else default_left
    return out


def _ease(targets_px: np.ndarray, src_w: int, crop_w: int,
          deadzone_px: float, max_step_px: float) -> np.ndarray:
    """Turn a noisy per-sample target into a smooth crop-x path.

    Holds position while the target stays within `deadzone_px` of the current x
    (kills micro-jitter); otherwise eases toward it by EASE_ALPHA, capped at
    `max_step_px` per sample so it glides instead of snapping. Clamped to the
    valid crop range.
    """
    lo, hi = 0.0, float(max(0, src_w - crop_w))
    xs = np.empty(len(targets_px), dtype=float)
    cur = float(np.clip(targets_px[0], lo, hi)) if len(targets_px) else lo
    for i, raw in enumerate(targets_px):
        tgt = float(np.clip(raw, lo, hi))
        if abs(tgt - cur) > deadzone_px:
            step = EASE_ALPHA * (tgt - cur)
            step = max(-max_step_px, min(max_step_px, step))
            cur = min(hi, max(lo, cur + step))
        xs[i] = cur
    return xs


def _write_sendcmd(times: np.ndarray, xs: np.ndarray, path: Path) -> Path:
    """Write an ffmpeg sendcmd script driving `crop@dyn`'s x.

    sendcmd applies each command as a step (no interpolation), so we densify the
    eased path to CMD_FPS by linear interpolation — small per-frame steps read as
    a smooth pan.
    """
    if len(times) >= 2:
        dense_t = np.arange(float(times[0]), float(times[-1]), 1.0 / CMD_FPS)
        dense_x = np.interp(dense_t, times, xs)
    else:
        dense_t, dense_x = times, xs
    lines = [f"{t:.3f} crop@dyn x {int(round(x))};" for t, x in zip(dense_t, dense_x)]
    path.write_text("\n".join(lines) + "\n")
    return path


def track_path(video: Path, start: float, dur: int, src_w: int, src_h: int,
               crop_w: int | None = None) -> tuple[int, Path | None]:
    """Eased crop-x trajectory that follows the action (replaces focus_x).

    Returns (x0, script_path). script_path is an ffmpeg sendcmd file driving
    `crop@dyn`; it is None when the path is effectively constant (short/blank
    clip, or the action never leaves the deadzone) so the caller can render a
    plain static crop. Only x moves — height/vertical framing is fixed.
    """
    crop_w = crop_w or min(int(src_h * 9 / 16), src_w)
    center_x = max(0, (src_w - crop_w) // 2)
    times, profile = _motion_profile(video, start, dur)
    if profile.shape[0] < 2 or profile.sum() == 0:
        return center_x, None
    win = max(1, round(crop_w / src_w * SW))
    if win >= SW:
        return center_x, None
    targets_col = _aim_targets(profile, win)                 # downscaled left col
    targets_px = targets_col / SW * src_w                    # source px (crop-left)
    max_step = (MAX_PAN_PX_PER_S or src_w * 0.6) / SAMPLE_FPS
    xs = _ease(targets_px, src_w, crop_w, DEADZONE_FRAC * src_w, max_step)
    if float(np.ptp(xs)) < 1.0:                              # never really moves
        return int(round(xs[0])), None
    script = CLIPS_DIR / f".track_{int(start)}_{crop_w}.cmd"
    _write_sendcmd(times, xs, script)
    return int(round(xs[0])), script


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", R"\:").replace("'", R"’")


# ── the four framings we ship ────────────────────────────────────────────────
# This tuple is the SINGLE SOURCE OF TRUTH for the framing menu. Everything that
# offers a choice reads it or is checked against it: `--layout`'s argparse
# choices, web/app.py's POST validation, the Flask template's <select>, and the
# dashboard's /clipper page (which is stdlib-only and so parses this literal out
# of the source rather than importing the module — see `clipper_layouts()`
# there). Adding or removing a framing here is the one edit that matters; the
# rest is labels.
#
#   full   9:16, whole source frame centered on a blurred fill of itself
#   whole  16:9, the ENTIRE video re-cut into consecutive 61s "Part N" pieces
#   split  9:16, streamer facecam on top / tracked gameplay below
#   zoom   9:16, punched-in playfield with the game HUD re-stacked underneath
#
# "crop" and "fit" stay internal-only `build_vf` primitives — "crop" is the plain
# tracked 9:16 window the `--sample` harness renders with, "fit" is the
# letterbox-into-blur variant. Neither is a framing a user can pick.
LAYOUTS = ("full", "whole", "split", "zoom")

# Output canvas per framing. Everything is a 9:16 vertical Short except `whole`,
# which exists precisely to keep the source's own landscape shape — so the canvas
# has to follow the layout instead of being assumed vertical. `canvas()` is what
# build_vf / caption_anchor / the ASS builder ask; the module-level W, H remain
# the 9:16 default for everything that is only ever vertical (thumbnails).
LAYOUT_CANVAS = {"whole": (1920, 1080)}


def canvas(layout) -> tuple[int, int]:
    """(width, height) of the output canvas for `layout`."""
    return LAYOUT_CANVAS.get(layout, (W, H))


SPLIT_TOP_FRAC = 0.42   # split: facecam occupies the top portion of the 9:16 canvas
ZOOM_TOP_FRAC  = 0.105  # zoom: black caption bar height (fraction of the 9:16 canvas)
ZOOM_HUD_FRAC  = 0.19   # zoom: bottom slice of the SOURCE treated as the game HUD strip

# ── whole-video mode ─────────────────────────────────────────────────────────
# `whole` is not a highlight picker: it cuts the ENTIRE source into consecutive
# parts, chronologically, and every frame of the video ends up in exactly one of
# them. 61s because that is the runtime a Short is cut for; the count is whatever
# the duration divides into, which is why the UI hides "clips" and "secs each".
WHOLE_PART_LEN = 61
# A remainder shorter than this is folded into the last full part instead of
# being emitted as its own. The requirement is that no footage is dropped, not
# that every part is exactly 61s — and a 3-second "Part 8" is not a video, it is
# an accident. Merging keeps every frame at the cost of one part running up to
# 61+WHOLE_TAIL_MIN-1 = 75s, comfortably inside the 3-minute Shorts ceiling.
WHOLE_TAIL_MIN = 15


def whole_segments(duration: float, part_len: int = WHOLE_PART_LEN,
                   tail_min: int = WHOLE_TAIL_MIN) -> list[tuple[float, float]]:
    """Consecutive (start, length) parts covering the WHOLE video, in order.

    Part 1 is 0:00-1:01, part 2 is 1:01-2:02, and so on: no gaps, no overlap, and
    the segments always sum back to `duration` (that is the invariant — "all of
    the video" is the feature). A trailing remainder shorter than `tail_min` is
    merged into the last part rather than shipped as a runt of its own; a video
    shorter than one part is a single part, because there is nothing to merge it
    into and dropping it would drop the entire video.
    """
    if duration <= 0:
        return []
    n = int(duration // part_len)
    rem = duration - n * part_len
    segs = [(float(i * part_len), float(part_len)) for i in range(n)]
    if not segs:                      # shorter than one part -> it IS the one part
        return [(0.0, float(duration))]
    if rem <= 1e-6:                   # exact multiple: nothing left over
        return segs
    if rem < tail_min:                # runt tail -> extend the last part over it
        start, length = segs[-1]
        segs[-1] = (start, length + rem)
    else:
        segs.append((float(n * part_len), rem))
    return segs


# ── framing / tracking tunables (docs/superpowers/specs/2026-07-23-...) ──────
SW, SH            = 240, 135  # downscaled analysis grid (cols, rows)
SAMPLE_FPS        = 6         # motion-sampling rate for the crop trajectory
CENTER_SIGMA_FRAC = 0.30      # width of the center-bias Gaussian (frac of SW)
MINIMAP_FRAC      = 0.23      # bottom-right box masked out (frac of W & H)
HUD_MASK_FRAC     = 0.16      # bottom strip masked out (frac of H)
DEADZONE_FRAC     = 0.06      # hold still while target stays within this frac of src_w
EASE_ALPHA        = 0.18      # exponential ease toward target per sample
MAX_PAN_PX_PER_S  = None      # velocity cap; None -> src_w * 0.6
CMD_FPS           = 15        # sendcmd densification rate (pan smoothness)
CAP_SIZE_TRACKED  = 84        # caption size bump for crop/zoom layouts

# ── affiliate end card (docs/superpowers/specs/2026-07-28-affiliate-*.md) ─────
# Shorts has no clickable surface mid-playback, so the only route to an affiliate
# link is comments -> owned page. The card exists to point at the pinned comment.
# It is drawn OVER frames the clip already had, so it adds zero runtime — this is
# the deliberate alternative to the 5s spoken outro, which would have cost 11-17%
# of a Short's runtime on a CTA fired when nothing is tappable.
ENDCARD_DUR  = 1.5        # seconds on screen, at the very end
ENDCARD_SIZE = 58         # font size
ENDCARD_BG   = "#252525"  # scrim colour — never pure black (standing rule)
ENDCARD_BAND = 0.11       # band height as a fraction of the 1920px canvas


def _even(v) -> int:
    return int(round(v / 2) * 2)  # yuv420p needs even dimensions


def zoom_geometry(dims) -> tuple[int, int, int, int, int, int]:
    """
    Band plan for the 'zoom' layout (the Korean solo-queue Short look):
    [black caption bar | punched-in playfield | game HUD strip], all full width,
    no blur. The playfield (source minus its HUD) is cropped to the window width
    that exactly fills the middle band when scaled to 1080 wide — that same width
    is what sets the punch-in (~1.8x on 16:9 sources).
    Returns (hud_src_h, play_h, top_bar, hud_out_h, mid_h, crop_w).
    """
    src_w, src_h = dims
    hud_src_h = _even(src_h * ZOOM_HUD_FRAC)   # HUD strip height in the source
    play_h    = src_h - hud_src_h              # playfield height in the source
    top_bar   = _even(H * ZOOM_TOP_FRAC)       # black caption bar (output px)
    hud_out_h = _even(hud_src_h * W / src_w)   # HUD scaled to the full output width
    mid_h     = H - top_bar - hud_out_h        # playfield band (output px)
    crop_w    = min(_even(play_h * W / mid_h), src_w)
    return hud_src_h, play_h, top_bar, hud_out_h, mid_h, crop_w


def split_geometry(dims) -> tuple[int, int, int]:
    """
    Band plan for the 'split' layout: [facecam | tracked gameplay], stacked.
    Returns (top_h, bot_h, crop_w) — the facecam band height, the gameplay band
    height, and the SOURCE-pixel width of the gameplay window. `crop_w` is the
    full-height window whose aspect matches the gameplay band, so scaling it to
    the band neither stretches nor letterboxes; it is narrower than the source,
    which is what leaves room for the same eased pan `crop`/`zoom` use.
    """
    src_w, src_h = dims
    Wc, Hc = canvas("split")
    top_h = _even(Hc * SPLIT_TOP_FRAC)
    bot_h = Hc - top_h
    crop_w = min(_even(src_h * Wc / bot_h), src_w)
    return top_h, bot_h, crop_w


def full_video_height(dims) -> int:
    """Height (px) of the source frame when scaled to the full 1080 width."""
    src_w, src_h = dims
    return int(round(W * src_h / src_w / 2) * 2)  # even number for yuv420p


def caption_anchor(layout, dims) -> tuple[int, int]:
    """
    Where captions belong for each layout, as (ASS alignment, margin px).
    Alignment 8 = top-anchored (margin measured from the top),
    Alignment 2 = bottom-anchored (margin measured from the bottom).

    Margins are in CANVAS pixels, so they are measured against `canvas(layout)` —
    a margin tuned as a fraction of a 1920px-tall Short would sit a long way off
    the bottom of the 1080px-tall `whole` canvas.
    """
    _, Hc = canvas(layout)
    if layout == "split":      # only at the BOTTOM of the facecam (top) panel
        return 8, int(Hc * SPLIT_TOP_FRAC * 0.88)
    if layout == "full":       # right UNDER the (now centered) video
        return 8, (H + full_video_height(dims)) // 2 + 24
    if layout == "whole":
        # Landscape lower third, lifted clear of the end-card band. The vertical
        # layouts let the CTA cover whatever caption is under it for the closing
        # beat; on a 1080-tall canvas that band is a much larger share of the
        # frame, so here the caption sits above it instead of behind it.
        return 2, int(Hc * (ENDCARD_BAND + 0.03))
    if layout == "fit":        # on the bottom blurred bar, clear of gameplay
        return 2, int(H * 0.07)
    if layout == "zoom":       # lower third: just above the bottom HUD strip
        _, _, _, hud_out_h, _, _ = zoom_geometry(dims)
        return 2, hud_out_h + int(H * 0.03)
    return 2, int(H * 0.16)    # crop: lower third, above the source's bottom HUD


def build_vf(layout, dims, crop_x, ass_path, caption, cap_size, cap_an, cap_margin,
             sendcmd=None, crop_w=None, suffix="", endcard=None, endcard_from=None,
             facecam=None, facecam_cmd=None) -> str:
    """Build the reframing filter chain for one segment, onto `canvas(layout)`.

    Every framing but `whole` targets the 1080x1920 vertical Short; `whole`
    targets 1920x1080, so the canvas is read per layout rather than assumed.

    `facecam` is (x, y, w, h) in SOURCE pixels and only means anything to the
    `split` layout — it is keyword-only so the positional signature every other
    call site (and every test) uses stays exactly as it was.

    `suffix` is appended to every internal link label AND to the `crop@dyn`
    filter *instance name*, so two copies of this graph can live in a single
    `-filter_complex` without colliding (the cold-open teaser renders alongside
    the main segment). `suffix=""` must stay byte-identical to the pre-teaser
    output — that identity is the regression guard for the framing work, and it
    also keeps the production segment's crop named exactly `crop@dyn`, which is
    the target `_write_sendcmd` addresses.
    """
    src_w, src_h = dims
    Wc, Hc = canvas(layout)
    s = suffix
    dyn = f"crop@dyn{s}"
    if layout == "split" and not facecam:
        # No face was found (or the caller never looked). Falling through to the
        # tracked-crop branch would silently ship a framing nobody asked for, so
        # do here what cut_clip does out loud: show the whole video instead.
        layout = "full"
    if layout == "split":                    # facecam on top, tracked gameplay below
        fx, fy, fw, fh = facecam
        top_h, bot_h, gw = split_geometry(dims)
        # Named `crop@face` so a sendcmd script can move it: the webcam is not in
        # a fixed place for the whole clip when the stream switches scenes. Only
        # x and y are ever commanded — w and h decide the frame size the vstack
        # below is built around, and a chain whose frame size changes mid-stream
        # does not survive the stack.
        vf = (f"split=2[cam{s}][pf{s}];"
              f"[cam{s}]crop@face{s}=w={fw}:h={fh}:x={fx}:y={fy},"
              f"scale={Wc}:{top_h}:force_original_aspect_ratio=increase:flags={SCALE_FLAGS},"
              f"crop={Wc}:{top_h}[face{s}];"
              # The gameplay panel is a full-height window that PANS with the
              # action, same primitive as crop/zoom — the 2026-07 pipeline drives
              # crop@dyn's x from a sendcmd script, and the old static centre crop
              # would have been the only framing left standing still.
              f"[pf{s}]{dyn}=w={gw}:h={src_h}:x={crop_x}:y=0,"
              f"scale={Wc}:{bot_h}:flags={SCALE_FLAGS}[game{s}];"
              f"[face{s}][game{s}]vstack=inputs=2")
    elif layout == "whole":                  # 16:9: the source frame, untouched
        # No crop, no blur, no punch-in: this framing exists to ship the video as
        # it was shot, just re-timed into parts. The scale+pad is a no-op on a
        # 16:9 source and letterboxes anything else onto the 16:9 canvas rather
        # than distorting it. The pad colour is the house grey, never pure black.
        vf = (f"scale={Wc}:{Hc}:force_original_aspect_ratio=decrease:flags={SCALE_FLAGS},"
              f"pad={Wc}:{Hc}:(ow-iw)/2:(oh-ih)/2:{ENDCARD_BG},setsar=1")
    elif layout == "full":                   # WHOLE video centered, blurred fill above+below
        vf = (f"split=2[bg{s}][fg{s}];"
              f"[bg{s}]scale={W}:{H}:force_original_aspect_ratio=increase,"
              f"crop={W}:{H},boxblur=22:4[b{s}];"
              f"[fg{s}]scale={W}:-2:flags={SCALE_FLAGS}[v{s}];"
              f"[b{s}][v{s}]overlay=(W-w)/2:(H-h)/2")  # centered in the 9:16 frame
    elif layout == "fit":                    # whole frame centered + blurred bars
        vf = (f"split[bg{s}][fg{s}];[bg{s}]scale={W}:{H}:force_original_aspect_ratio=increase,"
              f"crop={W}:{H},boxblur=22:4[b{s}];"
              f"[fg{s}]scale={W}:{H}:force_original_aspect_ratio=decrease:flags={SCALE_FLAGS}[f{s}];"
              f"[b{s}][f{s}]overlay=(W-w)/2:(H-h)/2")
    elif layout == "zoom":                   # punched-in playfield + game HUD re-stacked below
        _, play_h, top_bar, hud_out_h, mid_h, cw = zoom_geometry(dims)
        hud_src_h = src_h - play_h
        vf = (f"split=2[pf{s}][hs{s}];"
              f"[pf{s}]{dyn}=w={cw}:h={play_h}:x={crop_x}:y=0,scale={W}:{mid_h}:flags={SCALE_FLAGS}[game{s}];"
              f"[hs{s}]crop={src_w}:{hud_src_h}:0:{play_h},scale={W}:{hud_out_h}:flags={SCALE_FLAGS}[hud{s}];"
              f"[game{s}][hud{s}]vstack=inputs=2,pad={W}:{H}:0:{top_bar}:black")
    else:                                    # motion-tracked 9:16 crop (internal-only)
        # The four branches above this one are 9:16 by construction (their band
        # plans come from zoom_geometry / full_video_height, which are written in
        # 1080x1920 terms), so they keep using W/H directly. Only the shared tail
        # below has to work on either canvas.
        if crop_w:                           # sample harness punch-in: a tighter 9:16 window
            cw = min(_even(crop_w), src_w)
            ch = min(_even(cw * H / W), src_h)   # keep 9:16 so scaling to WxH doesn't distort
            cy = (src_h - ch) // 2               # re-centered vertically
        else:                                # production: full-height 9:16 slice
            cw, ch, cy = min(int(src_h * 9 / 16), src_w), src_h, 0
        vf = f"{dyn}=w={cw}:h={ch}:x={crop_x}:y={cy},scale={W}:{H}:flags={SCALE_FLAGS}"
    if facecam_cmd:                          # feed crop@face's x/y live -> follows the webcam
        # Prepended before the gameplay tracker's own sendcmd so both end up
        # ahead of the graph they drive; sendcmd is a pass-through, so two of
        # them in series is just two sets of commands on the same timeline.
        p = str(facecam_cmd).replace("\\", "/").replace(":", R"\:")
        vf = f"sendcmd=f='{p}',{vf}"
    if sendcmd:                              # feed crop@dyn's x live -> a smooth pan
        p = str(sendcmd).replace("\\", "/").replace(":", R"\:")
        vf = f"sendcmd=f='{p}',{vf}"
    if ass_path:                             # dynamic word-by-word captions
        p = str(ass_path).replace("\\", "/").replace(":", R"\:")
        vf += f",ass='{p}'"
    if caption:                              # static headline, placed by layout anchor
        y = f"{cap_margin}" if cap_an in (7, 8, 9) else f"h-text_h-{cap_margin}"
        vf += (f",drawtext=fontfile='{FONT}':text='{_esc(caption)}':fontcolor=white:"
               f"fontsize={cap_size}:borderw=5:bordercolor=black@0.9:x=(w-text_w)/2:y={y}")
    if endcard and endcard_from is not None:
        # Affiliate CTA over the final ENDCARD_DUR seconds. Deliberately appended
        # LAST, so it composites on top of any caption still on screen: the
        # layouts anchor captions at different heights, and rather than solve
        # collision per layout we let the CTA own the lower band for the closing
        # beat. Every layer below it has already had its turn by then.
        on = f"gte(t,{endcard_from:.2f})"
        # Band and type size are fractions of the CANVAS height, not of 1920: on
        # the 16:9 `whole` canvas a band tuned for a 1920px-tall Short would eat
        # a fifth of the frame and the text would be twice as large as intended.
        band = int(Hc * ENDCARD_BAND)
        ec_size = max(20, round(ENDCARD_SIZE * Hc / H))
        vf += (f",drawbox=x=0:y=ih-{band}:w=iw:h={band}:"
               f"color={ENDCARD_BG}@0.82:t=fill:enable='{on}'")
        vf += (f",drawtext=fontfile='{FONT}':text='{_esc(endcard)}':fontcolor=white:"
               f"fontsize={ec_size}:borderw=3:bordercolor=black@0.6:"
               f"x=(w-text_w)/2:y=h-{band}+({band}-text_h)/2:enable='{on}'")
    return vf


_WHISPER = None

# ── spoken-language handling ─────────────────────────────────────────────────
# Source languages whose speech gets captioned as ENGLISH instead of verbatim.
# Whisper's "translate" task decodes straight to English, so this costs one pass,
# not a transcribe-then-translate round trip. Korean is here because the LCK is
# the source of most of this footage; adding a language is a one-word edit.
TRANSLATE_LANGS = {"ko"}
LANG_MIN_PROB   = 0.60   # below this the detector is guessing — caption verbatim


def _whisper_model():
    """Cached faster-whisper model, or None if the package isn't installed.
    CPU int8 avoids a CUDA/cuBLAS dependency; the model load is the expensive
    part, so it is shared across every clip in a run."""
    global _WHISPER
    if _WHISPER is None:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            print("[subs] faster-whisper missing — run: pip install faster-whisper")
            return None
        _WHISPER = WhisperModel("base", device="cpu", compute_type="int8")
    return _WHISPER


def detect_speech_language(clip: Path) -> tuple[str | None, float]:
    """(language, probability) for a clip's speech, without transcribing it.

    `transcribe()` runs language detection eagerly and returns the segment
    generator lazily, so reading `info` and dropping the generator costs the
    detection pass alone — no decoding happens.
    """
    model = _whisper_model()
    if model is None:
        return None, 0.0
    try:
        _, info = model.transcribe(str(clip))
        return (info.language or "").lower(), float(info.language_probability or 0.0)
    except Exception as ex:
        print(f"[subs] language detection failed ({ex.__class__.__name__})")
        return None, 0.0


def _ass_ts(s: float) -> str:
    h, m = divmod(int(s), 3600); m, sec = divmod(m, 60)
    return f"{h:d}:{m:02d}:{sec:02d}.{int((s % 1) * 100):02d}"


def make_dynamic_captions(clip: Path, an: int, margin_v: int, fontsize: int,
                          translate: bool = True, play_res: tuple[int, int] | None = None):
    """
    Transcribe spoken words and write an .ass with word-by-word reveal where the
    active word is highlighted (the modern animated-caption look). Returns
    (ass_path, hook, transcript) where hook is the first spoken phrase (used for
    the title) and transcript is the full spoken text (fed to AI metadata), or
    (None, None, None) if faster-whisper isn't installed / there's no speech.

    Two domain passes wrap the decode:
      * `lol_kb.whisper_prompt()` biases decoding toward esports proper nouns, so
        Keria stops arriving as "Korea" in the first place;
      * `lol_kb.correct_words()` repairs what still slips through, on the word
        list rather than the joined text so every caption token keeps its timing.

    If the speech is one of TRANSLATE_LANGS (Korean), the clip is decoded with
    Whisper's translate task and the captions come out in English.

    `play_res` is the (width, height) the script's coordinates — MarginV and
    every font size — are written in; it must match the canvas the clip will be
    rendered onto, or libass stretches the whole script to fit. Defaults to the
    9:16 Short canvas, which is every layout except `whole`.
    """
    play_w, play_h = play_res or (W, H)
    model = _whisper_model()
    if model is None:
        return None, None, None

    bias = lol_kb.whisper_prompt()
    # `info` is populated before the generator is consumed, so this first call
    # has only run language detection at the point we read it.
    segments, info = model.transcribe(str(clip), word_timestamps=True,
                                      initial_prompt=bias)
    lang = (info.language or "").lower()
    prob = float(info.language_probability or 0.0)
    if translate and lang in TRANSLATE_LANGS and prob >= LANG_MIN_PROB:
        print(f"[subs] speech detected as '{lang}' ({prob:.0%}) — "
              f"captioning the English translation")
        segments, info = model.transcribe(str(clip), task="translate",
                                          language=lang, word_timestamps=True,
                                          initial_prompt=bias)
    elif lang and lang != "en":
        # Say which of the three reasons applied — "not translated" with no cause
        # is the kind of log line that costs an hour later.
        why = ("--no-translate" if not translate else
               f"below the {LANG_MIN_PROB:.0%} confidence bar"
               if lang in TRANSLATE_LANGS else "not in TRANSLATE_LANGS")
        print(f"[subs] speech detected as '{lang}' ({prob:.0%}) — "
              f"captioning verbatim ({why})")
        if lang in ("ko", "ja", "zh", "ru", "ar", "th", "hi"):
            print(f"[subs] warning: the caption style asks for Arial, which has "
                  f"no '{lang}' glyphs — verbatim captions may render as boxes")

    words = [(w.word.strip(), w.start, w.end)
             for seg in segments for w in (seg.words or []) if w.word.strip()]
    if not words:
        return None, None, None
    words = lol_kb.correct_words(words)

    # group into short phrases (max 5 words, split on >0.6s gaps)
    phrases, cur = [], []
    for w in words:
        if cur and (w[1] - cur[-1][2] > 0.6 or len(cur) >= 5):
            phrases.append(cur); cur = []
        cur.append(w)
    if cur:
        phrases.append(cur)

    ACCENT = "00D4FF"  # BGR: bright yellow highlight
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {play_w}\nPlayResY: {play_h}\n\n"
            "[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,OutlineColour,"
            "BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,"
            "BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
            # Viral (MrBeast/Hormozi) look: thicker Outline 6 + Shadow 3 so big
            # ALL-CAPS words stay legible over busy gameplay (was Outline 4 / Shadow 2).
            f"Style: Pop,Arial,{fontsize},&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,"
            f"100,100,0,0,1,6,3,{an},60,60,{margin_v},1\n\n"
            "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n")

    lines = []
    for phrase in phrases:
        for i, (_, ws, we) in enumerate(phrase):
            # Bridge to the next word for smoothness, but never linger more than
            # ~0.5s past the spoken word, so captions clear during silence.
            nxt = phrase[i + 1][1] if i + 1 < len(phrase) else None
            end = min(nxt, we + 0.5) if nxt is not None else we + 0.3
            end = max(end, ws + 0.1)
            parts = []
            for j, (wt, _, _) in enumerate(phrase[:i + 1]):
                wt = wt.upper()               # ALL-CAPS displayed text (viral style)
                if j == i:
                    # Active spoken word: accent gold + a brief ~100ms pop that
                    # scales down from 118%->100% so it snaps the eye. Pop kept
                    # modest so caps never spill past the reserved caption band.
                    parts.append(f"{{\\fscx118\\fscy118\\t(0,100,\\fscx100\\fscy100)"
                                 f"\\c&H{ACCENT}&}}{wt}{{\\c&HFFFFFF&}}")
                else:
                    parts.append(wt)          # already-revealed words: plain white
            lines.append(f"Dialogue: 0,{_ass_ts(ws)},{_ass_ts(end)},Pop,,0,0,0,,"
                         + " ".join(parts))

    ass = clip.with_suffix(".ass")
    ass.write_text(head + "\n".join(lines), encoding="utf-8")
    hook = " ".join(w for w, _, _ in phrases[0][:8])   # first phrase -> title hook
    transcript = " ".join(w for w, _, _ in words)
    return ass, hook, transcript


# ── facecam: finding it, and following it ────────────────────────────────────
# A single box for the whole clip was the original design and it does not
# survive contact with a real stream. Streamers switch scenes — webcam-large
# while they talk, game-large in champion select — so the median box is aimed at
# the webcam for part of the clip and at a corner of the game client for the
# rest. Measured on short_01_94s.mp4: face at t=1s and t=20s, game client at
# t=6s and t=14s, from one fixed rectangle.
#
# So the box moves. It is sampled like the gameplay pan is sampled and driven by
# the same sendcmd primitive, with one difference that matters: only x and y are
# animated. The crop's WIDTH AND HEIGHT MUST STAY CONSTANT — they set the output
# frame size, and a filter chain whose frame size changes mid-stream will not
# vstack. Size is therefore fixed once from the median detection and only the
# position follows.
FACECAM_FPS        = 2      # face-detection sampling rate (Haar is ~15ms/frame here)
FACECAM_SNAP_FRAC  = 0.10   # a jump beyond this frac of src_w is a scene change: cut
FACECAM_STEP_FRAC  = 0.30   # otherwise follow at up to this frac of src_w per second
FACECAM_DEAD_FRAC  = 0.01   # jitter under this frac of src_w does not move the box
FACECAM_EXPAND     = (2.4, 2.8)   # face box -> webcam box, (horizontal, vertical)
FACECAM_DW, FACECAM_DH = 640, 360  # detection resolution; boxes scale back to source


def _facecam_samples(video: Path, start: float, dur: int):
    """(times, boxes) of the largest face per sampled frame, in DETECTION px.

    `boxes[i]` is (x, y, w, h) or None when nothing was found at `times[i]`.
    Returns (None, None) when face detection is unavailable — an empty result
    would be indistinguishable from "looked and found nobody", and those two
    cases deserve different messages.
    """
    try:
        import cv2
    except ImportError:
        # Say it. Without this line the split layout silently renders as `full`
        # and the only symptom is a framing the user did not choose.
        print("[split] opencv missing — no face detection available "
              "(pip install opencv-python-headless)")
        return None, None
    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    dw, dh = FACECAM_DW, FACECAM_DH
    raw = subprocess.run(
        [_FFMPEG, "-ss", str(start), "-i", str(video), "-t", str(dur),
         "-vf", f"fps={FACECAM_FPS},scale={dw}:{dh}",
         "-pix_fmt", "bgr24", "-f", "rawvideo", "-"],
        capture_output=True).stdout
    nf = len(raw) // (dw * dh * 3)
    if nf == 0:
        return np.array([]), []
    frames = np.frombuffer(raw[:nf * dw * dh * 3], np.uint8).reshape(nf, dh, dw, 3)

    boxes: list = []
    for f in frames:
        gray = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, 1.1, 5, minSize=(40, 40))
        boxes.append(max(faces, key=lambda b: b[2] * b[3]) if len(faces) else None)
    times = np.arange(nf, dtype=float) / FACECAM_FPS
    return times, boxes


def _facecam_box_size(boxes, src_w: int, src_h: int) -> tuple[int, int]:
    """Fixed (w, h) of the webcam crop, from the median detected face."""
    found = np.array([b for b in boxes if b is not None], dtype=float)
    sx, sy = src_w / FACECAM_DW, src_h / FACECAM_DH
    fw, fh = float(np.median(found[:, 2])), float(np.median(found[:, 3]))
    ex, ey = FACECAM_EXPAND
    bw = min(int(fw * sx * ex) // 2 * 2, src_w // 2 * 2)
    bh = min(int(fh * sy * ey) // 2 * 2, src_h // 2 * 2)
    return max(2, bw), max(2, bh)


def _facecam_enough(boxes, nf: int) -> bool:
    """The original conservatism, unchanged: a face in at least a third of the
    samples. A wrong facecam is far worse than none, and the caller's fallback
    (`full`) is a framing that always works."""
    return sum(b is not None for b in boxes) >= max(2, nf // 3)


def detect_facecam(video: Path, start: float, dur: int, src_w: int, src_h: int):
    """
    Best-effort: find a streamer facecam box via face detection on sampled frames.
    Returns (x, y, w, h) in source pixels, or None. Expands the detected face to a
    webcam-sized box. Requires opencv (cv2).

    The STATIC answer — the median box over the window. `facecam_track` is what
    the split layout renders with; this stays because a short window (the cold-
    open teaser) has too few samples to be worth animating, and because it is
    the honest one-box summary when a caller just wants "where is the webcam".
    """
    times, boxes = _facecam_samples(video, start, dur)
    if boxes is None or len(boxes) == 0 or not _facecam_enough(boxes, len(boxes)):
        return None
    bw, bh = _facecam_box_size(boxes, src_w, src_h)
    found = np.array([b for b in boxes if b is not None], dtype=float)
    sx, sy = src_w / FACECAM_DW, src_h / FACECAM_DH
    cx = float(np.median(found[:, 0] + found[:, 2] / 2)) * sx
    cy = float(np.median(found[:, 1] + found[:, 3] / 2)) * sy
    return _facecam_clamp(cx, cy, bw, bh, src_w, src_h) + (bw, bh)


def _facecam_clamp(cx: float, cy: float, bw: int, bh: int,
                   src_w: int, src_h: int) -> tuple[int, int]:
    """Centre -> top-left, clamped inside the frame and floored to even.

    Flooring rather than rounding: `_even` rounds to nearest and could push a
    box that was just clamped to the edge back past it, which fails the render
    outright instead of looking slightly wrong.
    """
    x = int(max(0, min(cx - bw / 2, src_w - bw))) // 2 * 2
    y = int(max(0, min(cy - bh / 2, src_h - bh))) // 2 * 2
    return x, y


def facecam_track(video: Path, start: float, dur: int, src_w: int, src_h: int):
    """Facecam box that FOLLOWS the webcam. Returns ((x, y, w, h), script|None).

    The box is the starting position and the fixed crop size; `script` is a
    sendcmd file driving `crop@face`'s x and y, or None when the webcam never
    really moves (a stream with one static scene — most of them) so the caller
    renders a plain static crop exactly as before.

    Returns (None, None) when there is no reliable face, which the caller turns
    into the `full` fallback.
    """
    times, boxes = _facecam_samples(video, start, dur)
    if boxes is None or len(boxes) == 0 or not _facecam_enough(boxes, len(boxes)):
        return None, None
    bw, bh = _facecam_box_size(boxes, src_w, src_h)
    sx, sy = src_w / FACECAM_DW, src_h / FACECAM_DH

    snap = FACECAM_SNAP_FRAC * src_w
    dead = FACECAM_DEAD_FRAC * src_w
    max_step = FACECAM_STEP_FRAC * src_w / FACECAM_FPS

    # Seed on the first real detection so the clip does not open on a hold-over
    # from nothing, then walk forward holding through the gaps.
    first = next(b for b in boxes if b is not None)
    cur = np.array([(first[0] + first[2] / 2) * sx, (first[1] + first[3] / 2) * sy])
    xs, ys = [], []
    for b in boxes:
        if b is not None:
            tgt = np.array([(b[0] + b[2] / 2) * sx, (b[1] + b[3] / 2) * sy])
            d = float(np.hypot(*(tgt - cur)))
            if d > snap:
                # A scene change is a cut, not a journey. Sliding the crop across
                # the frame to catch up would put the viewer on a slow pan over
                # the game client — worse than the wrong box it is correcting.
                cur = tgt
            elif d > dead:
                cur = cur + (tgt - cur) * (min(d, max_step) / d)
        # b is None: hold. A dropped detection is usually a blink or a head turn,
        # and holding is right for both.
        x, y = _facecam_clamp(cur[0], cur[1], bw, bh, src_w, src_h)
        xs.append(x); ys.append(y)

    box0 = (xs[0], ys[0], bw, bh)
    if float(np.ptp(xs)) < 2.0 and float(np.ptp(ys)) < 2.0:
        return box0, None                       # static scene: no script needed
    script = CLIPS_DIR / f".face_{int(start)}_{bw}.cmd"
    _write_facecam_sendcmd(times, np.array(xs, float), np.array(ys, float), script)
    print(f"[split] facecam follows the scene "
          f"(x {min(xs)}-{max(xs)}, y {min(ys)}-{max(ys)})")
    return box0, script


def _write_facecam_sendcmd(times: np.ndarray, xs: np.ndarray, ys: np.ndarray,
                           path: Path) -> Path:
    """sendcmd script driving `crop@face`'s x and y together.

    Densified to CMD_FPS for the same reason `_write_sendcmd` does it: sendcmd
    steps rather than interpolates, so the smoothness has to be in the script.
    Both coordinates are issued in one interval — two intervals at the same
    timestamp would be a diagonal made of two axis-aligned jumps.
    """
    if len(times) >= 2:
        dense_t = np.arange(float(times[0]), float(times[-1]), 1.0 / CMD_FPS)
        dense_x = np.interp(dense_t, times, xs)
        dense_y = np.interp(dense_t, times, ys)
    else:
        dense_t, dense_x, dense_y = times, xs, ys
    lines = [f"{t:.3f} crop@face x {int(round(x)) // 2 * 2}, "
             f"crop@face y {int(round(y)) // 2 * 2};"
             for t, x, y in zip(dense_t, dense_x, dense_y)]
    path.write_text("\n".join(lines) + "\n")
    return path


def has_existing_captions(video: Path, start: float, dur: int, dims) -> bool:
    """
    True if the source already has captions, so we don't add a duplicate layer:
      1) a real subtitle stream (reliable), or
      2) burned-in text in the lower-centre band (best-effort, via opencv).
    """
    probe = _FFMPEG.replace("ffmpeg.exe", "ffprobe.exe") if _FFMPEG_DIR else "ffprobe"
    subs = subprocess.run([probe, "-v", "error", "-select_streams", "s",
                           "-show_entries", "stream=index", "-of", "csv=p=0", str(video)],
                          capture_output=True, text=True).stdout.strip()
    if subs:
        return True
    try:
        import cv2
    except ImportError:
        return False
    src_w, src_h = dims
    dw, dh = 480, int(480 * src_h / src_w)
    raw = subprocess.run(
        [_FFMPEG, "-ss", str(start), "-i", str(video), "-t", str(dur),
         "-vf", f"fps=1,scale={dw}:{dh}", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
        capture_output=True).stdout
    nf = len(raw) // (dw * dh)
    if nf < 3:
        return False
    frames = np.frombuffer(raw[:nf * dw * dh], np.uint8).reshape(nf, dh, dw)
    band = frames[:, int(dh * 0.30):, :]          # captions live anywhere in the lower 70%
    hits = 0
    for f in band:
        grad = cv2.morphologyEx(f, cv2.MORPH_GRADIENT, np.ones((2, 2), np.uint8))
        _, th = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        # join characters into word/line blobs, then look for wide centred text lines
        joined = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((3, 15), np.uint8))
        cnts, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            x, _, w, h = cv2.boundingRect(c)
            cx = x + w / 2
            if w > dw * 0.25 and 6 < h < dh * 0.18 and w / h > 4 and 0.2 < cx / dw < 0.8:
                hits += 1
                break
    return hits >= max(2, int(nf * 0.6))           # text band present in most frames


def _hashtags(platform: str) -> str:
    yt = "#Shorts #LeagueOfLegends #LoL #Gaming"
    tk = "#fyp #lol #leagueoflegends #gaming #clips"
    return {"youtube": yt, "tiktok": tk, "both": f"{yt}\n{tk}"}[platform]


# ── Shorts description SEO (docs/reference/shorts-description-seo.md) ────────
# YouTube truncates a Short's description at ~100-125 characters behind "...more",
# and most viewers never expand it. Everything that has to be *seen* — the primary
# keyword and the hashtags that categorise the video — must fit in that window;
# everything that only has to be *indexed* can live below it, out to 5,000 chars.
# Hashtags belong here rather than in the title, which needs its characters for a
# clickable headline.
SHORTS_PREVIEW_CHARS = 125   # hard budget for line 1 (keyword + hashtags)
SHORTS_DESC_MAX      = 5000  # YouTube's description ceiling
SHORTS_CTA_START     = 126   # links/CTAs belong in the 126-500 band
SHORTS_TAGS_MIN      = 3     # 3-5 hashtags: #Shorts + 1 broad + 2-3 niche
SHORTS_TAGS_MAX      = 5


def shorts_hashtags(transcript: str | None, platform: str) -> list[str]:
    """The 3-5 tag set, ordered #Shorts -> niche -> broad.

    #Shorts first because it is what puts the video in the Shorts feed; the
    niche tags come from what the caster actually said (`lol_kb.niche_hashtags`),
    so a Faker clip gets #Faker #T1 rather than another generic #Gaming.
    """
    if platform == "tiktok":
        return ["fyp", "LeagueOfLegends", "LoL", "Gaming"]
    niche = lol_kb.niche_hashtags(transcript or "", limit=SHORTS_TAGS_MAX - 2)
    tags = ["Shorts", *niche, "Gaming"]          # broad category tag goes last
    out: list[str] = []
    for t in tags:                               # dedupe, preserve order
        if t.lower() not in {o.lower() for o in out}:
            out.append(t)
    return out[:SHORTS_TAGS_MAX]


def _front_line(hook: str, tags: list[str]) -> str:
    """Line 1: primary keyword + hashtags, guaranteed <= SHORTS_PREVIEW_CHARS.

    The hook is trimmed at a word boundary rather than the tags being dropped —
    the tags are what categorise the video, so they are the part that must
    survive. Returns the tags alone if even one word will not fit beside them.
    """
    tagstr = " ".join(f"#{t}" for t in tags)
    room = SHORTS_PREVIEW_CHARS - len(tagstr) - 1
    hook = " ".join((hook or "").split()).rstrip(" .")
    if room < 1:
        return tagstr[:SHORTS_PREVIEW_CHARS]
    if len(hook) > room:
        hook = hook[:room].rsplit(" ", 1)[0].rstrip(" ,;:-—")
    return f"{hook} {tagstr}".strip() if hook else tagstr


def build_description(hook: str, body: str, tags: list[str],
                      endcard: str | None = None, credit: str | None = None) -> str:
    """Assemble a Shorts description to the front-loaded structure.

        line 1 (<=125 chars) : primary keyword + 3-5 hashtags   <- the only part seen
        band 126-500         : CTA / link
        remainder            : SEO body, secondary keywords, credits

    `body` is the AI's 1-2 sentence description; `endcard` is the affiliate CTA
    already burned into the video, repeated here so the pinned-comment route has
    a second surface.
    """
    parts = [_front_line(hook, tags)]
    cta = endcard.strip() if endcard else "Full match link in the pinned comment."
    parts.append(cta)
    if body:
        parts.append(" ".join(body.split()))
    if credit:
        parts.append(credit.strip())
    parts.append("Clipped from the full VOD. All game footage belongs to Riot Games.")
    return "\n\n".join(p for p in parts if p)[:SHORTS_DESC_MAX]


# ── AI metadata from the clip transcript (idea from MoneyPrinter's gpt.py) ───
# llama3.2:3b: small + non-thinking on purpose — runs in ~10s per clip on this
# CPU box, where the 9b thinking models take minutes and stall the render loop.
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")


# A JSON *schema*, not `"format": "json"`. On this CPU box a 3B model asked
# politely for JSON returns prose and half-objects; constraining the decoder so
# invalid tokens are never sampled took verdict parse rates from 0/4 to 4/4 in
# the sibling apex-trader work. Same lesson, same fix.
_META_SCHEMA = {
    "type": "object",
    "properties": {
        "title":       {"type": "string"},
        "hook":        {"type": "string"},
        "description": {"type": "string"},
        "hashtags":    {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "hook", "description", "hashtags"],
}

# The transcript is unscripted speech, so it swears; the title and the caption
# are the two lines YouTube shows before anyone taps. A shipped clip was
# captioned "Fucked comeback" because the model echoed the commentary it was
# handed and nothing downstream looked. Profanity in a Short's title is also an
# ad-suitability problem, not only a taste one.
#
# Word-boundary matched, not substring: "classic" and "assist" contain shorter
# swears and a filter that trips on them is worse than no filter.
PROFANITY = frozenset({
    "fuck", "fucks", "fucked", "fucking", "fucker", "shit", "shits",
    "shitty", "bitch", "bitches", "cunt", "dick", "cock", "bastard",
    "asshole", "motherfucker", "nigga", "retard", "retarded", "whore", "slut",
})


def profane_words(text: str) -> list[str]:
    """Which PROFANITY terms `text` actually uses, in order, deduplicated."""
    out: list[str] = []
    for w in re.findall(r"[A-Za-z]+", text or ""):
        lw = w.lower()
        if lw in PROFANITY and lw not in out:
            out.append(lw)
    return out


def strip_profanity(text: str) -> str:
    """Drop profane words and tidy the whitespace/punctuation they leave.

    Used on the NON-AI fallback headline, which is raw transcribed speech and
    has no second draft to fall back to — unlike the model's answer, which gets
    told what it did wrong and asked again.
    """
    kept = [w for w in (text or "").split()
            if re.sub(r"[^A-Za-z]", "", w).lower() not in PROFANITY]
    return re.sub(r"\s{2,}", " ", " ".join(kept)).strip(" ,.-—").strip()


def _ollama_metadata(transcript: str, idx: int) -> dict | None:
    """Title/hook/description/tags for one clip via the local Ollama instance.
    Strictly best-effort: any failure (Ollama down, bad JSON, empty title)
    returns None and callers fall back to the hook-based title.

    The prompt carries two things the model cannot know on its own: a briefing
    on whichever players/teams/events this clip actually mentions
    (`lol_kb.context_brief`), and the Shorts description rules that decide what
    has to fit in the first 125 characters."""
    import json
    import urllib.request
    brief = lol_kb.context_brief(transcript)

    def _prompt(scold: list[str] | None = None, swore: bool = False) -> str:
        # The briefing is what the model is ALLOWED to know. When it is empty the
        # old prompt still said "lead with the player or team name" and "use the
        # names from the briefing", so a clip the KB could not identify asked a 3B
        # model to name names with nothing to name them from — and it reliably
        # reached for the two it knows best, T1 and Faker. Naming is now opt-in on
        # the briefing existing.
        known = (f"Who and what this clip is about:\n{brief}\n\n" if brief else
                 "No briefing is available for this clip: the knowledge base did "
                 "not recognise anybody in it. Do NOT name any player, team, "
                 "league or tournament — not one, however likely it seems. Title "
                 "the PLAY itself ('insane baron steal', 'flawless teamfight').\n\n")
        retry = ""
        if scold:
            retry = ("\nYour previous answer named " + ", ".join(scold) +
                     ", which appears nowhere in the commentary or the briefing. "
                     "That is invented. Rewrite without those names.\n")
        if swore:
            retry += ("\nYour previous answer swore. The commentary is "
                      "unscripted and does swear, but the title and description "
                      "are published text — describe what was said without "
                      "repeating the profanity.\n")
        return (
            "You write YouTube Shorts metadata for League of Legends esports "
            "highlight clips. Spoken commentary from this clip:\n"
            f'"{transcript[:1200]}"\n\n'
            + known
            + "Fields:\n"
            "- title: catchy, under 70 characters, no emoji, NO hashtags, faithful "
            "to the commentary. Lead with a player or team name ONLY if the "
            "commentary or the briefing names one.\n"
            "- hook: one short phrase, under 60 characters, leading with the main "
            "topic and why it is worth watching. This is the only text a viewer "
            "sees before tapping 'more', so no throat-clearing.\n"
            "- description: 2-3 sentences of context for search — who, what "
            "happened, which team/event. Use ONLY names that appear above.\n"
            "- hashtags: 4-6 single words, no # symbol, specific over generic\n\n"
            "Only state what the commentary or the briefing actually says. Never "
            "invent a year, a score, a tournament stage, or a result — a made-up "
            "detail in a published description is worse than a vague one.\n"
            "The same rule binds names hardest of all: never state a player, "
            "team, region or tournament that does not appear above. A clip "
            "credited to the wrong team is worse than a clip credited to nobody, "
            "so when you do not know who is playing, describe the play."
            + retry)

    def _ask(prompt: str) -> dict | None:
        body = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
                "format": _META_SCHEMA,
                "options": {"temperature": 0.4, "num_predict": 320}}
        req = urllib.request.Request(
            f"{OLLAMA_HOST}/api/generate", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = json.loads(json.load(r)["response"])
        title = str(raw.get("title", "")).strip().strip('"')
        title = re.sub(r"#\w+", "", title).strip()  # hashtags belong in the description
        if not title:
            return None
        return {"title": title,
                "hook": str(raw.get("hook", "")).strip().strip('"'),
                "description": str(raw.get("description", "")).strip(),
                "tags": [t for t in (re.sub(r"\W", "", str(t))
                                     for t in raw.get("hashtags", [])) if t][:6]}

    try:
        scold: list[str] | None = None
        swore = False
        # Two attempts, then honest fallback. Prompting lowers the invention rate
        # but cannot zero it, and the failure is silent — fluent, confident, wrong.
        # The vocabulary of nameable things is closed, so an ungrounded name is
        # detectable after the fact; see lol_kb.ungrounded_names.
        for attempt in (1, 2):
            meta = _ask(_prompt(scold, swore))
            if meta is None:
                return None
            # The hook is checked too, and that is not padding: `write_metadata`
            # feeds it to `build_description` as the first line of the caption,
            # which is the one piece of text every viewer reads. An unchecked
            # hook is a published claim.
            published = f"{meta['title']} {meta['hook']} {meta['description']}"
            bad = (lol_kb.ungrounded_names(published, transcript, brief)
                   + lol_kb.ungrounded_claims(published, transcript, brief))
            # Swearing is checked on the same pass and scolded the same way. It
            # is not an invention — the commentary really did say it — so it
            # gets its own sentence in the retry rather than being folded into
            # "that is invented", which would be a lie the model then reasons
            # from.
            swears = profane_words(published)
            if not bad and not swears:
                # Tags are dropped individually rather than failing the whole
                # answer: one invented tag does not make the title wrong.
                meta["tags"] = [
                    t for t in meta["tags"]
                    if not lol_kb.ungrounded_names(t, transcript, brief)
                    and not lol_kb.ungrounded_claims(t, transcript, brief)]
                print(f"[meta {idx}] AI title: {meta['title']}")
                return {"title": meta["title"][:90],
                        "hook": (meta["hook"] or meta["title"])[:120],
                        "description": meta["description"][:800],
                        "tags": meta["tags"]}
            faults = ([f"invented {', '.join(bad)}"] if bad else []) + \
                     ([f"swore ({', '.join(swears)})"] if swears else [])
            print(f"[meta {idx}] attempt {attempt} {' and '.join(faults)}"
                  + (" — retrying" if attempt == 1 else " — using hook title"))
            scold = bad
            swore = bool(swears)
        return None
    except Exception as ex:
        print(f"[meta {idx}] Ollama unavailable ({ex.__class__.__name__}) — using hook title")
        return None


def write_metadata(clip: Path, title_base: str, idx: int, platform: str,
                   hook: str | None, meta: dict | None = None,
                   transcript: str | None = None, endcard: str | None = None,
                   title_override: str | None = None):
    """Write a sidecar .txt with a ready-to-paste title + caption for the
    platform(s). TITLE stays the first section — the dashboard reads it.
    With AI meta, add a TAGS section that --draft feeds to the YouTube API.

    The clip index is bracketed `[3]`, never `(#3)`: YouTube parses a `#` in a
    title as a hashtag, which both burns title characters and drops the clip
    into a `#3` feed. Hashtags live in the description now — see
    `build_description`.

    `title_override` replaces the headline outright and is what the whole-video
    mode uses to title its parts "Part 1"…"Part N": a series is navigated by its
    ordinal, and an AI-written headline per 61s slice would fight the ordering
    the parts exist to express. The caption is still built normally, so a part
    keeps its description, hashtags and CTA."""
    tags = shorts_hashtags(transcript, platform)
    if meta:
        title = f"{meta['title']} [{idx}]"
        caption = build_description(meta.get("hook") or meta["title"],
                                    meta["description"], tags, endcard=endcard)
        body = (f"TITLE:\n{title_override or title}\n\nCAPTION:\n{caption}\n\n"
                f"TAGS:\n{', '.join(meta['tags'])}\n")
    else:
        # `hook` is the first spoken phrase, verbatim. It reaches the title and
        # the first line of the caption, so it is stripped rather than trusted;
        # if swearing was all it had, fall back to the run's base title.
        headline = strip_profanity((hook or "").strip()) or title_base.strip()
        headline = headline.rstrip(".!?")
        title = f"{headline} 🔥 [{idx}]"
        caption = build_description(headline,
                                    f"{headline} — League of Legends highlight.",
                                    tags, endcard=endcard)
        body = f"TITLE:\n{title_override or title}\n\nCAPTION:\n{caption}\n"
    clip.with_suffix(".txt").write_text(body, encoding="utf-8")


def _read_sidecar(clip: Path) -> dict:
    """Parse a sidecar .txt back into {'TITLE': ..., 'CAPTION': ..., 'TAGS': ...}
    so --draft uploads reuse the (possibly AI-written) metadata."""
    out, cur = {}, None
    try:
        for ln in clip.with_suffix(".txt").read_text(encoding="utf-8").splitlines():
            head = ln.strip().rstrip(":").upper()
            if ln.strip().endswith(":") and head in ("TITLE", "CAPTION", "TAGS"):
                cur = head
                out[cur] = ""
            elif cur is not None:
                out[cur] = f"{out[cur]}\n{ln}".strip()
    except OSError:
        pass
    return out


# ── 3.6 AI-edited thumbnails ─────────────────────────────────────────────────
# Fully automatic: pick the sharpest/most colorful frame near the hype moment,
# grade it (saturation/contrast/vignette), punch in, and overlay a 2-3 word
# ALL-CAPS hook (culture word bank -> Ollama -> title fallback). Design rules
# from CTR research live in obsidian1 Sources/Web (LoL thumbnail design note).
THUMB_W, THUMB_H = 1080, 1920  # vertical 9:16 Shorts cover — matches the clips
THUMB_HOOK_WORDS = 3          # research says <=3-5 words; cap hard for legibility
THUMB_GOLD = (255, 200, 0)    # last-word accent: gold on dark is the LoL palette

# Culture terms with click-pull, only used when they appear in the title or the
# spoken commentary — the hook must stay faithful to what actually happens.
THUMB_HOOKS = ("PENTAKILL", "QUADRA", "BARON STEAL", "BARON", "OUTPLAY",
               "CLUTCH", "ACE", "1V5", "1V4", "1V3", "200 IQ", "BACKDOOR",
               "COMEBACK", "GAME WINNER", "THROW", "INSANE", "PERFECT")


def _thumb_font(size: int):
    """PIL wants the raw font path — FONT is drawtext-escaped for ffmpeg."""
    from PIL import ImageFont
    return ImageFont.truetype(FONT.replace(R"\:", ":"), size)


SCORE_W, SCORE_H = 640, 360    # scoring resolution — see _pick_frame


def _frame_at(video: Path, t: float, width: int | None = None):
    """One frame at t seconds as a PIL image (None on failure).

    `width` decodes a scaled-down frame instead of full res. Scoring candidates
    does not need 1920x1080 — `_frame_score` resizes to 320x180 immediately — and
    PNG-encoding six full-res frames through a pipe was costing more than it
    saved. The winner is re-fetched at full res by the caller.
    """
    import io
    from PIL import Image
    vf = ["-vf", f"scale={width}:-2:flags=bilinear"] if width else []
    r = subprocess.run([_FFMPEG, "-v", "error", "-ss", f"{max(0, t):.2f}",
                        "-i", str(video), "-frames:v", "1", *vf,
                        "-f", "image2pipe", "-vcodec", "png", "-"],
                       capture_output=True)
    return Image.open(io.BytesIO(r.stdout)).convert("RGB") if r.stdout else None


def _frame_score(img) -> float:
    """Sharp + colorful + well-exposed wins. Edge energy favors ability-VFX
    action frames, colorfulness rejects grey death/shop screens, and the
    exposure term rejects fade-to-black transitions."""
    from PIL import ImageFilter, ImageStat
    small = img.resize((320, 180))
    edges = ImageStat.Stat(small.convert("L").filter(ImageFilter.FIND_EDGES)).mean[0]
    mean = ImageStat.Stat(small).mean
    colorfulness = (abs(mean[0] - mean[1]) + abs(mean[1] - mean[2]) + abs(mean[2] - mean[0])) / 3
    exposure = 1.0 - abs(sum(mean) / (3 * 255) - 0.45) * 2
    return edges + colorfulness * 0.8 + exposure * 30


def _pick_frame(video: Path, times):
    """Best-scoring frame among candidate timestamps (None if all fail).

    Two passes on purpose: score every candidate at SCORE_W (cheap), then decode
    ONLY the winner at full resolution. Scoring all of them at full res meant
    PNG-encoding six 1920x1080 frames through a pipe to look at 320x180 of each.
    Scores shift a hair versus full-res scoring because ffmpeg does the first
    downscale instead of PIL, but this is a heuristic picking between frames of
    the same clip, not a measurement.
    """
    best_t, best_s = None, -1.0
    for t in times:
        img = _frame_at(video, t, width=SCORE_W)
        if img is not None:
            s = _frame_score(img)
            if s > best_s:
                best_t, best_s = t, s
    return _frame_at(video, best_t) if best_t is not None else None


def _ollama_thumb_hook(text: str) -> str | None:
    """2-3 word ALL-CAPS hook via local Ollama; None when it's down or rambles."""
    import json
    import urllib.request
    prompt = (
        "You write 2-3 word ALL-CAPS YouTube thumbnail hooks for League of "
        f'Legends esports highlights. Clip title/commentary: "{text[:300]}"\n'
        'Reply JSON only: {"hook": "..."} — 2-3 words, no punctuation, no emoji, '
        "hype but professional (think PENTAKILL, BARON STEAL, FAKER OUTPLAY).")
    body = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
            "format": "json", "options": {"temperature": 0.3, "num_predict": 40}}
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/generate",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            hook = json.loads(json.load(r)["response"]).get("hook", "")
        words = re.sub(r"[^A-Za-z0-9 ]", "", str(hook)).upper().split()
        if 1 <= len(words) <= THUMB_HOOK_WORDS and all(len(w) < 14 for w in words):
            return " ".join(words)
    except Exception:
        pass
    return None


def _hook_text(title: str, transcript: str | None) -> str:
    """Hook priority: culture term actually present in the clip -> Ollama ->
    the first words of the title. Never empty."""
    hay = f"{title} {transcript or ''}".upper()
    for h in THUMB_HOOKS:
        if h in hay:
            return h
    ai = _ollama_thumb_hook(transcript or title)
    if ai:
        return ai
    words = re.sub(r"[^A-Za-z0-9 ]", "", title).upper().split()
    return " ".join(words[:THUMB_HOOK_WORDS]) or "MUST SEE"


# -- creative vertical thumbnail: helpers ------------------------------------
EMOJI_FONT = next((p for p in (
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/noto/NotoColorEmoji.ttf",
) if os.path.exists(p)), None)

# themed emoji picked from the hook, longest/most-specific keys first
THUMB_EMOJI = (("PENTA", "💥"), ("QUADRA", "💥"), ("ACE", "💥"), ("STEAL", "🔥"),
               ("BARON", "🐉"), ("DRAGON", "🐉"), ("BACKDOOR", "🚪"),
               ("CLUTCH", "⚔️"), ("OUTPLAY", "⚔️"), ("1V", "⚔️"), ("IQ", "🧠"),
               ("THROW", "💀"), ("COMEBACK", "🔥"), ("INSANE", "🔥"))


def _cover(img, size):
    """Resize + center-crop so `img` fully covers `size` (no letterboxing)."""
    from PIL import Image
    tw, th = size
    w, h = img.size
    s = max(tw / w, th / h)
    r = img.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)
    x, y = (r.width - tw) // 2, (r.height - th) // 2
    return r.crop((x, y, x + tw, y + th))


def _rounded_mask(size, radius):
    from PIL import Image, ImageDraw
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1),
                                        radius=radius, fill=255)
    return m


def _radial(size, cx, cy, r_in, r_out, inner=255, outer=0):
    """L-mode radial ramp: `inner` inside r_in, fading to `outer` past r_out."""
    from PIL import Image
    w, h = size
    yy, xx = np.ogrid[:h, :w]
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    t = np.clip((dist - r_in) / max(1, (r_out - r_in)), 0, 1)
    return Image.fromarray((inner + (outer - inner) * t).astype(np.uint8), "L")


def _wrap_hook(draw, text, fnt, max_w):
    """Greedy word-wrap of the hook to fit `max_w` px per line."""
    words, lines, cur = text.split(), [], ""
    for wd in words:
        t = (cur + " " + wd).strip()
        if draw.textlength(t, font=fnt) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    return lines


def _burst(size, cx, cy, color, n=18, seed=0):
    """Transparent layer of soft radial light rays fanning out from (cx, cy)."""
    import math
    from PIL import Image, ImageDraw, ImageFilter
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    R = max(size) * 1.4
    base = (seed * 2.399963) % (2 * math.pi)     # golden-angle phase per clip
    for i in range(n):
        a = base + i * (2 * math.pi / n)
        p1 = (cx + R * math.cos(a - 0.16), cy + R * math.sin(a - 0.16))
        p2 = (cx + R * math.cos(a + 0.16), cy + R * math.sin(a + 0.16))
        d.polygon([(cx, cy), p1, p2], fill=color + (26,))
    return layer.filter(ImageFilter.GaussianBlur(6))


def _pick_emoji(hook: str) -> str:
    h = hook.upper()
    for key, em in THUMB_EMOJI:
        if key in h:
            return em
    return "🔥"


def _emoji_img(ch: str, px: int):
    """A color emoji as an RGBA image ~px tall (None if the font is missing)."""
    if not EMOJI_FONT:
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont
        f = ImageFont.truetype(EMOJI_FONT, 109)     # NotoColorEmoji: fixed strike
        layer = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((80, 80), ch, font=f, embedded_color=True,
                                   anchor="mm")
        bbox = layer.getbbox()
        if not bbox:
            return None
        layer = layer.crop(bbox)
        s = px / max(layer.size)
        return layer.resize((max(1, int(layer.width * s)),
                             max(1, int(layer.height * s))), Image.LANCZOS)
    except Exception:
        return None


def _paste_emoji(base, em, cx, cy, tilt=0):
    """Alpha-composite an emoji centered at (cx, cy) with a soft glow halo."""
    if em is None:
        return
    from PIL import Image, ImageFilter
    if tilt:
        em = em.rotate(tilt, expand=True, resample=Image.BICUBIC)
    halo = Image.new("RGBA", base.size, (0, 0, 0, 0))
    halo.alpha_composite(em, (cx - em.width // 2, cy - em.height // 2))
    base.alpha_composite(halo.filter(ImageFilter.GaussianBlur(18)))
    base.alpha_composite(em, (cx - em.width // 2, cy - em.height // 2))


def _compose_thumb(img, hook: str, out: Path, seed_key: str | None = None):
    """Compose a creative vertical 9:16 Shorts thumbnail (1080x1920) from a clean
    landscape source frame — an ACTUAL edited picture, not a captioned screenshot:

      • ambient backdrop  — the frame blown up to fill 9:16, blurred + dimmed
      • energy            — a warm spotlight + gold light-ray burst behind the card
      • hero card         — the graded, punched-in frame as a white-bordered,
                            glowing, drop-shadowed, slightly tilted 'sticker'
      • emoji badge        — 🔥/💥/⚔️/🐉 chosen from the hook, filling the top
      • hook               — 2-3 words, wrapped, ALL-CAPS, heavy stroke, gold accent
                            word + gold underline, over a bottom scrim
      • finish            — film grain + vignette for a processed, edited feel

    Per-clip variation (tilt direction, ray phase, grain) is seeded from
    `seed_key` (defaults to the output name) so a batch never looks copy-pasted.
    Stays a JPEG well under YouTube's 2 MB cap.
    """
    import hashlib
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
    seed = int(hashlib.md5((seed_key or str(out)).encode()).hexdigest()[:8], 16)
    tilt = -3.2 if seed % 2 else 3.2
    W, H, gold = THUMB_W, THUMB_H, THUMB_GOLD
    hx, hy = W // 2, int(H * 0.43)

    # 1) ambient backdrop: the frame blown up to fill 9:16, blurred + dimmed
    bg = _cover(img, (W, H)).filter(ImageFilter.GaussianBlur(55))
    bg = ImageEnhance.Brightness(bg).enhance(0.5)
    bg = ImageEnhance.Color(bg).enhance(1.25)
    # 2) warm spotlight + gold ray burst where the hero card will land
    spot = _radial((W, H), hx, hy, 120, 1000, inner=120, outer=0)
    bg = Image.composite(ImageEnhance.Brightness(bg).enhance(1.7), bg, spot)
    bg = bg.convert("RGBA")
    bg.alpha_composite(_burst((W, H), hx, hy, gold, seed=seed))

    # 3) hero card: graded, punched-in frame as a bordered, glowing, tilted sticker
    hero = ImageEnhance.Color(img).enhance(1.4)
    hero = ImageEnhance.Contrast(hero).enhance(1.14)
    hero = ImageEnhance.Sharpness(hero).enhance(1.7)
    w, h = hero.size
    cw, ch = int(w / 1.14), int(h / 1.14)
    hero = hero.crop(((w - cw) // 2, (h - ch) // 2, (w + cw) // 2, (h + ch) // 2))
    sw, sh = hero.size                           # keep the card landscape even if the
    if sh / sw > 0.62:                           # source frame is tall -> crop to 16:9
        nh = int(sw * 9 / 16)
        hero = hero.crop((0, (sh - nh) // 2, sw, (sh + nh) // 2))
    HW = 1000
    HH = max(1, int(HW * hero.height / hero.width))
    hero = hero.resize((HW, HH), Image.LANCZOS)
    rad = 34
    card = Image.new("RGBA", (HW, HH), (0, 0, 0, 0))
    card.paste(hero, (0, 0), _rounded_mask((HW, HH), rad))
    ImageDraw.Draw(card).rounded_rectangle((0, 0, HW - 1, HH - 1), radius=rad,
                                           outline=(255, 255, 255, 255), width=10)
    card = card.rotate(tilt, expand=True, resample=Image.BICUBIC)
    gx, gy = hx - card.width // 2, hy - card.height // 2
    alpha = card.split()[3]
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))          # gold glow behind card
    sil = Image.new("RGBA", card.size, (0, 0, 0, 0))
    sil.paste(gold + (255,), (0, 0), alpha)
    glow.alpha_composite(sil, (gx, gy))
    bg.alpha_composite(glow.filter(ImageFilter.GaussianBlur(34)))
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))        # drop shadow
    ssil = Image.new("RGBA", card.size, (0, 0, 0, 0))
    ssil.paste((0, 0, 0, 200), (0, 0), alpha)
    shadow.alpha_composite(ssil, (gx + 12, gy + 26))
    bg.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(22)))
    bg.alpha_composite(card, (gx, gy))

    # 4) themed emoji badge fills the top dead-space, tilted opposite the card
    _paste_emoji(bg, _emoji_img(_pick_emoji(hook), 240),
                 W // 2, int(H * 0.135), tilt=-tilt)

    out_img = bg.convert("RGB")

    # 5) bottom scrim so the hook always reads over the busy backdrop
    ramp = (np.clip((np.arange(H) - H * 0.52) / (H * 0.48), 0, 1) * 210).astype(np.uint8)
    grad = np.repeat(ramp[:, None], W, axis=1)
    out_img = Image.composite(Image.new("RGB", (W, H), (8, 8, 12)), out_img,
                              Image.fromarray(grad))

    # 6) hook — big, wrapped to <=2 lines, ALL-CAPS, heavy stroke, gold accent word
    d = ImageDraw.Draw(out_img)
    size = 210
    while size > 96:
        f = _thumb_font(size)
        lines = _wrap_hook(d, hook, f, W - 120)
        if len(lines) <= 2 and max(d.textlength(l, font=f) for l in lines) <= W - 110:
            break
        size -= 10
    f = _thumb_font(size)
    lines = _wrap_hook(d, hook, f, W - 120)
    lh = int(size * 1.06)
    y = int(H * 0.90) - lh * len(lines)
    stroke = max(6, size // 8)
    flat, wi = hook.split(), 0
    for ln in lines:
        x = (W - d.textlength(ln, font=f)) / 2
        for wd in ln.split():
            last = wi == len(flat) - 1          # accent the final (or only) word gold
            d.text((x, y), wd, font=f, fill=gold if last else (255, 255, 255),
                   stroke_width=stroke, stroke_fill=(0, 0, 0))
            x += d.textlength(wd + " ", font=f)
            wi += 1
        y += lh
    uw = min(W - 160, max(280, int(max(d.textlength(l, font=f) for l in lines) * 0.5)))
    uy = int(H * 0.905)
    d.rounded_rectangle(((W - uw) // 2, uy, (W + uw) // 2, uy + 12), radius=6, fill=gold)

    # 7) film grain + vignette for a processed, edited feel
    rng = np.random.default_rng(seed)
    noise = rng.integers(-10, 11, (H, W, 1)).repeat(3, axis=2).astype(np.int16)
    out_img = Image.fromarray(
        np.clip(np.asarray(out_img).astype(np.int16) + noise, 0, 255).astype(np.uint8),
        "RGB")
    vig = _radial((W, H), W // 2, int(H * 0.42), int(H * 0.30), int(H * 0.75),
                  inner=255, outer=70)
    out_img = Image.composite(out_img, ImageEnhance.Brightness(out_img).enhance(0.5), vig)

    out_img.save(out, "JPEG", quality=90)
    print(f"[thumb] {out}")


def make_thumbnail(video: Path, start: float, dur: int, peak_pos: float,
                   idx: int, clip_out: Path, title: str, transcript: str | None):
    """AI thumbnail for a freshly cut clip, taken from the CLEAN source video
    (no burned captions): sample frames around the audio spike, keep the best.
    Never fails the render — a clip without a thumb beats no clip."""
    try:
        peak = start + peak_pos * dur
        img = _pick_frame(video, [max(start, peak + o)
                                  for o in (-2.5, -1.5, -0.5, 0.5, 1.5, 2.5)])
        if img is None:
            print(f"[thumb {idx}] no frame extracted — skipped")
            return None
        out = clip_out.with_name(clip_out.stem + "_thumb.jpg")
        _compose_thumb(img, _hook_text(title, transcript), out)
        return out
    except Exception as ex:
        print(f"[thumb {idx}] failed ({ex.__class__.__name__}: {ex})")
        return None


def make_hero_thumbnail(video: Path, moments, dims, title: str,
                        transcript: str | None, out_path: Path,
                        *, peak_pos: float, clip_len: int):
    """ONE hero thumbnail for the whole source, composed like a per-clip thumb.

    Samples frames around the audio peak of each hype moment off the CLEAN source
    (no burned captions) and keeps the single best-scoring frame (sharp + colorful
    + well-exposed) — i.e. the most thumbnail-worthy instant of the video. This
    picks by frame score across the moments rather than the raw audio rank because
    ``find_hype_moments`` returns its moments time-sorted, and visual score is a
    better predictor of a compelling thumbnail than loudness alone. Never fails the
    job — returns ``out_path`` or ``None``.
    """
    if not moments:
        return None
    try:
        cands = []
        for m in moments:
            peak = m + peak_pos * clip_len           # where the spike sits in each clip
            cands += [max(0.0, peak + o) for o in (-1.5, -0.5, 0.5, 1.5)]
        img = _pick_frame(video, cands)
        if img is None:
            print("[hero] no frame extracted — skipped")
            return None
        _compose_thumb(img, _hook_text(title, transcript), out_path)
        print(f"[hero] {out_path}")
        return out_path
    except Exception as ex:
        print(f"[hero] failed ({ex.__class__.__name__}: {ex})")
        return None


def _uncrop_916(img):
    """Recover a 16:9 action canvas from a rendered clip frame.
    zoom clips have a pure-black caption bar up top (and a sharp HUD band at
    the bottom) -> lift the playfield band; anything else is treated as the
    'full' layout, whose middle band holds the whole source frame. A `whole`
    part is already 16:9, and the 'full' branch reduces to the identity crop on
    one (h == w*9/16), so it needs no case of its own."""
    from PIL import ImageStat
    w, h = img.size

    def region_mean(box):
        return sum(ImageStat.Stat(img.crop(box)).mean) / 3

    top_bar = _even(h * ZOOM_TOP_FRAC)
    if region_mean((0, 0, w // 8, 40)) < 10:          # zoom: black corner
        hud_out = _even(_even(1080 * ZOOM_HUD_FRAC * 16 / 9) * w / (1080 * 16 / 9))
        band = img.crop((0, top_bar, w, h - hud_out))
        bh = int(w * 9 / 16)
        y0 = top_bar + (band.height - bh) // 2
        return img.crop((0, max(top_bar, y0), w, min(h - hud_out, y0 + bh)))
    bh = int(w * 9 / 16)                              # full: frame sits centered
    return img.crop((0, (h - bh) // 2, w, (h + bh) // 2))


def rethumb_all():
    """Regenerate thumbnails for every rendered clip in clips/ — no re-render,
    no VOD needed. Uses the clip itself (canvas recovered per layout) and the
    sidecar title for the hook."""
    clips = sorted(CLIPS_DIR.glob("short_*.mp4"))
    if not clips:
        return print(f"[thumb] no clips in ./{CLIPS_DIR}/")
    for mp4 in clips:
        dur = _duration(mp4) or 30
        img = _pick_frame(mp4, [dur * f for f in (0.15, 0.30, 0.45, 0.60, 0.75)])
        if img is None:
            print(f"[thumb] {mp4.name}: no frame — skipped")
            continue
        title = (_read_sidecar(mp4).get("TITLE") or mp4.stem).splitlines()[0]
        # accepts both the current " [3]" suffix and the legacy " (#3)" one
        title = re.sub(r"\s*(?:\(#\d+\)|\[\d+\])\s*$", "", title).replace("🔥", "").strip()
        _compose_thumb(_uncrop_916(img), _hook_text(title, None),
                       mp4.with_name(mp4.stem + "_thumb.jpg"))


def _teaser_window(start: float, dur: int, peak_pos: float) -> tuple[float, float]:
    """Source window the cold-open flash is cut from: (t0, length).

    Ends TEASER_DUR-TEASER_LEAD (0.4s) *after* the audio spike — the viewer sees
    the engage and the caster starting to yell, then it's gone. That the flash is
    too short to show the outcome is the whole reason a cold open doesn't break
    HPC's "never pay off early": it raises the question instead of answering it.
    Clamped to the video start and to the clip's own end.
    """
    peak = start + peak_pos * dur
    t0 = max(0.0, peak - TEASER_LEAD)
    t1 = min(t0 + TEASER_DUR, start + dur)
    return t0, max(0.0, t1 - t0)


def _track_window(layout, dims) -> int | None:
    """SOURCE-pixel width of the window a tracked layout pans, or None for the
    plain full-height 9:16 slice `crop` uses. Keeps the three call sites (main
    segment, teaser, and the split gameplay panel) from drifting apart."""
    if layout == "zoom":
        return zoom_geometry(dims)[-1]
    if layout == "split":
        return split_geometry(dims)[-1]
    return None


def cut_clip(video, start, dur, idx, layout, caption, subs, dims,
             cap_size=66, peak_pos=0.72, facecam_override=None,
             title="Highlight", platform="youtube", ai_meta=True,
             thumbs=False, teaser=True, endcard=None, translate=True,
             title_override=None) -> Path:
    out = CLIPS_DIR / f"short_{idx:02d}_{int(start)}s.mp4"
    src_w, src_h = dims

    facecam, face_cmd = None, None
    if layout == "split":
        if facecam_override:
            facecam = facecam_override           # caller pinned the box; do not track
        else:
            facecam, face_cmd = facecam_track(video, start, dur, src_w, src_h)
        if not facecam:                          # no face -> show the whole video instead
            print(f"[clip {idx}] no facecam detected; falling back to full-video layout")
            layout = "full"
    crop_x, track_cmd = 0, None
    if layout in ("crop", "zoom", "split"):  # each tracks with its own window width
        crop_x, track_cmd = track_path(video, start, dur, src_w, src_h,
                                       crop_w=_track_window(layout, dims))

    # Tracked layouts fill the whole 1080 width, so captions can be bigger and
    # still clear the action — the user's clips read as "captions very far".
    # `split` is excluded: its captions sit inside the facecam panel, which is
    # 42% of the canvas, so the bumped size would run straight over the face.
    cap_size_eff = max(cap_size, CAP_SIZE_TRACKED) if layout in ("crop", "zoom") else cap_size
    if layout == "whole":
        # The 16:9 canvas is 1080 tall, not 1920: a size tuned as ~4.4% of a
        # Short's height has to be re-expressed against this canvas or the text
        # comes out nearly twice as large relative to the frame.
        cap_size_eff = max(28, round(CAP_SIZE_TRACKED * canvas(layout)[1] / H))

    ass_path, hook, transcript = None, None, None
    if subs:
        tmp = CLIPS_DIR / f".raw_{idx}.mp4"
        ff("-y", "-ss", str(start), "-i", str(video), "-t", str(dur), "-c", "copy", str(tmp))
        # "Already captioned -> don't add a second layer" is a rule about
        # DUPLICATES. A Korean broadcast with burned-in Korean text is the one
        # case where our layer says something the source's doesn't, so ask what
        # language is being spoken before honouring the skip. Detection is one
        # encoder pass; transcription is the expensive part and still gated.
        skip = has_existing_captions(video, start, dur, dims)
        if skip:
            lang, prob = detect_speech_language(tmp)
            if lang in TRANSLATE_LANGS and prob >= LANG_MIN_PROB:
                print(f"[clip {idx}] source is captioned, but the speech is "
                      f"'{lang}' — adding the English translation anyway")
                skip = False
            else:
                print(f"[clip {idx}] source already has captions; skipping added captions")
        if not skip:
            an, margin = caption_anchor(layout, dims)  # placement is decided by layout
            # The 48px floor is a 9:16 number; on the shorter 16:9 canvas it would
            # be the size the layout was just scaled DOWN from, so scale it too.
            floor = round(48 * canvas(layout)[1] / H)
            ass_path, hook, transcript = make_dynamic_captions(
                tmp, an, margin, max(floor, cap_size_eff), translate=translate,
                play_res=canvas(layout))
        tmp.unlink(missing_ok=True)

    cap_an, cap_margin = caption_anchor(layout, dims)
    # The end card rides the MAIN segment, whose filters run on its own timeline —
    # and since the teaser is prepended, the end of that segment IS the end of the
    # finished Short. The teaser's build_vf call passes no endcard, so the flash
    # can never carry the CTA.
    vf = build_vf(layout, dims, crop_x, ass_path, caption,
                  cap_size_eff, cap_an, cap_margin, sendcmd=track_cmd,
                  endcard=endcard, endcard_from=max(0.0, dur - ENDCARD_DUR),
                  facecam=facecam, facecam_cmd=face_cmd)
    # Applied once to the FINISHED stream, so a teaser and the main segment can
    # never end up graded or tagged differently.
    tail = ""
    if QUALITY == "max":
        # Only at max: a mild unsharp before the color tag. The pipeline upscales
        # heavily onto 1080x1920 and YouTube's re-encode softens edges; this keeps
        # HUD/caption edges crisp. Amount kept low (0.4) to avoid halos/ringing.
        tail += ",unsharp=5:5:0.4:5:5:0.0"
    # Tag BT.709 explicitly — untagged uploads get guessed as BT.601 by the
    # YouTube transcoder, which is what makes clips look washed out/dull. Done as
    # a filter, not via -color_primaries/-color_trc: this ffmpeg build silently
    # drops those into the h264 VUI (verified: transfer=unknown).
    tail += "," + _BT709
    # NOTE: `-sws_flags` used to live here and was a proven no-op (see SCALE_FLAGS).
    # Scaler choice now rides on each scale filter, so there is nothing global left.
    common: list[str] = []
    enc = [*_ENC, "-pix_fmt", "yuv420p",
           # A keyframe every 2s is what YouTube's ingest wants; avoids re-encode drift.
           "-force_key_frames", "expr:gte(t,n_forced*2)",
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]

    t0, t_len = _teaser_window(start, dur, peak_pos) if teaser else (0.0, 0.0)
    if t_len >= 0.5:
        # HPC hook: a cold-open flash of the moment before the spike, hard-cut
        # back to the build-up. Rendered as a SECOND INPUT to this same ffmpeg
        # call and concatenated in the graph — not as a separate file — so one
        # loudnorm pass covers both segments (see _LOUDNORM) and there is no
        # concat-seam drift from audio priming samples.
        t_crop_x = crop_x
        if layout in ("crop", "zoom", "split"):  # frame the teaser on the FIGHT, not the walk-in
            t_crop_x = track_path(video, t0, max(1, int(round(t_len))), src_w, src_h,
                                  crop_w=_track_window(layout, dims))[0]
        # No captions and no headline on the flash; `suffix` keeps its link labels
        # and its crop@dyn instance from colliding with the main segment's (whose
        # crop@dyn is what the sendcmd script addresses by name).
        #
        # The teaser gets its OWN static facecam box rather than the main
        # segment's. It is a different moment in the stream — that is the whole
        # point of a cold open — so the scene there may not be the scene the
        # main segment opens on, which is exactly the mismatch the tracker
        # exists to fix. Too short to animate (a second or two, ~2-4 samples),
        # so it is detected once and held; if that detection fails it falls back
        # to the main box, which is still better than no facecam panel at all.
        t_face = facecam
        if layout == "split" and not facecam_override:
            t_face = detect_facecam(video, t0, max(1, int(round(t_len))),
                                    src_w, src_h) or facecam
        tvf = build_vf(layout, dims, t_crop_x, None, None,
                       cap_size_eff, cap_an, cap_margin, suffix="_t",
                       facecam=t_face)
        fc = (f"[0:v]{tvf}[tv];[1:v]{vf}[mv];"
              f"[tv][mv]concat=n=2:v=1:a=0{tail}[vo];"
              f"[0:a][1:a]concat=n=2:v=0:a=1,{_LOUDNORM}[ao]")
        ff("-y", *common,
           "-ss", str(t0), "-t", str(t_len), "-i", str(video),
           "-ss", str(start), "-t", str(dur), "-i", str(video),
           "-filter_complex", fc, "-map", "[vo]", "-map", "[ao]",
           *enc, str(out))
    else:
        ff("-y", *common,
           "-ss", str(start), "-i", str(video), "-t", str(dur),
           "-vf", vf + tail, "-af", _LOUDNORM,
           *enc, str(out))
    if ass_path:
        ass_path.unlink(missing_ok=True)
    if track_cmd:                                # remove the temp sendcmd pan script
        track_cmd.unlink(missing_ok=True)
    if face_cmd:                                 # ...and the facecam's
        face_cmd.unlink(missing_ok=True)
    meta = _ollama_metadata(transcript, idx) if (ai_meta and transcript) else None
    write_metadata(out, title, idx, platform, hook, meta,   # title + caption sidecar
                   transcript=transcript, endcard=endcard,
                   title_override=title_override)
    if thumbs:                                   # AI thumbnail from the clean source
        make_thumbnail(video, start, dur, peak_pos, idx, out,
                       (meta or {}).get("title") or (hook or title), transcript)
    print(f"[clip] {out}  ({out.stat().st_size // 1_000_000} MB)")
    return out


def make_clips(video: Path, *, max_clips=5, clip_len=30, peak_pos=0.72, layout="full",
               caption=None, subs=False, cap_size=66, title="Highlight",
               platform="youtube", progress=None, ai_meta=True, thumbs=True,
               teaser=True, endcard=None, translate=True) -> tuple[list[Path], Path | None]:
    """Full local pipeline on a downloaded video. Shared by CLI + web.

    Returns ``(clips, hero)`` — the rendered clips plus ONE hero thumbnail for the
    whole source (or ``None`` if disabled/unavailable). Each clip also gets its own
    creative 9:16 thumbnail (``<stem>_thumb.jpg``) so every Short has a ready cover
    the dashboard can show + offer for download; ``thumbs`` gates both.

    ``layout="whole"`` takes a different route entirely: see ``make_whole_parts``.
    ``max_clips`` and ``clip_len`` do not apply there and are ignored.
    """
    dims = _dims(video)
    if layout == "whole":
        return make_whole_parts(video, dims, caption=caption, subs=subs,
                                cap_size=cap_size, title=title, platform=platform,
                                progress=progress, ai_meta=ai_meta, endcard=endcard,
                                translate=translate)
    # A source shorter than the requested clip length yields NOTHING at all:
    # find_hype_moments rejects every window whose `start + clip_len` runs past
    # the end, so a 30s Twitch clip asked for 45s clips returns zero moments and
    # the render "succeeds" with an empty clips/ directory. Clamp instead — the
    # whole source becomes the clip, which is what a short source wants anyway.
    dur = int(_duration(video))
    if dur and clip_len > dur:
        print(f"[clip] source is only {dur}s — clamping clip length from {clip_len}s")
        clip_len = max(5, dur)
    moments = find_hype_moments(video, clip_len, max_clips, peak_pos)
    clips = []
    for i, t in enumerate(moments, 1):
        clips.append(cut_clip(video, t, clip_len, i, layout, caption, subs, dims,
                              cap_size=cap_size, peak_pos=peak_pos,
                              title=title, platform=platform, ai_meta=ai_meta,
                              thumbs=thumbs, teaser=teaser, endcard=endcard,
                              translate=translate))
        if progress:
            progress(i, len(moments))
    hero = None
    if thumbs and clips:
        hero = make_hero_thumbnail(video, moments, dims, title, None,
                                   CLIPS_DIR / f"hero_{video.stem}.jpg",
                                   peak_pos=peak_pos, clip_len=clip_len)
    return clips, hero


def make_whole_parts(video: Path, dims, *, caption=None, subs=False, cap_size=66,
                     title="Highlight", platform="youtube", progress=None,
                     ai_meta=True, endcard=None,
                     translate=True) -> tuple[list[Path], Path | None]:
    """Cut the ENTIRE video into consecutive 16:9 parts, titled Part 1..Part N.

    This is not the highlight pipeline with different numbers — it shares nothing
    with it but ``cut_clip``:

      * no audio-spike detection, because nothing is being selected: the segments
        are `whole_segments(duration)`, back to back, covering every frame;
      * no cold-open teaser, because a flash-forward would replay footage the
        part is about to show in order and break the one thing a "Part N" series
        promises — that watching them in sequence is watching the video;
      * no hero/per-part thumbnail, because ``_compose_thumb`` composes a 1080x1920
        Shorts cover and these parts are landscape. A vertical cover on a 16:9
        video is worse than none.

    Returns ``(parts, None)`` to match ``make_clips``.
    """
    duration = _duration(video)
    # Both tunables are passed explicitly rather than left to the defaults, so the
    # values in force are the module's current ones — that is what lets a test
    # shrink the part length and exercise the real segmentation on a 10s source.
    segs = whole_segments(duration, WHOLE_PART_LEN, WHOLE_TAIL_MIN)
    if not segs:
        sys.exit("[error] could not read the video's duration — nothing to cut.")
    print(f"[whole] {duration:.0f}s source -> {len(segs)} parts of "
          f"<={WHOLE_PART_LEN}s (16:9)")
    parts: list[Path] = []
    for i, (start, seg_len) in enumerate(segs, 1):
        print(f"[whole] Part {i}/{len(segs)}  {start:.0f}s -> {start + seg_len:.0f}s")
        parts.append(cut_clip(video, start, seg_len, i, "whole", caption, subs, dims,
                              cap_size=cap_size, title=title, platform=platform,
                              ai_meta=ai_meta, thumbs=False, teaser=False,
                              endcard=endcard, translate=translate,
                              title_override=f"Part {i}"))
        if progress:
            progress(i, len(segs))
    return parts, None


# ── framing sample harness (pick a zoom level from real renders) ──────────────
SAMPLE_ZOOMS = (1.0, 1.25, 1.5)   # punch-in multipliers to compare
SAMPLE_DUR   = 12                 # seconds per sample clip


def _render_sample(video: Path, start: float, dur: int, dims, crop_w: int,
                   eased: bool, out: Path) -> Path:
    """Render one framing sample (no captions) with a fast encode.

    `eased` renders the sendcmd pan; otherwise the crop freezes at the opening x
    so the two sit side by side. `crop_w` sets the punch-in: build_vf keeps 9:16
    and re-centers, so tighter windows read as a genuine zoom, not a stretch.
    """
    x0, script = track_path(video, start, dur, dims[0], dims[1], crop_w=crop_w)
    if not eased:
        script = None                        # static: freeze at the opening x
    an, margin = caption_anchor("crop", dims)
    vf = build_vf("crop", dims, x0, None, None, 66, an, margin,
                  sendcmd=script, crop_w=crop_w)
    ff("-y", "-ss", str(start), "-i", str(video), "-t", str(dur),
       "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
       "-pix_fmt", "yuv420p", "-an", str(out))
    if script:
        script.unlink(missing_ok=True)
    return out


def make_sample(video: Path, at: float | None = None) -> list[Path]:
    """Render a labeled framing comparison set to clips/samples/ so the user can
    pick a zoom level and confirm the eased pan by eye: static vs eased across
    SAMPLE_ZOOMS punch-in levels."""
    dims = _dims(video)
    src_w, src_h = dims
    start = at if at is not None else find_hype_moments(video, SAMPLE_DUR, 1, 0.5)[0]
    base_w = min(int(src_h * 9 / 16), src_w)
    outdir = CLIPS_DIR / "samples"
    outdir.mkdir(parents=True, exist_ok=True)
    outs: list[Path] = []
    for z in SAMPLE_ZOOMS:
        crop_w = max(2, int(base_w / z) // 2 * 2)      # tighter zoom = narrower crop
        for eased in (False, True):
            tag = "eased" if eased else "static"
            out = outdir / f"sample_{tag}_{z:.2f}x.mp4"
            _render_sample(video, start, SAMPLE_DUR, dims, crop_w, eased, out)
            outs.append(out)
    print(f"[sample] wrote {len(outs)} clips to {outdir}")
    return outs


# ── 4. YouTube (only for --draft / --list-channels) ──────────────────────────
def _youtube():
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    scopes = ["https://www.googleapis.com/auth/youtube.upload",
              "https://www.googleapis.com/auth/youtube.readonly"]
    token, secrets = Path("token.pickle"), Path("client_secrets.json")
    if not secrets.exists():
        sys.exit("[auth] client_secrets.json not found — see SETUP.md.")
    creds = pickle.loads(token.read_bytes()) if token.exists() else None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            creds = InstalledAppFlow.from_client_secrets_file(
                str(secrets), scopes).run_local_server(port=0)
        token.write_bytes(pickle.dumps(creds))
    return build("youtube", "v3", credentials=creds)


def show_channel():
    for ch in _youtube().channels().list(part="snippet", mine=True).execute().get("items", []):
        print(f"[channel] {ch['snippet']['title']}  (id: {ch['id']})")


def upload_draft(clip: Path, title: str, idx: int):
    from googleapiclient.http import MediaFileUpload
    side = _read_sidecar(clip)   # prefer the (AI-written) sidecar metadata
    tags = [t.strip() for t in side.get("TAGS", "").split(",") if t.strip()]
    body = {"snippet": {"title": side.get("TITLE") or f"{title} #{idx}",
                        "description": side.get("CAPTION") or "#LeagueOfLegends #LoL #Shorts #Gaming",
                        "tags": tags or ["LeagueOfLegends", "LoL", "Shorts", "Gaming"],
                        "categoryId": "20"},
            "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False}}
    req = _youtube().videos().insert(
        part="snippet,status", body=body,
        media_body=MediaFileUpload(str(clip), mimetype="video/mp4", resumable=True))
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            print(f"[draft] {int(status.progress()*100)}% — {clip.name}")
    print(f"[draft] PRIVATE: https://studio.youtube.com/video/{resp['id']}/edit")


# ── main ─────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="Cut a YouTube VOD into 9:16 Shorts clips.")
    p.add_argument("url", nargs="?",
                   help="YouTube or Twitch URL, or a path to a local video file")
    p.add_argument("--max-clips", type=int, default=5, help="clips to produce (5)")
    p.add_argument("--clip-len", type=int, default=30, help="seconds per clip (30)")
    p.add_argument("--peak-pos", type=float, default=0.72,
                   help="spike position 0-1; higher = longer build-up (0.72)")
    p.add_argument("--endcard", metavar="TEXT",
                   help="affiliate CTA burned over the final 1.5s, e.g. "
                        "\"Faker's settings -> pinned comment\". Adds no runtime; "
                        "Shorts has no clickable surface mid-playback, so the card "
                        "points at the pinned comment (see the affiliate spec)")
    p.add_argument("--no-teaser", action="store_true",
                   help="skip the cold-open flash of the moment before the spike "
                        "(on by default; it is what owns the first seconds)")
    p.add_argument("--layout", choices=LAYOUTS, default="full",
                   help="full=whole video centered with a blurred fill, captions "
                        "under it (default); whole=cut the ENTIRE video into "
                        f"consecutive {WHOLE_PART_LEN}s landscape 16:9 parts titled "
                        "Part 1..Part N (--max-clips/--clip-len do not apply); "
                        "split=streamer facecam on top, tracked gameplay below "
                        "(falls back to full when no face is found); "
                        "zoom=punched-in playfield that tracks "
                        "the action, with the game HUD re-stacked at the bottom and "
                        "a black caption bar on top")
    p.add_argument("--caption", help="static headline text burned onto every clip")
    p.add_argument("--cap-size", type=int, default=66, help="caption font size (66)")
    p.add_argument("--subtitles", action="store_true",
                   help="dynamic word-by-word captions from speech (needs faster-whisper); "
                        "auto-skipped if the source is already captioned, unless the "
                        "speech is a language we translate")
    p.add_argument("--no-translate", action="store_true",
                   help=f"caption foreign speech verbatim instead of translating it. "
                        f"By default speech detected as {'/'.join(sorted(TRANSLATE_LANGS))} "
                        f"is captioned in English via Whisper's translate task")
    p.add_argument("--platform", choices=["youtube", "tiktok", "both"], default="youtube",
                   help="platform the generated title/caption sidecar targets (youtube)")
    p.add_argument("--title", default="LoL Highlight", help="base title for clips and --draft")
    p.add_argument("--no-ai-meta", action="store_true",
                   help="skip Ollama title/description generation (falls back to hook titles)")
    p.add_argument("--no-thumbs", action="store_true",
                   help="skip the hero thumbnail (clips/hero_<source>.jpg, one per video)")
    p.add_argument("--rethumb", action="store_true",
                   help="regenerate thumbnails for existing clips in clips/, then exit")
    p.add_argument("--draft", action="store_true", help="ALSO upload as PRIVATE drafts")
    p.add_argument("--list-channels", action="store_true",
                   help="show which channel --draft would use, then exit")
    p.add_argument("--sample", metavar="URL_OR_FILE",
                   help="render a framing/zoom comparison set (static vs eased across "
                        "punch-in levels) to clips/samples/ instead of full clips, then exit")
    p.add_argument("--at", type=float, default=None,
                   help="sample at this many seconds in (default: top audio peak)")
    a = p.parse_args()

    if a.list_channels:
        return show_channel()
    if a.rethumb:
        return rethumb_all()
    if a.sample:
        src = Path(a.sample)
        if not src.exists():                 # not a local file -> treat as a URL to fetch
            src = download_video(a.sample)
        make_sample(src, at=a.at)
        return
    if not a.url:
        p.error("a URL or local video file is required "
                "(unless using --list-channels or --rethumb)")

    if a.layout == "whole":
        # Length is fixed and the count comes from the source's duration, so both
        # of these are meaningless here. Say so rather than appearing to honour a
        # number the run will not use — the dashboard hides the fields entirely,
        # but a CLI caller (or a script written before this mode existed) can
        # still pass them.
        given = [f"--{n.replace('_', '-')}" for n, d in
                 (("max_clips", 5), ("clip_len", 30)) if getattr(a, n) != d]
        if given:
            print(f"[whole] ignoring {' and '.join(given)}: whole-video mode cuts "
                  f"the entire source into {WHOLE_PART_LEN}s parts")
        print(f"\n=== LoL Clipper ===\nwhole video -> {WHOLE_PART_LEN}s parts "
              f"| layout=whole (16:9)\n")
    else:
        print(f"\n=== LoL Clipper ===\n{a.max_clips} x {a.clip_len}s | layout={a.layout}\n")
    # Same idiom --sample has always used: an argument that names a file on disk
    # is that file, not something to hand to yt-dlp. This is how an uploaded
    # video reaches the pipeline — the dashboard stages the upload and passes
    # its path here, so nothing has to be re-fetched from a network at all.
    local = Path(a.url)
    if local.is_file():
        print(f"[source] local file {local}  ({local.stat().st_size // 1_000_000} MB)")
        video = local
    else:
        video = download_video(a.url)
    clips, hero = make_clips(video, max_clips=a.max_clips, clip_len=a.clip_len,
                             peak_pos=a.peak_pos, layout=a.layout, caption=a.caption,
                             subs=a.subtitles, cap_size=a.cap_size,
                             teaser=not a.no_teaser, endcard=a.endcard,
                             translate=not a.no_translate,
                             title=a.title, platform=a.platform, ai_meta=not a.no_ai_meta,
                             thumbs=not a.no_thumbs)

    print(f"\n[done] {len(clips)} clips in ./{CLIPS_DIR}/")
    if hero:
        print(f"[done] hero thumbnail: ./{hero}")
    if a.draft:
        show_channel()
        for i, c in enumerate(clips, 1):
            upload_draft(c, a.title, i)
    else:
        print("Pick your favorites from clips/ and drag them into YouTube Studio.")


if __name__ == "__main__":
    DOWNLOADS_DIR.mkdir(exist_ok=True)
    CLIPS_DIR.mkdir(exist_ok=True)
    main()
