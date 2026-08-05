"""Sanity-check client_secrets.json before you find out the hard way.

Two mistakes cost the most time here and neither one announces itself:

  1. Downloading a *Desktop* client when the web app needs a *Web application*
     one. Both files are called client_secret_....json and both look identical
     at a glance. The web app fails at the point where it builds the flow.
  2. A redirect URI that is registered *almost* right. Google answers with
     redirect_uri_mismatch only after you have already signed in, which reads
     like a login problem rather than a config typo.

Run: .venv/bin/python scripts/check_oauth_client.py

Prints nothing secret. The client_id is shown truncated because it is the one
field you may need to match against the Console; the client_secret is never
read or echoed.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PATH = ROOT / "client_secrets.json"


def main() -> int:
    if not PATH.exists():
        print(f"MISSING  {PATH}")
        print("\n  It downloads to the machine running the browser, not to this")
        print("  one. From THAT machine (fill in your own user@host):")
        print("    scp ~/Downloads/client_secret_*.json \\")
        print(f"        user@host:{PATH}")
        return 1

    # A world-readable secret on a multi-user box is a real exposure, and the
    # browser download almost always arrives as 0644.
    mode = PATH.stat().st_mode & 0o777
    if mode & 0o077:
        PATH.chmod(0o600)
        print(f"FIXED    permissions {mode:o} -> 600 (was readable by others)")

    try:
        data = json.loads(PATH.read_text())
    except json.JSONDecodeError as e:
        print(f"BROKEN   not valid JSON: {e}")
        print("         Re-download it; a partial copy looks exactly like this.")
        return 1

    kind = next((k for k in ("web", "installed") if k in data), None)
    if kind is None:
        print(f"BROKEN   no 'web' or 'installed' key; top level is {list(data)}")
        return 1

    if kind == "installed":
        print("WRONG    this is a DESKTOP client ('installed').")
        print("         clipper.py --draft wants this one; the Connect YouTube")
        print("         button does not and will fail on it.")
        print("         Console -> Clients -> Create client -> Web application,")
        print("         then save that download as client_secrets.json instead.")
        return 1

    cfg = data["web"]
    cid = cfg.get("client_id", "")
    print(f"OK       Web application client  ({cid[:24]}...)")

    # The redirect URI the app will actually send. Importing here rather than at
    # module scope keeps the not-set-up path above dependency-free.
    from web import youtube
    want = youtube.redirect_uri()
    registered = cfg.get("redirect_uris", [])

    if want in registered:
        print(f"OK       redirect URI registered: {want}")
    else:
        print(f"MISSING  redirect URI not registered: {want}")
        print(f"         file lists: {registered or '(none)'}")
        print("         Add it under Authorised redirect URIs -- exact match,")
        print("         no trailing slash. This is redirect_uri_mismatch.")
        return 1

    print("\nReady. Start the app (python serve.py), sign in with your email,")
    print("then click Connect YouTube.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
