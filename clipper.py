"""
LoL YouTube Shorts Clipper — turn a YouTube VOD into 9:16 highlight clips.

Clips are saved locally by default; nothing is uploaded unless you pass --draft
(which uploads as PRIVATE, never public). Run with -h for all options.
"""

import argparse
import os
import pickle
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

DOWNLOADS_DIR = Path("downloads")
CLIPS_DIR = Path("clips")
FONT = R"C\:/Windows/Fonts/arialbd.ttf"  # drawtext needs the drive colon escaped
W, H = 1080, 1920
# Always grab the best video up to 1080p, ANY codec (1080p on YouTube is usually
# VP9/webm, not mp4 — restricting to mp4 silently drops you to 720p or lower).
FMT = "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b"
# Format sort: prefer resolution 1080, then 60fps, then h264 (mp4-friendly), then m4a.
FMT_SORT = "res:1080,fps,vcodec:h264,acodec:m4a"
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


def _pick_encoder() -> list[str]:
    """Use NVIDIA NVENC if it actually works (much faster); else fast x264."""
    try:
        subprocess.run([_FFMPEG, "-hide_banner", "-f", "lavfi",
                        "-i", "color=black:s=64x64:d=0.1",
                        "-c:v", "h264_nvenc", "-f", "null", "-"],
                       capture_output=True, check=True)
        print("[encoder] NVIDIA NVENC (GPU) — fast")
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "23"]
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("[encoder] libx264 (CPU) — preset veryfast")
        return ["-c:v", "libx264", "-crf", "21", "-preset", "veryfast"]


_ENC = _pick_encoder()


def ff(*args):
    subprocess.run([_FFMPEG, *args], capture_output=True, check=True)


# ── 1. download (with progress callback) ─────────────────────────────────────
_PCT = re.compile(r"(\d{1,3}\.\d)%")


def _find_media(vid: str) -> Path | None:
    """Largest finished media file for this id (any container), ignoring partials."""
    files = [p for p in DOWNLOADS_DIR.glob(f"{vid}.*")
             if p.suffix.lower() in _MEDIA_EXTS and p.stat().st_size > 1_000_000]
    return max(files, key=lambda p: p.stat().st_size) if files else None


def download_video(url: str, on_progress=None) -> Path:
    """Download the best video+audio up to 1080p (any container), merged."""
    vid = subprocess.run(["yt-dlp", "--no-playlist", "--print", "id", url],
                         capture_output=True, text=True, check=True).stdout.strip()
    cached = _find_media(vid)
    if cached:
        print(f"[download] reusing {cached}")
        return cached

    cmd = ["yt-dlp", "--no-playlist", "-N", "8", "--newline", "-f", FMT,
           "-S", FMT_SORT, "--merge-output-format", "mp4",
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


def focus_x(video: Path, start: float, dur: int, src_w: int, src_h: int,
            crop_w=None, spike_frac=0.65) -> int:
    """
    Return the left x (px) of the crop window that captures the most action.

    Improvement over a center-of-mass: we slide the actual crop-width window
    across the per-column motion profile and pick the window with the MOST
    motion, weighted toward the highlight moment (spike_frac through the clip).
    Center-of-mass drifts to the middle when motion is on both sides; a sliding
    window does not.
    """
    crop_w = crop_w or min(int(src_h * 9 / 16), src_w)
    sw, sh, fps = 240, 135, 4
    raw = subprocess.run(
        [_FFMPEG, "-ss", str(start), "-i", str(video), "-t", str(dur),
         "-vf", f"fps={fps},scale={sw}:{sh},format=gray", "-f", "rawvideo", "-"],
        capture_output=True).stdout
    nf = len(raw) // (sw * sh)
    if nf < 2:
        return (src_w - crop_w) // 2
    frames = np.frombuffer(raw[:nf * sw * sh], np.uint8).reshape(nf, sh, sw).astype(np.int16)
    m = np.abs(np.diff(frames, axis=0)).sum(axis=1)          # (nf-1, sw) motion per column
    t = np.arange(m.shape[0])
    spike = spike_frac * (m.shape[0] - 1)
    w = np.exp(-0.5 * ((t - spike) / max(1.0, m.shape[0] * 0.25)) ** 2)  # focus near the climax
    profile = (m * w[:, None]).sum(axis=0)                   # (sw,) time-weighted column motion
    if profile.sum() == 0:
        return (src_w - crop_w) // 2
    win = max(1, round(crop_w / src_w * sw))
    if win >= sw:
        return (src_w - crop_w) // 2
    sums = np.convolve(profile, np.ones(win), "valid")       # motion captured per window
    left = int(np.argmax(sums))
    x = int(left / sw * src_w)
    return max(0, min(x, src_w - crop_w))


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", R"\:").replace("'", R"’")


SPLIT_TOP_FRAC = 0.42  # facecam occupies the top portion of the 9:16 canvas


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
    if layout == "full":       # right UNDER the video so viewers don't look far down
        return 8, full_video_height(dims) + 24
    if layout == "fit":        # on the bottom blurred bar, clear of gameplay
        return 2, int(H * 0.07)
    return 2, int(H * 0.24)    # crop: lower third, above the bottom HUD


def build_vf(layout, dims, crop_x, facecam, ass_path, caption, cap_size, cap_an, cap_margin) -> str:
    src_w, src_h = dims
    if layout == "split" and facecam:        # facecam on top, gameplay on bottom
        fx, fy, fw, fh = facecam
        top_h, bot_h = int(H * SPLIT_TOP_FRAC), H - int(H * SPLIT_TOP_FRAC)
        vf = (f"split=2[a][b];"
              f"[a]crop={fw}:{fh}:{fx}:{fy},"
              f"scale={W}:{top_h}:force_original_aspect_ratio=increase,crop={W}:{top_h}[cam];"
              f"[b]scale={W}:{bot_h}:force_original_aspect_ratio=increase,crop={W}:{bot_h}[game];"
              f"[cam][game]vstack=inputs=2")
    elif layout == "full":                   # WHOLE video at the top, blurred fill below
        vf = (f"split=2[bg][fg];"
              f"[bg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=22:4[b];"
              f"[fg]scale={W}:-2[v];"
              f"[b][v]overlay=(W-w)/2:0")    # video pinned to the top edge
    elif layout == "fit":                    # whole frame centered + blurred bars
        vf = (f"split[bg][fg];[bg]scale={W}:{H}:force_original_aspect_ratio=increase,"
              f"crop={W}:{H},boxblur=22:4[b];"
              f"[fg]scale={W}:{H}:force_original_aspect_ratio=decrease[f];"
              f"[b][f]overlay=(W-w)/2:(H-h)/2")
    else:                                    # motion-tracked 9:16 crop
        cw = min(int(src_h * 9 / 16), src_w)
        vf = f"crop={cw}:{src_h}:{crop_x}:0,scale={W}:{H}"
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
    (ass_path, hook) where hook is the first spoken phrase (used for the title),
    or (None, None) if faster-whisper isn't installed / there's no speech.
    """
    global _WHISPER
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("[subs] faster-whisper missing — run: pip install faster-whisper")
        return None, None
    if _WHISPER is None:  # cached across clips; CPU int8 avoids a CUDA/cuBLAS dependency
        _WHISPER = WhisperModel("base", device="cpu", compute_type="int8")

    segments, _ = _WHISPER.transcribe(str(clip), word_timestamps=True)
    words = [(w.word.strip(), w.start, w.end)
             for seg in segments for w in (seg.words or []) if w.word.strip()]
    if not words:
        return None, None

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
            f"Style: Pop,Arial,{fontsize},&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,"
            f"100,100,0,0,1,4,2,{an},60,60,{margin_v},1\n\n"
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
                parts.append(f"{{\\c&H{ACCENT}&}}{wt}{{\\c&HFFFFFF&}}" if j == i else wt)
            lines.append(f"Dialogue: 0,{_ass_ts(ws)},{_ass_ts(end)},Pop,,0,0,0,,"
                         + " ".join(parts))

    ass = clip.with_suffix(".ass")
    ass.write_text(head + "\n".join(lines), encoding="utf-8")
    hook = " ".join(w for w, _, _ in phrases[0][:8])   # first phrase -> title hook
    return ass, hook


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


def write_metadata(clip: Path, title_base: str, idx: int, platform: str, hook: str | None):
    """Write a sidecar .txt with a ready-to-paste title + caption for the platform(s)."""
    headline = (hook or title_base).strip().rstrip(".!?")
    title = f"{headline} 🔥 (#{idx})"
    body = (f"TITLE:\n{title}\n\nCAPTION:\n{headline} — League of Legends highlight.\n"
            f"{_hashtags(platform)}\n")
    clip.with_suffix(".txt").write_text(body, encoding="utf-8")


def cut_clip(video, start, dur, idx, layout, caption, subs, dims,
             cap_size=66, peak_pos=0.65, facecam_override=None,
             title="Highlight", platform="youtube") -> Path:
    out = CLIPS_DIR / f"short_{idx:02d}_{int(start)}s.mp4"
    src_w, src_h = dims

    facecam = None
    if layout == "split":
        facecam = facecam_override or detect_facecam(video, start, dur, src_w, src_h)
        if not facecam:                          # no face -> show the whole video instead
            print(f"[clip {idx}] no facecam detected; falling back to full-video layout")
            layout = "full"
    crop_x = focus_x(video, start, dur, src_w, src_h, spike_frac=peak_pos) if layout == "crop" else 0

    # Don't add captions if the source is already captioned (avoids duplicates).
    if subs and has_existing_captions(video, start, dur, dims):
        print(f"[clip {idx}] source already has captions; skipping added captions")
        subs = False

    ass_path, hook = None, None
    if subs:
        tmp = CLIPS_DIR / f".raw_{idx}.mp4"
        ff("-y", "-ss", str(start), "-i", str(video), "-t", str(dur), "-c", "copy", str(tmp))
        an, margin = caption_anchor(layout, dims)     # placement is decided by layout
        ass_path, hook = make_dynamic_captions(tmp, an, margin, max(48, cap_size))
        tmp.unlink(missing_ok=True)

    cap_an, cap_margin = caption_anchor(layout, dims)
    ff("-y", "-ss", str(start), "-i", str(video), "-t", str(dur),
       "-vf", build_vf(layout, dims, crop_x, facecam, ass_path, caption, cap_size, cap_an, cap_margin),
       *_ENC, "-pix_fmt", "yuv420p",
       "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k",
       "-movflags", "+faststart", str(out))
    if ass_path:
        ass_path.unlink(missing_ok=True)
    write_metadata(out, title, idx, platform, hook)   # title + caption sidecar
    print(f"[clip] {out}  ({out.stat().st_size // 1_000_000} MB)")
    return out


def make_clips(video: Path, *, max_clips=5, clip_len=45, peak_pos=0.65, layout="full",
               caption=None, subs=False, cap_size=66, title="Highlight",
               platform="youtube", progress=None) -> list[Path]:
    """Full local pipeline on a downloaded video. Shared by CLI + web."""
    dims = _dims(video)
    moments = find_hype_moments(video, clip_len, max_clips, peak_pos)
    clips = []
    for i, t in enumerate(moments, 1):
        clips.append(cut_clip(video, t, clip_len, i, layout, caption, subs, dims,
                              cap_size=cap_size, peak_pos=peak_pos,
                              title=title, platform=platform))
        if progress:
            progress(i, len(moments))
    return clips


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
    body = {"snippet": {"title": f"{title} #{idx}",
                        "description": "#LeagueOfLegends #LoL #Shorts #Gaming",
                        "tags": ["LeagueOfLegends", "LoL", "Shorts", "Gaming"],
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
    p.add_argument("--layout", choices=["crop", "full", "split"], default="full",
                   help="full=whole video, captions under it (default); crop=motion-tracked "
                        "zoom; split=facecam on top + gameplay on bottom (auto-detects facecam)")
    p.add_argument("--caption", help="static headline text burned onto every clip")
    p.add_argument("--cap-size", type=int, default=66, help="caption font size (66)")
    p.add_argument("--subtitles", action="store_true",
                   help="dynamic word-by-word captions from speech (needs faster-whisper); "
                        "auto-skipped if the source is already captioned")
    p.add_argument("--platform", choices=["youtube", "tiktok", "both"], default="youtube",
                   help="platform the generated title/caption sidecar targets (youtube)")
    p.add_argument("--title", default="LoL Highlight", help="base title for clips and --draft")
    p.add_argument("--draft", action="store_true", help="ALSO upload as PRIVATE drafts")
    p.add_argument("--list-channels", action="store_true",
                   help="show which channel --draft would use, then exit")
    a = p.parse_args()

    if a.list_channels:
        return show_channel()
    if not a.url:
        p.error("a YouTube URL is required (unless using --list-channels)")

    print(f"\n=== LoL Clipper ===\n{a.max_clips} x {a.clip_len}s | layout={a.layout}\n")
    video = download_video(a.url)
    clips = make_clips(video, max_clips=a.max_clips, clip_len=a.clip_len,
                       peak_pos=a.peak_pos, layout=a.layout, caption=a.caption,
                       subs=a.subtitles, cap_size=a.cap_size,
                       title=a.title, platform=a.platform)

    print(f"\n[done] {len(clips)} clips in ./{CLIPS_DIR}/")
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
