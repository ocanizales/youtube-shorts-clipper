"""
Web front-end for the LoL Shorts Clipper.

The web tier is now stateless: it enqueues jobs into SQLite and reads status back.
A separate process (web/worker.py) does the encoding. Run both:
    python web/worker.py      # one or more
    python web/app.py         # the web server
Then open http://localhost:5000
"""

import sys
import uuid
from pathlib import Path

from flask import (Flask, g, jsonify, make_response, render_template, request,
                   send_from_directory)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import web.db as db  # noqa: E402

UPLOADS, CLIPS = ROOT / "uploads", ROOT / "clips"
for d in (UPLOADS, CLIPS, ROOT / "downloads"):
    d.mkdir(exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 ** 3  # 4 GB uploads
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
        "max_clips": max(1, min(int(request.form.get("max_clips", 5)), 20)),
        "clip_len": max(5, min(int(request.form.get("clip_len", 45)), 90)),
        "peak_pos": float(request.form.get("peak_pos", 0.65)),
        "layout": request.form.get("layout", "full"),
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
                   thumb=job.get("thumb"))


@app.get("/clips/<path:name>")
def clip(name):
    return send_from_directory(CLIPS, name)


@app.get("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(debug=True, port=5000)
