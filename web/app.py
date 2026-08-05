"""
Web front-end for the LoL Shorts Clipper.

The web tier is now stateless: it enqueues jobs into SQLite and reads status back.
A separate process (web/worker.py) does the encoding. Run both:
    python web/worker.py      # one or more
    python web/app.py         # the web server
Then open http://localhost:5000
"""

import json
import os
import secrets as pysecrets
import sys
import uuid
from pathlib import Path

from flask import (Flask, g, jsonify, make_response, redirect, render_template,
                   request, send_from_directory, session)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import clipper  # noqa: E402  — for LAYOUTS, the single source of truth
import web.db as db  # noqa: E402
import web.youtube as yt  # noqa: E402

UPLOADS, CLIPS = ROOT / "uploads", ROOT / "clips"
for d in (UPLOADS, CLIPS, ROOT / "downloads"):
    d.mkdir(exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 ** 3  # 4 GB uploads


def _secret_key() -> bytes:
    """Stable across restarts, or every restart would log everyone out and void
    any OAuth handshake in flight. Generated once into a gitignored file rather
    than hardcoded; FLASK_SECRET overrides for multi-process deployments."""
    if os.environ.get("FLASK_SECRET"):
        return os.environ["FLASK_SECRET"].encode()
    path = ROOT / ".flask_secret"
    if not path.exists():
        path.write_bytes(pysecrets.token_bytes(32))
        path.chmod(0o600)
    return path.read_bytes()


app.secret_key = _secret_key()
db.init_db()


# ── identity (lightweight: account keyed by email, opaque token in a cookie) ──
def current_user():
    token = request.cookies.get("token") or request.headers.get("X-Token", "")
    return db.get_user_by_token(token)


def user_payload(u):
    return {"email": u["email"], "plan": u["plan"],
            "whitelisted": bool(u["whitelisted"]), "remaining": db.remaining(u)}


@app.post("/account")
def account():
    email = (request.json or request.form).get("email", "").strip()
    if "@" not in email:
        return jsonify(error="Enter a valid email."), 400
    u = db.get_or_create_user(email)
    if (request.json or request.form).get("newsletter"):
        db.subscribe_newsletter(email, u["id"])
    resp = make_response(jsonify(user_payload(u)))
    resp.set_cookie("token", u["token"], max_age=60 * 60 * 24 * 365,
                    samesite="Lax", httponly=True)
    return resp


@app.get("/me")
def me():
    u = current_user()
    return jsonify(user_payload(u) if u else {"anonymous": True})


@app.post("/redeem")
def redeem():
    u = current_user()
    if not u:
        return jsonify(error="Sign in with your email first."), 401
    code = (request.json or request.form).get("code", "")
    ok, msg = db.redeem_promo(u["id"], code)
    return (jsonify(message=msg, user=user_payload(db.get_user_by_token(u["token"])))
            if ok else (jsonify(error=msg), 400))


@app.post("/newsletter")
def newsletter():
    email = (request.json or request.form).get("email", "").strip()
    if "@" not in email:
        return jsonify(error="Enter a valid email."), 400
    u = current_user()
    db.subscribe_newsletter(email, u["id"] if u else None)
    return jsonify(message="Subscribed.")


# ── processing (enqueue only; the worker does the work) ──────────────────────
@app.post("/process")
def process():
    u = current_user()
    if not u:
        return jsonify(error="Create an account (enter your email) to process videos."), 401
    ok, msg = db.can_process(u)
    if not ok:
        return jsonify(error=msg + " Redeem a code or upgrade to continue."), 402

    opts = {
        # `whole` disables these two in the form, so a whole-video POST arrives
        # without them: read them defensively (an absent field is "" and int("")
        # would raise) and let the layout decide whether they mean anything.
        "max_clips": max(1, min(int(request.form.get("max_clips") or 5), 20)),
        "clip_len": max(5, min(int(request.form.get("clip_len") or 30), 90)),
        "peak_pos": float(request.form.get("peak_pos") or 0.72),
        # HPC cold open — on unless the form explicitly turns it off.
        "teaser": request.form.get("teaser", "on") in ("on", "1", "true"),
        # Only the shipped framings: clipper.LAYOUTS is the single source of
        # truth, and anything else (a stale form, a crafted POST) falls back to
        # full rather than reaching a retired code path or dying in argparse.
        "layout": (request.form.get("layout", "full")
                   if request.form.get("layout") in clipper.LAYOUTS else "full"),
        "caption": request.form.get("caption", "").strip(),
        "cap_size": int(request.form.get("cap_size", 66)),
        "subtitles": request.form.get("subtitles") in ("on", "1", "true"),
        "platform": request.form.get("platform", "youtube"),
        "title": request.form.get("caption", "").strip() or "Highlight",
    }
    file = request.files.get("file")
    url = request.form.get("url", "").strip()
    if file and file.filename:
        path = UPLOADS / f"{uuid.uuid4().hex}_{file.filename}"
        file.save(path)
        source, is_upload = str(path), True
    elif url:
        source, is_upload = url, False
    else:
        return jsonify(error="Provide a YouTube URL or a video file."), 400

    job_id = uuid.uuid4().hex
    db.create_job(job_id, u["id"], source, is_upload, opts)
    return jsonify(job_id=job_id)


@app.get("/status/<job_id>")
def status(job_id):
    job = db.get_job(job_id)
    if not job:
        return jsonify(stage="Unknown job", done=True, error=1)
    return jsonify(stage=job["stage"], progress=job["progress"],
                   done=job["done"], error=job["error"], clips=job["clips"],
                   thumb=job.get("thumb"), kind=job.get("kind", "render"),
                   result_url=job.get("result_url"))


@app.get("/clips/<path:name>")
def clip(name):
    return send_from_directory(CLIPS, name)


# ── YouTube: connect a channel, then send finished clips as private drafts ────
def _safe_clip(name: str) -> Path | None:
    """Resolve a clip name to a real file inside CLIPS, or None.

    The name arrives from a form POST, so it is attacker-controlled: resolve it
    and confirm the result is still under CLIPS before opening anything. Without
    this, "../../etc/passwd" is an upload target.
    """
    path = (CLIPS / name).resolve()
    if path.parent != CLIPS.resolve() or not path.is_file():
        return None
    return path


def _is_loopback() -> bool:
    """True when the request came from this machine.

    The setup paste box below writes a credential to disk, so it must not be
    reachable from the network. The dashboard on :8080 is bound to 0.0.0.0 and
    this app can be too; over `ssh -L` the request still arrives from 127.0.0.1,
    so the intended workflow passes and a remote one does not.

    Deliberately reads request.remote_addr and NOT X-Forwarded-For: that header
    is caller-supplied and would let anyone claim to be localhost.
    """
    return request.remote_addr in ("127.0.0.1", "::1", "::ffff:127.0.0.1")


@app.post("/youtube/setup")
def youtube_setup():
    """Accept a pasted client_secrets.json over loopback, once.

    This exists because the file has to get from the machine running the browser
    onto this box, and every route people reach for is worse: the GitHub web UI
    ignores .gitignore, and the dashboard's /intake files what it receives into
    an Obsidian vault that has a GitHub remote. Both turn a local secret into a
    pushed one. This writes to the single path that is already gitignored, 0600,
    and never reads it back out.
    """
    u = current_user()
    if not u:
        return jsonify(error="Sign in with your email first."), 401
    if not _is_loopback():
        return jsonify(error="Setup is only available from this machine. "
                             "Use: ssh -L 5000:localhost:5000"), 403
    # Replacing a working credential silently would be a foothold for anyone who
    # got this far; make it deliberate instead.
    if yt.is_configured() and not (request.get_json(silent=True) or {}).get("replace"):
        return jsonify(error="Already set up. Send replace:true to overwrite."), 409

    raw = (request.get_json(silent=True) or {}).get("json", "")
    if not raw.strip():
        return jsonify(error="Paste the JSON you downloaded from Google."), 400
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return jsonify(error=f"That is not valid JSON: {exc}"), 400
    # Reject the wrong client type here rather than at the OAuth redirect, where
    # Google's error names neither the file nor the cause.
    if "installed" in data and "web" not in data:
        return jsonify(error="That is a Desktop client. The button needs a "
                             "Web application client — see SETUP.md."), 400
    if "web" not in data:
        return jsonify(error="No 'web' key — not an OAuth client file."), 400
    want = yt.redirect_uri()
    if want not in data["web"].get("redirect_uris", []):
        return jsonify(error=f"This client has no redirect URI {want}. "
                             "Add it in the Console, re-download, paste again."), 400

    # Write 0600 before any bytes land, so it is never briefly world-readable.
    path = yt.CLIENT_SECRETS
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh)
    return jsonify(ok=True, client_id=data["web"].get("client_id", "")[:24] + "…")


@app.get("/youtube/status")
def youtube_status():
    u = current_user()
    s = yt.status_for(u["id"] if u else None)
    # The UI only offers the paste box where it would actually work.
    s["can_setup"] = _is_loopback()
    return jsonify(s)


@app.get("/youtube/connect")
def youtube_connect():
    u = current_user()
    if not u:
        return jsonify(error="Sign in with your email first."), 401
    if not yt.is_configured():
        return jsonify(error="client_secrets.json is missing — see SETUP.md."), 503
    url, state = yt.authorization_url()
    # Tie the callback to this browser: Google echoes `state` back and a mismatch
    # means the response did not originate from the request we sent.
    session["yt_state"] = state
    return redirect(url)


@app.get("/youtube/callback")
def youtube_callback():
    u = current_user()
    if not u:
        return _yt_page("Sign in with your email first, then connect again.", False)
    if request.args.get("error"):
        return _yt_page(f"YouTube declined: {request.args['error']}", False)
    if not request.args.get("state") or request.args["state"] != session.get("yt_state"):
        return _yt_page("That sign-in did not match this browser. Try again.", False)
    session.pop("yt_state", None)
    try:
        chan = yt.finish_connect(u["id"], request.url)
    except Exception as exc:  # noqa: BLE001 — surface the real reason to the user
        return _yt_page(f"Could not connect: {exc}", False)
    return _yt_page(f"Connected to “{chan['title']}”. You can close this tab.", True)


def _yt_page(msg: str, ok: bool) -> str:
    """The callback lands in a browser tab, not in fetch(), so it needs to render
    something a human can read — and tell the opener to refresh its status."""
    colour = "#7CFFB2" if ok else "#FF8A8A"
    return (f"<!doctype html><meta charset=utf-8><title>YouTube</title>"
            f"<body style='background:#252525;color:{colour};font:16px system-ui;"
            f"display:grid;place-items:center;height:100vh;margin:0;text-align:center'>"
            f"<div><p>{msg}</p><p style='color:#888;font-size:13px'>"
            f"Return to the clipper tab.</p></div>"
            f"<script>try{{localStorage.setItem('yt_ping',Date.now())}}catch(e){{}}"
            f"</script></body>")


@app.post("/youtube/disconnect")
def youtube_disconnect():
    u = current_user()
    if not u:
        return jsonify(error="Not signed in."), 401
    db.delete_youtube_account(u["id"])
    return jsonify(ok=True)


@app.post("/youtube/upload")
def youtube_upload():
    u = current_user()
    if not u:
        return jsonify(error="Sign in with your email first."), 401
    if not db.get_youtube_account(u["id"]):
        return jsonify(error="Connect your YouTube channel first."), 400
    name = (request.json or request.form).get("clip", "")
    path = _safe_clip(name)
    if not path:
        return jsonify(error="Unknown clip."), 404
    # Uploading a 1080p clip takes far longer than a request should, so it rides
    # the same queue as renders and the UI polls /status/<id> exactly as it
    # already does for cutting.
    job_id = uuid.uuid4().hex
    db.create_job(job_id, u["id"], str(path), False, {"clip": name}, kind="youtube")
    return jsonify(job_id=job_id)


@app.get("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(debug=True, port=5000)
