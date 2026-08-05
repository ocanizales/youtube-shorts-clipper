"""
"Connect YouTube" for the web app: OAuth web flow + private-draft uploads.

WHY THIS IS NOT `clipper._youtube()`
------------------------------------
The CLI authenticates with `InstalledAppFlow.run_local_server(port=0)` and
pickles one global `token.pickle`. That is a *desktop* flow: it spawns a browser
on the machine running the code and binds an ephemeral localhost port. On a
headless VPS there is no browser to spawn, and one shared pickle cannot express
"this user connected this channel". So the web tier gets a proper server-side
flow — the user's own browser does the consent, we exchange the code, and the
credentials land in SQLite keyed by user.

Both paths stay: the CLI keeps `--draft`, the web app gets a button. They do not
share state, and connecting one does not connect the other.

THE REDIRECT URI IS THE PART THAT BREAKS
----------------------------------------
Google matches the redirect URI EXACTLY against what is registered in the Cloud
Console — scheme, host, port, path, no trailing slash. A mismatch is the
`redirect_uri_mismatch` error and it is the single most common way this setup
fails. It is one env var here precisely so it can be fixed without a code edit:

    YT_REDIRECT_URI=http://localhost:5000/youtube/callback   (default)

Google exempts `localhost` from its HTTPS requirement, which is why the default
works over an SSH tunnel with no certificate. Any OTHER host must be https.

SECURITY
--------
`youtube_accounts.creds` holds a refresh token, which is a long-lived key to the
upload scope of someone's channel. Anyone who can read `data.db` can post to
that channel. Keep the DB off shared storage and out of git, and keep
`client_secrets.json` gitignored — the ignore list is the only guard, there is
no pre-push hook on this box despite what earlier notes claimed.

The saved blob also carries `client_secret` (see `_save`), and refresh uses that
stored copy. So resetting the client secret in the Console INVALIDATES every
already-connected account: rotate first, connect second, or you will have to
reconnect.

Uploads are ALWAYS `privacyStatus: private`. That is a hard rule in CLAUDE.md and
it is enforced here in code, not left to a caller's argument — see `upload_draft`.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import web.db as db  # noqa: E402

CLIENT_SECRETS = ROOT / "client_secrets.json"

# youtube.upload is what actually posts. youtube.readonly is only used to read
# back the channel name so the UI can show WHICH channel is connected — without
# it the user has no way to tell they authorised the wrong account, which is the
# mistake worth catching before an upload, not after.
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly"]

DEFAULT_REDIRECT = "http://localhost:5000/youtube/callback"


def redirect_uri() -> str:
    return os.environ.get("YT_REDIRECT_URI", DEFAULT_REDIRECT)


def is_configured() -> bool:
    """False until the owner drops in client_secrets.json. The UI asks this so it
    can show setup instructions instead of a Connect button that cannot work."""
    return CLIENT_SECRETS.exists()


def _allow_http_localhost() -> None:
    """oauthlib refuses non-HTTPS redirects unless told otherwise.

    Scoped deliberately: only relaxed when the redirect really is loopback. A
    plain-http redirect to any other host would send an authorization code
    across the network in the clear, so that case keeps the error.
    """
    uri = redirect_uri()
    if uri.startswith("http://localhost") or uri.startswith("http://127.0.0.1"):
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"


def build_flow(state: str | None = None):
    from google_auth_oauthlib.flow import Flow
    _allow_http_localhost()
    flow = Flow.from_client_secrets_file(
        str(CLIENT_SECRETS), scopes=SCOPES, state=state)
    flow.redirect_uri = redirect_uri()
    return flow


def authorization_url() -> tuple[str, str]:
    """(url, state) to send the user to Google.

    `access_type=offline` + `prompt=consent` is what makes this a *connect once*
    feature: without offline Google returns no refresh token, and without the
    explicit consent prompt it silently omits the refresh token on every
    re-authorisation after the first — the classic "works today, 401s tomorrow".
    """
    flow = build_flow()
    return flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent")


def _creds_to_json(creds) -> str:
    return json.dumps({
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or SCOPES),
    })


def _creds_from_json(raw: str):
    from google.oauth2.credentials import Credentials
    return Credentials(**json.loads(raw))


def finish_connect(user_id: str, authorization_response: str) -> dict:
    """Exchange the callback URL for credentials and store them.

    Returns {'channel_id', 'channel_title'}. Raises on failure — the caller turns
    that into a visible error, because a silent half-connected state is worse
    than a red banner.
    """
    _allow_http_localhost()
    flow = build_flow()
    flow.fetch_token(authorization_response=authorization_response)
    creds = flow.credentials
    chan = _fetch_channel(creds)
    db.save_youtube_account(user_id, chan["id"], chan["title"],
                            _creds_to_json(creds))
    return chan


def _fetch_channel(creds) -> dict:
    from googleapiclient.discovery import build
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    items = yt.channels().list(part="snippet", mine=True).execute().get("items", [])
    if not items:
        # A Google account with no YouTube channel authenticates fine and then
        # fails at upload time with an opaque error. Catch it here instead.
        raise RuntimeError(
            "That Google account has no YouTube channel. Create one at "
            "youtube.com, then connect again.")
    return {"id": items[0]["id"], "title": items[0]["snippet"]["title"]}


def service_for_user(user_id: str):
    """An authorised YouTube client for this user, refreshing if needed.

    A refreshed access token is written back so the next upload starts warm; a
    refresh token revoked from the Google side surfaces as a clear message
    telling the user to reconnect, rather than a traceback.
    """
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    acct = db.get_youtube_account(user_id)
    if not acct:
        raise RuntimeError("No YouTube channel connected.")
    creds = _creds_from_json(acct["creds"])
    if not creds.valid:
        if not (creds.expired and creds.refresh_token):
            raise RuntimeError("YouTube access expired. Reconnect your channel.")
        try:
            creds.refresh(Request())
        except Exception as exc:  # refresh_token revoked, client deleted, etc.
            raise RuntimeError(
                f"YouTube access could not be renewed ({exc.__class__.__name__}). "
                "Reconnect your channel.") from exc
        db.update_youtube_creds(user_id, _creds_to_json(creds))
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload_draft(user_id: str, clip: Path, on_progress=None) -> dict:
    """Upload one rendered clip as a PRIVATE draft. Returns {'video_id','url'}.

    Metadata comes from the sidecar `.txt` that `clipper.write_metadata` wrote,
    so the title/caption/tags the grounded metadata pass produced are exactly
    what gets posted — no second generation, no drift between what the UI shows
    and what YouTube receives.

    `privacyStatus` is hardcoded private and takes no argument. Making it a
    parameter would make "publish publicly" one wrong call away, and the rule
    that nothing is auto-published is not a default, it is the point.
    """
    import clipper
    from googleapiclient.http import MediaFileUpload

    clip = Path(clip)
    if not clip.exists():
        raise RuntimeError(f"Clip not found: {clip.name}")

    side = clipper._read_sidecar(clip)
    tags = [t.strip() for t in side.get("TAGS", "").split(",") if t.strip()]
    body = {
        "snippet": {
            "title": (side.get("TITLE") or clip.stem)[:100],  # YouTube hard limit
            "description": side.get("CAPTION") or "#LeagueOfLegends #LoL #Shorts",
            "tags": tags or ["LeagueOfLegends", "LoL", "Shorts"],
            "categoryId": "20",  # Gaming
        },
        "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(str(clip), mimetype="video/mp4", resumable=True)
    req = service_for_user(user_id).videos().insert(
        part="snippet,status", body=body, media_body=media)

    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status and on_progress:
            on_progress(int(status.progress() * 100))
    return {"video_id": resp["id"],
            "url": f"https://studio.youtube.com/video/{resp['id']}/edit"}


def status_for(user_id: str) -> dict:
    """What the UI needs to decide between 'Connect', 'Send', and 'set me up'."""
    if not is_configured():
        return {"configured": False, "connected": False,
                "redirect_uri": redirect_uri()}
    acct = db.get_youtube_account(user_id) if user_id else None
    return {"configured": True, "connected": bool(acct),
            "channel_title": acct["channel_title"] if acct else None,
            "channel_id": acct["channel_id"] if acct else None,
            "redirect_uri": redirect_uri()}
