"""
Job worker for the clipper web app.

Polls the SQLite jobs queue, runs the ffmpeg pipeline, and writes status back to
the DB. Runs as a SEPARATE process from the web server so CPU-bound encoding does
not block request handling and survives web restarts. Start with:  python web/worker.py

Scale by running more worker processes. claim_next_job() is atomic, so workers do
not double-claim. (Production: swap this poll loop for Redis + RQ.)
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import clipper          # noqa: E402
import web.db as db     # noqa: E402  (run from project root: python web/worker.py)

POLL_SECONDS = 2


def process(job: dict) -> None:
    jid, opts = job["id"], job["opts"]
    video = None
    try:
        if job["is_upload"]:
            video = Path(job["source"])
        else:
            def dl(pct):  # download is the first half of the progress bar
                db.update_job(jid, stage=f"Downloading video {pct:.0f}%",
                              progress=int(pct * 0.5))
            video = clipper.download_video(job["source"], on_progress=dl)

        db.update_job(jid, stage="Finding highlights", progress=50)

        def cut(done, total):  # cutting is the second half
            db.update_job(jid, stage=f"Cutting clip {done}/{total}",
                          progress=50 + int(done / total * 50))

        clips, hero = clipper.make_clips(
            video, max_clips=opts["max_clips"], clip_len=opts["clip_len"],
            peak_pos=opts["peak_pos"], layout=opts["layout"],
            caption=opts.get("caption") or None, subs=opts.get("subtitles", False),
            cap_size=opts.get("cap_size", 66), title=opts.get("title", "Highlight"),
            platform=opts.get("platform", "youtube"), progress=cut,
            teaser=opts.get("teaser", True))

        db.update_job(jid, status="done", stage="Done", progress=100,
                      clips=[c.name for c in clips],
                      thumb=hero.name if hero else None)
        if job["user_id"]:  # meter usage only on success
            db.record_usage(job["user_id"], jid)
    except Exception as e:
        db.update_job(jid, status="error", error=1, stage=f"Error: {e}")
    finally:
        # The full source video is transient: delete it so downloads/ and uploads/
        # do not accumulate multi-GB files. The clips in clips/ are the product.
        try:
            if video and Path(video).exists():
                Path(video).unlink()
                print(f"[worker] removed source {Path(video).name}")
        except OSError:
            pass


def main() -> None:
    db.init_db()
    print("[worker] started; polling for jobs...")
    while True:
        job = db.claim_next_job()
        if job:
            print(f"[worker] processing {job['id']}")
            process(job)
        else:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
