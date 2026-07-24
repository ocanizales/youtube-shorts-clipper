"""
LoL YouTube Shorts Clipper — turn a YouTube VOD into 9:16 highlight clips.

Clips are saved locally by default; nothing is uploaded unless you pass --draft
(which uploads as PRIVATE, never public). Run with -h for all options.
"""

import argparse
import os
import pickle
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

DOWNLOADS_DIR = Path("downloads")
CLIPS_DIR = Path("clips")
W, H = 1080, 1920


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
    "high": ("17", "medium",   "24M", "48M"),
    # max: lowest CRF the upload pipeline benefits from + the slowest preset we'll
    # accept, with headroom on the rate cap so a busy teamfight keeps its detail.
    "max":  ("15", "slower",   "48M", "96M"),
}
_NVENC_CQ = {"fast": "23", "high": "19", "max": "16"}
# Appended to every output filter chain so the file is tagged Rec.709 (see cut_clip).
_BT709 = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"


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
    for line in proc.stdout:                      # stream + parse % as it downloads
        m = _PCT.search(line)
        if m:
            pct = float(m.group(1))
            (on_progress or (lambda p: print(f"\r[download] {p:5.1f}%", end="")))(pct)
    proc.wait()
    print()
    if proc.returncode != 0:
        sys.exit("[error] yt-dlp failed.")

    media = _find_media(vid)
    if not media:
        sys.exit(f"[error] download produced no media file for {vid}.")
    print(f"[download] {media}  ({media.stat().st_size // 1_000_000} MB)")
    return media


# ── 2. detect hype moments via audio energy ──────────────────────────────────
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
        start = float(max(0, int(frame) - lead_in))
        if start + clip_len > n:
            continue
        if all(abs(start - s) >= clip_len for s in chosen):
            chosen.append(start)
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


SPLIT_TOP_FRAC = 0.42  # facecam occupies the top portion of the 9:16 canvas
ZOOM_TOP_FRAC  = 0.105  # zoom: black caption bar height (fraction of the 9:16 canvas)
ZOOM_HUD_FRAC  = 0.19   # zoom: bottom slice of the SOURCE treated as the game HUD strip

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


def full_video_height(dims) -> int:
    """Height (px) of the source frame when scaled to the full 1080 width."""
    src_w, src_h = dims
    return int(round(W * src_h / src_w / 2) * 2)  # even number for yuv420p


def caption_anchor(layout, dims) -> tuple[int, int]:
    """
    Where captions belong for each layout, as (ASS alignment, margin px).
    Alignment 8 = top-anchored (margin measured from the top),
    Alignment 2 = bottom-anchored (margin measured from the bottom).
    """
    if layout == "split":      # only at the BOTTOM of the facecam (top) panel
        return 8, int(H * SPLIT_TOP_FRAC * 0.88)
    if layout == "full":       # right UNDER the (now centered) video
        return 8, (H + full_video_height(dims)) // 2 + 24
    if layout == "fit":        # on the bottom blurred bar, clear of gameplay
        return 2, int(H * 0.07)
    if layout == "zoom":       # inside the black caption bar above the playfield
        return 8, max(24, int(H * ZOOM_TOP_FRAC * 0.30))
    return 2, int(H * 0.24)    # crop: lower third, above the bottom HUD


def build_vf(layout, dims, crop_x, facecam, ass_path, caption, cap_size, cap_an, cap_margin,
             sendcmd=None) -> str:
    src_w, src_h = dims
    if layout == "split" and facecam:        # facecam on top, gameplay on bottom
        fx, fy, fw, fh = facecam
        top_h, bot_h = int(H * SPLIT_TOP_FRAC), H - int(H * SPLIT_TOP_FRAC)
        vf = (f"split=2[a][b];"
              f"[a]crop={fw}:{fh}:{fx}:{fy},"
              f"scale={W}:{top_h}:force_original_aspect_ratio=increase,crop={W}:{top_h}[cam];"
              f"[b]scale={W}:{bot_h}:force_original_aspect_ratio=increase,crop={W}:{bot_h}[game];"
              f"[cam][game]vstack=inputs=2")
    elif layout == "full":                   # WHOLE video centered, blurred fill above+below
        vf = (f"split=2[bg][fg];"
              f"[bg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=22:4[b];"
              f"[fg]scale={W}:-2[v];"
              f"[b][v]overlay=(W-w)/2:(H-h)/2")  # vertically centered in the 9:16 frame
    elif layout == "fit":                    # whole frame centered + blurred bars
        vf = (f"split[bg][fg];[bg]scale={W}:{H}:force_original_aspect_ratio=increase,"
              f"crop={W}:{H},boxblur=22:4[b];"
              f"[fg]scale={W}:{H}:force_original_aspect_ratio=decrease[f];"
              f"[b][f]overlay=(W-w)/2:(H-h)/2")
    elif layout == "zoom":                   # punched-in playfield + game HUD re-stacked below
        _, play_h, top_bar, hud_out_h, mid_h, cw = zoom_geometry(dims)
        hud_src_h = src_h - play_h
        vf = (f"split=2[pf][hs];"
              f"[pf]crop@dyn=w={cw}:h={play_h}:x={crop_x}:y=0,scale={W}:{mid_h}[game];"
              f"[hs]crop={src_w}:{hud_src_h}:0:{play_h},scale={W}:{hud_out_h}[hud];"
              f"[game][hud]vstack=inputs=2,pad={W}:{H}:0:{top_bar}:black")
    else:                                    # motion-tracked 9:16 crop
        cw = min(int(src_h * 9 / 16), src_w)
        vf = f"crop@dyn=w={cw}:h={src_h}:x={crop_x}:y=0,scale={W}:{H}"
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
    return vf


_WHISPER = None


def _ass_ts(s: float) -> str:
    h, m = divmod(int(s), 3600); m, sec = divmod(m, 60)
    return f"{h:d}:{m:02d}:{sec:02d}.{int((s % 1) * 100):02d}"


def make_dynamic_captions(clip: Path, an: int, margin_v: int, fontsize: int):
    """
    Transcribe spoken words and write an .ass with word-by-word reveal where the
    active word is highlighted (the modern animated-caption look). Returns
    (ass_path, hook, transcript) where hook is the first spoken phrase (used for
    the title) and transcript is the full spoken text (fed to AI metadata), or
    (None, None, None) if faster-whisper isn't installed / there's no speech.
    """
    global _WHISPER
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("[subs] faster-whisper missing — run: pip install faster-whisper")
        return None, None, None
    if _WHISPER is None:  # cached across clips; CPU int8 avoids a CUDA/cuBLAS dependency
        _WHISPER = WhisperModel("base", device="cpu", compute_type="int8")

    segments, _ = _WHISPER.transcribe(str(clip), word_timestamps=True)
    words = [(w.word.strip(), w.start, w.end)
             for seg in segments for w in (seg.words or []) if w.word.strip()]
    if not words:
        return None, None, None

    # group into short phrases (max 5 words, split on >0.6s gaps)
    phrases, cur = [], []
    for w in words:
        if cur and (w[1] - cur[-1][2] > 0.6 or len(cur) >= 5):
            phrases.append(cur); cur = []
        cur.append(w)
    if cur:
        phrases.append(cur)

    ACCENT = "00D4FF"  # BGR: bright yellow highlight
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {W}\nPlayResY: {H}\n\n"
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


def detect_facecam(video: Path, start: float, dur: int, src_w: int, src_h: int):
    """
    Best-effort: find a streamer facecam box via face detection on sampled frames.
    Returns (x, y, w, h) in source pixels, or None. Expands the detected face to a
    webcam-sized box. Requires opencv (cv2).
    """
    try:
        import cv2
    except ImportError:
        return None
    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    dw, dh = 640, 360
    raw = subprocess.run(
        [_FFMPEG, "-ss", str(start), "-i", str(video), "-t", str(dur),
         "-vf", f"fps=1,scale={dw}:{dh}", "-pix_fmt", "bgr24", "-f", "rawvideo", "-"],
        capture_output=True).stdout
    nf = len(raw) // (dw * dh * 3)
    if nf == 0:
        return None
    frames = np.frombuffer(raw[:nf * dw * dh * 3], np.uint8).reshape(nf, dh, dw, 3)

    boxes = []
    for f in frames:
        gray = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, 1.1, 5, minSize=(40, 40))
        if len(faces):
            boxes.append(max(faces, key=lambda b: b[2] * b[3]))  # largest face
    if len(boxes) < max(2, nf // 3):   # need consistent detections
        return None

    fx, fy, fw, fh = np.median(np.array(boxes), axis=0)
    # expand face -> webcam box, scale back to source resolution
    sx, sy = src_w / dw, src_h / dh
    cx, cy = (fx + fw / 2) * sx, (fy + fh / 2) * sy
    bw, bh = fw * sx * 2.4, fh * sy * 2.8
    x, y = cx - bw / 2, cy - bh / 2
    x = max(0, min(x, src_w - bw)); y = max(0, min(y, src_h - bh))
    bw = min(bw, src_w - x); bh = min(bh, src_h - y)
    return int(x), int(y), int(bw), int(bh)


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


# ── AI metadata from the clip transcript (idea from MoneyPrinter's gpt.py) ───
# llama3.2:3b: small + non-thinking on purpose — runs in ~10s per clip on this
# CPU box, where the 9b thinking models take minutes and stall the render loop.
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")


def _ollama_metadata(transcript: str, idx: int) -> dict | None:
    """Title/description/tags for one clip via the local Ollama instance.
    Strictly best-effort: any failure (Ollama down, bad JSON, empty title)
    returns None and callers fall back to the hook-based title."""
    import json
    import urllib.request
    prompt = (
        "You write YouTube Shorts metadata for League of Legends esports "
        "highlight clips. Spoken commentary from this clip:\n"
        f'"{transcript[:1200]}"\n\n'
        'Reply with JSON only, exactly: {"title": "...", "description": "...", '
        '"hashtags": ["...", "..."]}\n'
        "- title: catchy, under 70 characters, no emoji, faithful to the commentary\n"
        "- description: 1-2 sentences\n"
        "- hashtags: 4-6 single words, no # symbol")
    body = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
            "format": "json", "options": {"temperature": 0.4, "num_predict": 250}}
    try:
        req = urllib.request.Request(
            f"{OLLAMA_HOST}/api/generate", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            meta = json.loads(json.load(r)["response"])
        title = str(meta.get("title", "")).strip().strip('"')
        desc = str(meta.get("description", "")).strip()
        tags = [re.sub(r"\W", "", str(t)) for t in meta.get("hashtags", [])]
        tags = [t for t in tags if t][:6]
        if not title:
            return None
        print(f"[meta {idx}] AI title: {title}")
        return {"title": title[:90], "description": desc[:400], "tags": tags}
    except Exception as ex:
        print(f"[meta {idx}] Ollama unavailable ({ex.__class__.__name__}) — using hook title")
        return None


def write_metadata(clip: Path, title_base: str, idx: int, platform: str,
                   hook: str | None, meta: dict | None = None):
    """Write a sidecar .txt with a ready-to-paste title + caption for the
    platform(s). TITLE stays the first section — the dashboard reads it.
    With AI meta, add a TAGS section that --draft feeds to the YouTube API."""
    if meta:
        title = f"{meta['title']} (#{idx})"
        body = (f"TITLE:\n{title}\n\nCAPTION:\n{meta['description']}\n"
                f"{_hashtags(platform)}\n\nTAGS:\n{', '.join(meta['tags'])}\n")
    else:
        headline = (hook or title_base).strip().rstrip(".!?")
        title = f"{headline} 🔥 (#{idx})"
        body = (f"TITLE:\n{title}\n\nCAPTION:\n{headline} — League of Legends highlight.\n"
                f"{_hashtags(platform)}\n")
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


def _frame_at(video: Path, t: float):
    """One full-res frame at t seconds as a PIL image (None on failure)."""
    import io
    from PIL import Image
    r = subprocess.run([_FFMPEG, "-v", "error", "-ss", f"{max(0, t):.2f}",
                        "-i", str(video), "-frames:v", "1",
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
    """Best-scoring frame among candidate timestamps (None if all fail)."""
    best, best_s = None, -1.0
    for t in times:
        img = _frame_at(video, t)
        if img is not None:
            s = _frame_score(img)
            if s > best_s:
                best, best_s = img, s
    return best


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
    """Recover a 16:9 action canvas from a rendered 9:16 clip frame.
    zoom clips have a pure-black caption bar up top (and a sharp HUD band at
    the bottom) -> lift the playfield band; anything else is treated as the
    'full' layout, whose middle band holds the whole source frame."""
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
        probe = _FFMPEG.replace("ffmpeg.exe", "ffprobe.exe") if _FFMPEG_DIR else "ffprobe"
        out = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", str(mp4)], capture_output=True, text=True).stdout
        dur = float(out.strip() or 30)
        img = _pick_frame(mp4, [dur * f for f in (0.15, 0.30, 0.45, 0.60, 0.75)])
        if img is None:
            print(f"[thumb] {mp4.name}: no frame — skipped")
            continue
        title = (_read_sidecar(mp4).get("TITLE") or mp4.stem).splitlines()[0]
        title = re.sub(r"\s*\(#\d+\)\s*$", "", title).replace("🔥", "").strip()
        _compose_thumb(_uncrop_916(img), _hook_text(title, None),
                       mp4.with_name(mp4.stem + "_thumb.jpg"))


def cut_clip(video, start, dur, idx, layout, caption, subs, dims,
             cap_size=66, peak_pos=0.65, facecam_override=None,
             title="Highlight", platform="youtube", ai_meta=True,
             thumbs=False) -> Path:
    out = CLIPS_DIR / f"short_{idx:02d}_{int(start)}s.mp4"
    src_w, src_h = dims

    facecam = None
    if layout == "split":
        facecam = facecam_override or detect_facecam(video, start, dur, src_w, src_h)
        if not facecam:                          # no face -> show the whole video instead
            print(f"[clip {idx}] no facecam detected; falling back to full-video layout")
            layout = "full"
    crop_x, track_cmd = 0, None
    if layout in ("crop", "zoom"):           # zoom tracks with its own (narrower) window
        zw = zoom_geometry(dims)[-1] if layout == "zoom" else None
        crop_x, track_cmd = track_path(video, start, dur, src_w, src_h, crop_w=zw)

    # Don't add captions if the source is already captioned (avoids duplicates).
    if subs and has_existing_captions(video, start, dur, dims):
        print(f"[clip {idx}] source already has captions; skipping added captions")
        subs = False

    ass_path, hook, transcript = None, None, None
    if subs:
        tmp = CLIPS_DIR / f".raw_{idx}.mp4"
        ff("-y", "-ss", str(start), "-i", str(video), "-t", str(dur), "-c", "copy", str(tmp))
        an, margin = caption_anchor(layout, dims)     # placement is decided by layout
        ass_path, hook, transcript = make_dynamic_captions(tmp, an, margin, max(48, cap_size))
        tmp.unlink(missing_ok=True)

    cap_an, cap_margin = caption_anchor(layout, dims)
    vf = build_vf(layout, dims, crop_x, facecam, ass_path, caption,
                  cap_size, cap_an, cap_margin, sendcmd=track_cmd)
    if QUALITY == "max":
        # Only at max: a mild unsharp before the color tag. The pipeline upscales
        # heavily onto 1080x1920 and YouTube's re-encode softens edges; this keeps
        # HUD/caption edges crisp. Amount kept low (0.4) to avoid halos/ringing.
        vf += ",unsharp=5:5:0.4:5:5:0.0"
    vf += "," + _BT709
    ff("-y",
       # Lanczos beats ffmpeg's default bicubic on the big upscales this pipeline
       # does (720/1080 source -> 1080x1920 canvas); keeps HUD text/edges crisp.
       "-sws_flags", "lanczos+accurate_rnd+full_chroma_int",
       "-ss", str(start), "-i", str(video), "-t", str(dur),
       # Tag BT.709 explicitly — untagged uploads get guessed as BT.601 by the
       # YouTube transcoder, which is what makes clips look washed out/dull.
       # Done as a filter, not via -color_primaries/-color_trc: this ffmpeg build
       # silently drops those into the h264 VUI (verified: transfer=unknown).
       "-vf", vf,
       *_ENC, "-pix_fmt", "yuv420p",
       # A keyframe every 2s is what YouTube's ingest wants; avoids re-encode drift.
       "-force_key_frames", "expr:gte(t,n_forced*2)",
       "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k",
       "-movflags", "+faststart", str(out))
    if ass_path:
        ass_path.unlink(missing_ok=True)
    if track_cmd:                                # remove the temp sendcmd pan script
        track_cmd.unlink(missing_ok=True)
    meta = _ollama_metadata(transcript, idx) if (ai_meta and transcript) else None
    write_metadata(out, title, idx, platform, hook, meta)   # title + caption sidecar
    if thumbs:                                   # AI thumbnail from the clean source
        make_thumbnail(video, start, dur, peak_pos, idx, out,
                       (meta or {}).get("title") or (hook or title), transcript)
    print(f"[clip] {out}  ({out.stat().st_size // 1_000_000} MB)")
    return out


def make_clips(video: Path, *, max_clips=5, clip_len=45, peak_pos=0.65, layout="full",
               caption=None, subs=False, cap_size=66, title="Highlight",
               platform="youtube", progress=None, ai_meta=True, thumbs=True
               ) -> tuple[list[Path], Path | None]:
    """Full local pipeline on a downloaded video. Shared by CLI + web.

    Returns ``(clips, hero)`` — the rendered clips plus ONE hero thumbnail for the
    whole source (or ``None`` if disabled/unavailable). Each clip also gets its own
    creative 9:16 thumbnail (``<stem>_thumb.jpg``) so every Short has a ready cover
    the dashboard can show + offer for download; ``thumbs`` gates both.
    """
    dims = _dims(video)
    moments = find_hype_moments(video, clip_len, max_clips, peak_pos)
    clips = []
    for i, t in enumerate(moments, 1):
        clips.append(cut_clip(video, t, clip_len, i, layout, caption, subs, dims,
                              cap_size=cap_size, peak_pos=peak_pos,
                              title=title, platform=platform, ai_meta=ai_meta,
                              thumbs=thumbs))          # per-clip 9:16 cover each
        if progress:
            progress(i, len(moments))
    hero = None
    if thumbs and clips:
        hero = make_hero_thumbnail(video, moments, dims, title, None,
                                   CLIPS_DIR / f"hero_{video.stem}.jpg",
                                   peak_pos=peak_pos, clip_len=clip_len)
    return clips, hero


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
    p.add_argument("url", nargs="?", help="YouTube video URL")
    p.add_argument("--max-clips", type=int, default=5, help="clips to produce (5)")
    p.add_argument("--clip-len", type=int, default=45, help="seconds per clip (45)")
    p.add_argument("--peak-pos", type=float, default=0.65,
                   help="spike position 0-1; higher = longer build-up (0.65)")
    p.add_argument("--layout", choices=["crop", "full", "split", "zoom"], default="full",
                   help="full=whole video, captions under it (default); crop=motion-tracked "
                        "zoom; split=facecam on top + gameplay on bottom (auto-detects facecam); "
                        "zoom=punched-in playfield with the game HUD re-stacked at the bottom "
                        "and a black caption bar on top")
    p.add_argument("--caption", help="static headline text burned onto every clip")
    p.add_argument("--cap-size", type=int, default=66, help="caption font size (66)")
    p.add_argument("--subtitles", action="store_true",
                   help="dynamic word-by-word captions from speech (needs faster-whisper); "
                        "auto-skipped if the source is already captioned")
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
    a = p.parse_args()

    if a.list_channels:
        return show_channel()
    if a.rethumb:
        return rethumb_all()
    if not a.url:
        p.error("a YouTube URL is required (unless using --list-channels or --rethumb)")

    print(f"\n=== LoL Clipper ===\n{a.max_clips} x {a.clip_len}s | layout={a.layout}\n")
    video = download_video(a.url)
    clips, hero = make_clips(video, max_clips=a.max_clips, clip_len=a.clip_len,
                             peak_pos=a.peak_pos, layout=a.layout, caption=a.caption,
                             subs=a.subtitles, cap_size=a.cap_size,
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
