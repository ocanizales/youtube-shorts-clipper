"""
Durable state for the clipper web app (SQLite, WAL mode).

This replaces the in-memory JOBS dict so state survives restarts and is shared
across multiple web/worker processes. Tables: users, promo_codes, redemptions,
usage_events, newsletter, jobs.

Production migration path (documented, not premature): move the jobs queue to
Redis + RQ and the relational tables to Postgres. The function signatures here
are the seam for that swap.
"""

import json
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data.db"

# Plan -> monthly cap of processed videos ("dissections"). None = unlimited.
PLAN_CAPS = {"free": 3, "basic": 30}
WHITELIST_PLAN = "unlimited"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _month_start() -> str:
    n = datetime.now(timezone.utc)
    return n.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, token TEXT UNIQUE NOT NULL,
            plan TEXT NOT NULL DEFAULT 'free', whitelisted INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY, kind TEXT NOT NULL, plan TEXT,
            max_redemptions INTEGER NOT NULL DEFAULT 1, used_count INTEGER NOT NULL DEFAULT 0,
            expires_at TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS redemptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL, user_id TEXT NOT NULL,
            created_at TEXT NOT NULL, UNIQUE(code, user_id));
        CREATE TABLE IF NOT EXISTS usage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, job_id TEXT NOT NULL,
            created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS newsletter (
            email TEXT PRIMARY KEY, user_id TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, user_id TEXT, status TEXT NOT NULL DEFAULT 'queued',
            stage TEXT, progress INTEGER NOT NULL DEFAULT 0, clips TEXT NOT NULL DEFAULT '[]',
            error INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL, is_upload INTEGER NOT NULL DEFAULT 0,
            opts TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, thumb TEXT);
        CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_usage_user ON usage_events(user_id, created_at);
        -- One connected YouTube channel per user. `creds` is the JSON form of a
        -- google.oauth2.credentials.Credentials, refresh token included: that is
        -- what makes "connect once" work instead of re-consenting every upload.
        -- It is effectively a password to the channel's upload scope — see the
        -- warning in web/youtube.py about who can read this file.
        CREATE TABLE IF NOT EXISTS youtube_accounts (
            user_id TEXT PRIMARY KEY, channel_id TEXT, channel_title TEXT,
            creds TEXT NOT NULL, connected_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        """)
        # Idempotent migration for DBs created before the hero-thumbnail column.
        cols = {r["name"] for r in c.execute("PRAGMA table_info(jobs)")}
        if "thumb" not in cols:
            c.execute("ALTER TABLE jobs ADD COLUMN thumb TEXT")
        # Upload jobs ride the same queue as renders so they inherit the worker,
        # the progress plumbing and the polling UI. `kind` is what the worker
        # branches on; existing rows are renders.
        if "kind" not in cols:
            c.execute("ALTER TABLE jobs ADD COLUMN kind TEXT NOT NULL DEFAULT 'render'")
        if "result_url" not in cols:
            c.execute("ALTER TABLE jobs ADD COLUMN result_url TEXT")


# ── users ────────────────────────────────────────────────────────────────────
def get_or_create_user(email: str) -> sqlite3.Row:
    email = email.strip().lower()
    with connect() as c:
        row = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if row:
            return row
        uid, token = secrets.token_hex(8), secrets.token_urlsafe(24)
        c.execute("INSERT INTO users (id,email,token,created_at) VALUES (?,?,?,?)",
                  (uid, email, token, _now()))
        return c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def get_user_by_token(token: str):
    if not token:
        return None
    with connect() as c:
        return c.execute("SELECT * FROM users WHERE token=?", (token,)).fetchone()


# ── quota ────────────────────────────────────────────────────────────────────
def usage_this_month(user_id: str) -> int:
    with connect() as c:
        return c.execute(
            "SELECT COUNT(*) FROM usage_events WHERE user_id=? AND created_at>=?",
            (user_id, _month_start())).fetchone()[0]


def cap_for(user: sqlite3.Row):
    """Monthly cap for a user. None means unlimited (whitelisted)."""
    if user["whitelisted"]:
        return None
    return PLAN_CAPS.get(user["plan"], PLAN_CAPS["free"])


def remaining(user: sqlite3.Row):
    cap = cap_for(user)
    if cap is None:
        return None  # unlimited
    return max(0, cap - usage_this_month(user["id"]))


def record_usage(user_id: str, job_id: str) -> None:
    with connect() as c:
        c.execute("INSERT INTO usage_events (user_id,job_id,created_at) VALUES (?,?,?)",
                  (user_id, job_id, _now()))


def active_jobs_count(user_id: str) -> int:
    """Queued/running jobs not yet metered. Counted against the cap to stop bursts."""
    with connect() as c:
        return c.execute("SELECT COUNT(*) FROM jobs WHERE user_id=? AND "
                         "status IN ('queued','running')", (user_id,)).fetchone()[0]


def can_process(user: sqlite3.Row) -> tuple[bool, str]:
    cap = cap_for(user)
    if cap is None:
        return True, ""
    committed = usage_this_month(user["id"]) + active_jobs_count(user["id"])
    if committed >= cap:
        return False, f"Monthly limit reached ({cap} videos on the {user['plan']} plan)."
    return True, ""


# ── promo codes / whitelist ──────────────────────────────────────────────────
def create_promo(code: str, kind: str, plan=None, max_redemptions=1, expires_at=None):
    """kind: 'whitelist' (unlimited bypass) or 'plan' (grants `plan`)."""
    with connect() as c:
        c.execute("INSERT OR REPLACE INTO promo_codes "
                  "(code,kind,plan,max_redemptions,used_count,expires_at,created_at) "
                  "VALUES (?,?,?,?,0,?,?)",
                  (code.strip().upper(), kind, plan, max_redemptions, expires_at, _now()))


def redeem_promo(user_id: str, code: str) -> tuple[bool, str]:
    code = code.strip().upper()
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        p = c.execute("SELECT * FROM promo_codes WHERE code=?", (code,)).fetchone()
        if not p:
            return False, "Invalid code."
        if p["expires_at"] and p["expires_at"] < _now():
            return False, "Code expired."
        if p["used_count"] >= p["max_redemptions"]:
            return False, "Code fully redeemed."
        if c.execute("SELECT 1 FROM redemptions WHERE code=? AND user_id=?",
                     (code, user_id)).fetchone():
            return False, "Already redeemed by this account."
        c.execute("INSERT INTO redemptions (code,user_id,created_at) VALUES (?,?,?)",
                  (code, user_id, _now()))
        c.execute("UPDATE promo_codes SET used_count=used_count+1 WHERE code=?", (code,))
        if p["kind"] == "whitelist":
            c.execute("UPDATE users SET whitelisted=1, plan=? WHERE id=?",
                      (WHITELIST_PLAN, user_id))
            return True, "Whitelisted: unlimited usage unlocked."
        c.execute("UPDATE users SET plan=? WHERE id=?", (p["plan"], user_id))
        return True, f"Plan upgraded to {p['plan']}."


# ── newsletter ───────────────────────────────────────────────────────────────
def subscribe_newsletter(email: str, user_id=None) -> None:
    with connect() as c:
        c.execute("INSERT OR IGNORE INTO newsletter (email,user_id,created_at) VALUES (?,?,?)",
                  (email.strip().lower(), user_id, _now()))


# ── jobs (queue + status) ────────────────────────────────────────────────────
def create_job(job_id, user_id, source, is_upload, opts, kind="render") -> None:
    with connect() as c:
        c.execute("INSERT INTO jobs (id,user_id,source,is_upload,opts,kind,stage,"
                  "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                  (job_id, user_id, source, int(is_upload), json.dumps(opts),
                   kind, "Queued", _now(), _now()))


def get_job(job_id):
    with connect() as c:
        r = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["clips"], d["opts"] = json.loads(d["clips"]), json.loads(d["opts"])
        d["done"] = d["status"] in ("done", "error")
        return d


def update_job(job_id, **fields) -> None:
    if "clips" in fields:
        fields["clips"] = json.dumps(fields["clips"])
    fields["updated_at"] = _now()
    cols = ", ".join(f"{k}=?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))


def claim_next_job():
    """Atomically move the oldest queued job to 'running'. Returns the job dict or None."""
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        r = c.execute("SELECT id FROM jobs WHERE status='queued' "
                      "ORDER BY created_at LIMIT 1").fetchone()
        if not r:
            return None
        c.execute("UPDATE jobs SET status='running', updated_at=? WHERE id=?",
                  (_now(), r["id"]))
    return get_job(r["id"])


# ── connected YouTube channels ───────────────────────────────────────────────
def save_youtube_account(user_id, channel_id, channel_title, creds_json) -> None:
    """Insert or update the channel a user has connected.

    Keyed by user_id, so re-connecting REPLACES the previous channel rather than
    accumulating them. That is deliberate: "which channel does this upload go to"
    must have exactly one answer at all times.
    """
    with connect() as c:
        c.execute(
            "INSERT INTO youtube_accounts (user_id,channel_id,channel_title,creds,"
            "connected_at,updated_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET channel_id=excluded.channel_id, "
            "channel_title=excluded.channel_title, creds=excluded.creds, "
            "updated_at=excluded.updated_at",
            (user_id, channel_id, channel_title, creds_json, _now(), _now()))


def get_youtube_account(user_id):
    if not user_id:
        return None
    with connect() as c:
        r = c.execute("SELECT * FROM youtube_accounts WHERE user_id=?",
                      (user_id,)).fetchone()
        return dict(r) if r else None


def update_youtube_creds(user_id, creds_json) -> None:
    """Persist a refreshed access token. Called after google refreshes it, so the
    next upload does not have to refresh again."""
    with connect() as c:
        c.execute("UPDATE youtube_accounts SET creds=?, updated_at=? WHERE user_id=?",
                  (creds_json, _now(), user_id))


def delete_youtube_account(user_id) -> None:
    with connect() as c:
        c.execute("DELETE FROM youtube_accounts WHERE user_id=?", (user_id,))


if __name__ == "__main__":
    init_db()
    print(f"Initialized {DB_PATH}")
