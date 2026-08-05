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


# ── device flow: connecting without a browser on this machine ────────────────
# Google will not accept a redirect URI that is plain http or a raw IP, which is
# what a VPS dashboard is, so the redirect flow above can only be completed over
# localhost or https. The device flow sidesteps redirects entirely: we show a
# short code, the user types it at google.com/device on any device they like,
# and we poll. Nothing needs to be reachable from the internet.
#
# The cost is scope. youtube.upload is NOT among the scopes Google allows here
# (only `youtube`, `youtube.readonly`, and a few non-YouTube ones), and
# `youtube` is broader — it is full account management, not just upload.
# videos.insert accepts it, so uploads work. The narrowing is done in code
# instead: upload_draft is the only call this module ever makes against a
# channel, and its privacyStatus is hardcoded.
DEVICE_SCOPES = ["https://www.googleapis.com/auth/youtube"]
DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
DEVICE_TOKEN_URL = "https://oauth2.googleapis.com/token"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


def _client_pair() -> tuple[str, str]:
    """(client_id, client_secret) from whichever block the file carries.

    A "TVs and Limited Input devices" client downloads under `installed`, the
    same key a Desktop client uses. They are not interchangeable for the redirect
    flow, but for the device flow only these two fields matter.
    """
    with open(CLIENT_SECRETS) as fh:
        data = json.load(fh)
    block = data.get("installed") or data.get("web") or {}
    if not block.get("client_id"):
        raise RuntimeError("client_secrets.json has no client_id")
    return block["client_id"], block.get("client_secret", "")


def _post_form(url: str, fields: dict) -> dict:
    """POST application/x-www-form-urlencoded, return parsed JSON.

    urllib rather than requests: this module is imported by the stdlib-only
    dashboard's subprocess path, and an HTTP error here carries a JSON body we
    need to read (`authorization_pending` arrives as a 428/400), so the error
    case is parsed rather than raised.
    """
    import urllib.error
    import urllib.parse
    import urllib.request
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type":
                                          "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode())
        except Exception:
            raise RuntimeError(f"Google returned HTTP {exc.code}") from exc


def device_start() -> dict:
    """Ask Google for a user code. Returns the dict the UI needs to display."""
    cid, _ = _client_pair()
    out = _post_form(DEVICE_CODE_URL,
                     {"client_id": cid, "scope": " ".join(DEVICE_SCOPES)})
    if "device_code" not in out:
        # The usual cause is a client that is not of type "TVs and Limited Input
        # devices"; say so rather than surfacing a bare invalid_client. Google
        # HTML-escapes the quotes in its own message, which reads badly in a
        # terminal or a text node.
        import html
        raise RuntimeError(html.unescape(
            out.get("error_description") or out.get("error")
            or "Google did not return a device code. Is this a "
               "'TVs and Limited Input devices' OAuth client?"))
    return out


def device_exchange(device_code: str) -> tuple[str, object]:
    """Poll Google once. Returns (status, payload).

    status is 'pending' | 'connected' | 'denied' | 'expired' | 'error'; payload
    is a Credentials on 'connected' and a human-readable string otherwise. Split
    out from device_poll so the CLI path can store a token.pickle and the web
    path can store a DB row without either duplicating the protocol.

    Polling is driven by the caller so a slow approval never holds a request
    open. `authorization_pending` and `slow_down` are both normal and must not
    be treated as failures.
    """
    cid, secret = _client_pair()
    out = _post_form(DEVICE_TOKEN_URL, {
        "client_id": cid, "client_secret": secret,
        "device_code": device_code, "grant_type": DEVICE_GRANT,
    })
    err = out.get("error")
    if err in ("authorization_pending", "slow_down"):
        return "pending", err
    if err == "access_denied":
        return "denied", "You declined the request in Google."
    if err == "expired_token":
        return "expired", "That code expired. Start again."
    if err:
        return "error", (out.get("error_description") or err)

    from google.oauth2.credentials import Credentials
    creds = Credentials(
        token=out["access_token"], refresh_token=out.get("refresh_token"),
        token_uri=DEVICE_TOKEN_URL, client_id=cid, client_secret=secret,
        scopes=DEVICE_SCOPES)
    if not creds.refresh_token:
        # Without it the connection dies at the first token expiry, an hour
        # later, which is the worst time to discover it.
        return "error", ("Google returned no refresh token; revoke this app at "
                         "myaccount.google.com/permissions and connect again.")
    return "connected", creds


def device_poll(user_id: str, device_code: str) -> dict:
    """device_exchange, stored as a web-app account row."""
    status, payload = device_exchange(device_code)
    if status != "connected":
        return {"status": status, "detail": payload if isinstance(payload, str) else ""}
    chan = _fetch_channel(payload)
    db.save_youtube_account(user_id, chan["id"], chan["title"],
                            _creds_to_json(payload))
    return {"status": "connected", "channel_title": chan["title"],
            "channel_id": chan["id"]}


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
