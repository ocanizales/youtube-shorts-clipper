"""YouTube connect + draft-upload wiring.

Covers the parts that are cheap to get wrong and expensive to notice: who is
allowed to reach the upload route, what happens before setup is done, and that a
clip name from a form POST cannot escape clips/.

Does NOT talk to Google. The OAuth exchange and the upload itself need real
credentials and a real network, so they are exercised by hand — everything up to
the redirect is covered here.

Run: .venv/bin/python tests/test_youtube.py
"""
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Point the DB at a throwaway file BEFORE anything imports it, so a test run
# never touches the real data.db (which holds live user tokens).
import web.db as db  # noqa: E402

_TMP = tempfile.TemporaryDirectory()
db.DB_PATH = pathlib.Path(_TMP.name) / "test.db"

import web.app as webapp  # noqa: E402
import web.youtube as yt  # noqa: E402

db.init_db()
webapp.app.config["TESTING"] = True


def client():
    return webapp.app.test_client()


def signed_in():
    """A client carrying a real account cookie."""
    c = client()
    c.post("/account", json={"email": f"t{db.secrets.token_hex(4)}@example.com"})
    return c


# ── before setup ─────────────────────────────────────────────────────────────
def test_status_reports_unconfigured_without_client_secrets():
    """The UI keys off this to show setup help instead of a dead button."""
    if yt.is_configured():
        return  # owner has since added real secrets; nothing to assert
    s = client().get("/youtube/status").get_json()
    assert s["configured"] is False and s["connected"] is False
    assert s["redirect_uri"], "the UI has to be able to show the redirect URI"


def test_connect_refuses_before_setup():
    if yt.is_configured():
        return
    r = signed_in().get("/youtube/connect")
    assert r.status_code == 503, "should say 'not set up', not redirect to Google"


# ── auth gates ───────────────────────────────────────────────────────────────
def test_connect_requires_an_account():
    assert client().get("/youtube/connect").status_code == 401


def test_upload_requires_an_account():
    assert client().post("/youtube/upload", json={"clip": "x.mp4"}).status_code == 401


def test_upload_requires_a_connected_channel():
    r = signed_in().post("/youtube/upload", json={"clip": "x.mp4"})
    assert r.status_code == 400
    assert "connect" in r.get_json()["error"].lower()


# ── the clip name is attacker-controlled ─────────────────────────────────────
def _connect_fake_channel(c):
    """Register a channel directly in the DB so upload-route checks can be
    reached without a real OAuth round trip."""
    u = db.get_user_by_token(c.get_cookie("token").value)
    db.save_youtube_account(u["id"], "UC_test", "Test Channel",
                            json.dumps({"token": "x", "refresh_token": "y",
                                        "token_uri": "z", "client_id": "a",
                                        "client_secret": "b", "scopes": yt.SCOPES}))
    return u


def test_path_traversal_cannot_reach_outside_clips():
    c = signed_in()
    _connect_fake_channel(c)
    for evil in ("../data.db", "../../etc/passwd", "../client_secrets.json"):
        r = c.post("/youtube/upload", json={"clip": evil})
        assert r.status_code == 404, f"traversal accepted: {evil}"


def test_unknown_clip_is_rejected():
    c = signed_in()
    _connect_fake_channel(c)
    assert c.post("/youtube/upload", json={"clip": "nope.mp4"}).status_code == 404


def test_real_clip_enqueues_an_upload_job():
    c = signed_in()
    u = _connect_fake_channel(c)
    webapp.CLIPS.mkdir(exist_ok=True)
    clip = webapp.CLIPS / "test_enqueue_clip.mp4"
    clip.write_bytes(b"not really an mp4")
    try:
        r = c.post("/youtube/upload", json={"clip": clip.name})
        assert r.status_code == 200, r.get_json()
        job = db.get_job(r.get_json()["job_id"])
        # kind is what the worker branches on; a render here would run ffmpeg
        # over the clip instead of uploading it.
        assert job["kind"] == "youtube"
        assert job["user_id"] == u["id"]
        assert job["source"] == str(clip.resolve())
    finally:
        clip.unlink(missing_ok=True)


# ── disconnect ───────────────────────────────────────────────────────────────
def test_disconnect_forgets_the_channel():
    c = signed_in()
    u = _connect_fake_channel(c)
    assert db.get_youtube_account(u["id"]) is not None
    assert c.post("/youtube/disconnect").status_code == 200
    assert db.get_youtube_account(u["id"]) is None


# ── hard rules that must not drift ───────────────────────────────────────────
def test_uploads_are_private_and_that_is_not_a_parameter():
    """CLAUDE.md: never auto-published. Enforced in code, not by a default —
    upload_draft must not grow a privacy argument."""
    import inspect
    src = inspect.getsource(yt.upload_draft)
    assert '"privacyStatus": "private"' in src
    assert "public" not in src.lower().replace("publicly", "")
    params = inspect.signature(yt.upload_draft).parameters
    assert "privacy" not in params and "privacy_status" not in params


def test_insecure_transport_is_only_relaxed_for_loopback():
    """Allowing plain http to a non-loopback host would leak the auth code."""
    import os
    saved_env = os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)
    saved_uri = os.environ.get("YT_REDIRECT_URI")
    try:
        os.environ["YT_REDIRECT_URI"] = "http://example.com/youtube/callback"
        yt._allow_http_localhost()
        assert "OAUTHLIB_INSECURE_TRANSPORT" not in os.environ, \
            "relaxed HTTPS enforcement for a remote host"
        os.environ["YT_REDIRECT_URI"] = "http://localhost:5000/youtube/callback"
        yt._allow_http_localhost()
        assert os.environ.get("OAUTHLIB_INSECURE_TRANSPORT") == "1"
    finally:
        os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)
        if saved_env is not None:
            os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = saved_env
        os.environ.pop("YT_REDIRECT_URI", None)
        if saved_uri is not None:
            os.environ["YT_REDIRECT_URI"] = saved_uri


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"PASS {name}")
    print("youtube tests passed")
