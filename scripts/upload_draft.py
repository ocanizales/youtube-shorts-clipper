"""Upload one rendered clip to YouTube as a PRIVATE draft, using token.pickle.

WHY A THIRD UPLOAD PATH EXISTS
------------------------------
There were two, and neither was reachable from the dashboard the user actually
works in:

  * `clipper.py --draft` uploads from the CLI, but only as part of a render run
    — there is no "upload this finished clip" entry point.
  * `web/youtube.py` has one, but it reads credentials from `data.db`, which is
    the Flask app's store on :5000. The dashboard's Connect button writes
    `token.pickle`. Connecting on the dashboard therefore left the :5000 Send
    button still saying "connect your channel first" — two stores, and the user
    had filled the one with no consumer.

This script is the missing half: it uploads an ALREADY-RENDERED clip, reading
the same `token.pickle` the dashboard's own Connect flow writes. The dashboard
is stdlib-only and cannot unpickle google credentials or import googleapiclient,
so it shells out to this, exactly as it already does for renders and for
`channel_state.py`.

NEVER PROMPTS
-------------
`clipper._youtube()` falls back to `InstalledAppFlow.run_local_server()` when
the token is missing, which on a headless VPS blocks forever waiting for a
browser that will never open. This path refuses instead: a missing or
unrefreshable token is a clean error the dashboard can render as "reconnect".

PRIVACY
-------
`privacyStatus: private` is hardcoded and takes no argument, matching
`clipper.upload_draft` and `web.youtube.upload_draft`. CLAUDE.md makes this a
hard rule: nothing this project uploads is ever auto-published, and the way that
stays true is that no caller is given a knob to change it.

Run: .venv/bin/python scripts/upload_draft.py <clip-filename>
Emits one JSON object per line: {"progress": 0-100} then {"video_id", "url"},
or {"error": "..."} — line-oriented so the caller can stream it.
"""
import json
import pathlib
import pickle
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TOKEN = ROOT / "token.pickle"
CLIPS = ROOT / "clips"


def _emit(**obj) -> None:
    """One JSON object per line, flushed — the caller streams these live."""
    print(json.dumps(obj), flush=True)


def _credentials():
    """Valid credentials from token.pickle, refreshed and written back.

    Raises RuntimeError with something a user can act on. The refresh is written
    back so the next upload starts warm rather than spending a round trip.
    """
    if not TOKEN.exists():
        raise RuntimeError("No YouTube channel connected. Use Connect YouTube first.")
    try:
        creds = pickle.loads(TOKEN.read_bytes())
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"token.pickle is unreadable ({exc}). Reconnect.") from exc
    if creds.valid:
        return creds
    if not (creds.expired and getattr(creds, "refresh_token", None)):
        raise RuntimeError("YouTube access expired and cannot renew. Reconnect.")
    from google.auth.transport.requests import Request
    try:
        creds.refresh(Request())
    except Exception as exc:  # revoked, client secret rotated, project deleted
        # Rotating the OAuth client secret in the Console invalidates every
        # stored refresh token — see web/youtube.py. Naming that here saves the
        # next person the hour it cost to find.
        raise RuntimeError(
            f"YouTube access could not be renewed ({exc.__class__.__name__}). "
            "If the client secret was rotated, reconnect the channel.") from exc
    TOKEN.write_bytes(pickle.dumps(creds))
    return creds


def _resolve(name: str) -> pathlib.Path:
    """`name` -> a real file inside clips/, or raise.

    The dashboard passes a filename that arrived over HTTP. It is reduced to its
    basename and the RESOLVED path is required to sit inside clips/, so neither
    "../../etc/passwd" nor a symlink out of the directory reaches the uploader.
    """
    clip = (CLIPS / pathlib.Path(name).name).resolve()
    if clip.parent != CLIPS.resolve() or not clip.is_file():
        raise RuntimeError(f"Unknown clip: {pathlib.Path(name).name}")
    if clip.suffix.lower() != ".mp4":
        raise RuntimeError("Only rendered .mp4 clips can be uploaded.")
    return clip


def upload(name: str) -> int:
    import clipper
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    clip = _resolve(name)
    creds = _credentials()

    # Metadata comes from the sidecar the render already wrote, so what gets
    # posted is exactly what the dashboard showed — no second generation, no
    # drift between the two.
    side = clipper._read_sidecar(clip)
    tags = [t.strip() for t in side.get("TAGS", "").split(",") if t.strip()]
    body = {
        "snippet": {
            "title": (side.get("TITLE") or clip.stem)[:100],   # YouTube hard limit
            "description": side.get("CAPTION") or "#LeagueOfLegends #LoL #Shorts",
            "tags": tags or ["LeagueOfLegends", "LoL", "Shorts"],
            "categoryId": "20",                                # Gaming
        },
        "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False},
    }
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    req = yt.videos().insert(
        part="snippet,status", body=body,
        media_body=MediaFileUpload(str(clip), mimetype="video/mp4", resumable=True))

    resp, last = None, -1
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            if pct != last:                 # one line per percent, not per chunk
                _emit(progress=pct)
                last = pct
    _emit(progress=100, video_id=resp["id"],
          url=f"https://studio.youtube.com/video/{resp['id']}/edit")
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        _emit(error="usage: upload_draft.py <clip-filename>")
        return 2
    try:
        return upload(sys.argv[1])
    except RuntimeError as exc:
        _emit(error=str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001 — the dashboard needs a message, not a traceback
        _emit(error=f"{exc.__class__.__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
