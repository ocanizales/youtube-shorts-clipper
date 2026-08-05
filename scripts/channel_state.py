"""Report which YouTube channel token.pickle is connected to, as JSON.

A state probe for the dashboard, which is stdlib-only and cannot unpickle
google credentials itself. Prints the channel name, never the token.

Deliberately does no network call in the common case: it reads the stored
channel label written at connect time when there is one, and only asks Google
when the label is missing. A dashboard page load must not cost an API round
trip, and must not fail if the network is down.

Run: .venv/bin/python scripts/channel_state.py
"""
import json
import pathlib
import pickle
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TOKEN = ROOT / "token.pickle"
LABEL = ROOT / ".youtube_channel"     # plain text, written at connect time


def main() -> int:
    if not TOKEN.exists():
        print(json.dumps({"connected": False}))
        return 0
    try:
        creds = pickle.loads(TOKEN.read_bytes())
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"connected": False, "detail": f"unreadable token: {exc}"}))
        return 0
    title = LABEL.read_text().strip() if LABEL.exists() else ""
    if not title:
        try:
            import web.youtube as yt
            title = yt._fetch_channel(creds)["title"]
            LABEL.write_text(title)
        except Exception:  # noqa: BLE001 — offline is not "disconnected"
            title = "(connected)"
    print(json.dumps({"connected": True, "channel_title": title,
                      "has_refresh": bool(getattr(creds, "refresh_token", None))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
