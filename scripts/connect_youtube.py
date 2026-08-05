"""Connect a YouTube channel from a machine with no browser.

The redirect flow cannot be completed on this box: Google refuses a redirect URI
that is plain http or a raw IP, and there is no browser here to reach a
localhost one. The device flow has no redirect at all — Google issues a short
code, the user types it at google.com/device on their phone or laptop, and this
polls until it is approved.

Writes `token.pickle`, which is exactly what `clipper.py --draft` already reads,
so connecting here makes the existing CLI upload path work unchanged.

Used two ways:
  start                 -> prints JSON with user_code / verification_url
  poll <device_code>    -> prints JSON {"status": ...}, one poll per call
The dashboard shells out to this (it is stdlib-only and cannot import google
libraries); a human can run it directly and follow the printed instructions.

  .venv/bin/python scripts/connect_youtube.py            # interactive
"""
import json
import os
import pathlib
import pickle
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import web.youtube as yt  # noqa: E402

TOKEN = ROOT / "token.pickle"


def _save(creds) -> dict:
    """Persist as token.pickle, 0600 — it holds a refresh token, which is
    long-lived write access to the channel.

    Reading back the channel name is best-effort and must never fail the
    connect. The approval has already happened at this point and the refresh
    token is the thing worth keeping; if the name lookup dies — the YouTube Data
    API not yet enabled on the project is the common one — the account is still
    connected. Reporting that as a failure sent one user round the whole consent
    flow again for nothing.
    """
    fd = os.open(TOKEN, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        pickle.dump(creds, fh)
    try:
        chan = yt._fetch_channel(creds)
    except Exception as exc:  # noqa: BLE001 — connected either way
        detail = "the YouTube Data API is not enabled on this project" \
            if "accessNotConfigured" in str(exc) else str(exc)[:200]
        return {"id": "", "title": "", "warning": detail}
    # Cache the label so the dashboard's state probe costs no API call.
    (ROOT / ".youtube_channel").write_text(chan["title"])
    return chan


def cmd_start() -> int:
    try:
        out = yt.device_start()
    except Exception as exc:  # noqa: BLE001 — the message is the product here
        print(json.dumps({"error": str(exc)}))
        return 1
    print(json.dumps({
        "device_code": out["device_code"],
        "user_code": out["user_code"],
        "verification_url": out.get("verification_url")
                            or out.get("verification_uri"),
        "interval": out.get("interval", 5),
        "expires_in": out.get("expires_in", 1800),
    }))
    return 0


def cmd_poll(device_code: str) -> int:
    try:
        status, payload = yt.device_exchange(device_code)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "error", "detail": str(exc)}))
        return 1
    if status != "connected":
        print(json.dumps({"status": status,
                          "detail": payload if isinstance(payload, str) else ""}))
        return 0
    chan = _save(payload)
    out = {"status": "connected", "channel_title": chan["title"],
           "channel_id": chan["id"]}
    if chan.get("warning"):
        out["warning"] = chan["warning"]
    print(json.dumps(out))
    return 0


def interactive() -> int:
    try:
        out = yt.device_start()
    except Exception as exc:  # noqa: BLE001
        print(f"[auth] {exc}")
        return 1
    url = out.get("verification_url") or out.get("verification_uri")
    print(f"\n  1. On any device, open:  {url}")
    print(f"  2. Enter this code:      {out['user_code']}\n")
    print("  Waiting for approval… (Ctrl-C to stop)")
    interval, deadline = out.get("interval", 5), time.time() + out.get("expires_in", 1800)
    while time.time() < deadline:
        time.sleep(interval)
        status, payload = yt.device_exchange(out["device_code"])
        if status == "pending":
            if payload == "slow_down":
                interval += 5      # Google asks for this explicitly; obey it
            continue
        if status != "connected":
            print(f"  [auth] {payload}")
            return 1
        chan = _save(payload)
        print(f"\n  Connected to “{chan['title'] or 'your channel'}”.")
        if chan.get("warning"):
            print(f"  [warn] {chan['warning']} — the connection is fine, but "
                  f"uploads will fail until that is fixed.")
        print(f"  Wrote {TOKEN.name}; `clipper.py --draft` will use it.")
        return 0
    print("  [auth] the code expired before it was approved.")
    return 1


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "start":
        raise SystemExit(cmd_start())
    if arg == "poll":
        if len(sys.argv) < 3:
            print(json.dumps({"status": "error", "detail": "poll needs a device_code"}))
            raise SystemExit(1)
        raise SystemExit(cmd_poll(sys.argv[2]))
    raise SystemExit(interactive())
