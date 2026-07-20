# PROJECT_MAP — youtube-shorts-clipper
_Generated 2026-07-20 by scripts/build_memory.py — do not edit by hand._

## clipper.py
  - _LoL YouTube Shorts Clipper — turn a YouTube VOD into 9:16 highlight clips._
  - `ff(*args)`
  - `download_video(url, on_progress)` — Download the best video+audio up to 1080p (any container), merged.
  - `find_hype_moments(video, clip_len, top_n, peak_pos)`
  - `focus_x(video, start, dur, src_w, src_h, crop_w, spike_frac)` — Return the left x (px) of the crop window that captures the most action.
  - `zoom_geometry(dims)` — Band plan for the 'zoom' layout (the Korean solo-queue Short look):
  - `full_video_height(dims)` — Height (px) of the source frame when scaled to the full 1080 width.
  - `caption_anchor(layout, dims)` — Where captions belong for each layout, as (ASS alignment, margin px).
  - `build_vf(layout, dims, crop_x, facecam, ass_path, caption, cap_size, cap_an, cap_margin)`
  - `make_dynamic_captions(clip, an, margin_v, fontsize)` — Transcribe spoken words and write an .ass with word-by-word reveal where the
  - `detect_facecam(video, start, dur, src_w, src_h)` — Best-effort: find a streamer facecam box via face detection on sampled frames.
  - `has_existing_captions(video, start, dur, dims)` — True if the source already has captions, so we don't add a duplicate layer:
  - `write_metadata(clip, title_base, idx, platform, hook, meta)` — Write a sidecar .txt with a ready-to-paste title + caption for the
  - `make_thumbnail(video, start, dur, peak_pos, idx, clip_out, title, transcript)` — AI thumbnail for a freshly cut clip, taken from the CLEAN source video
  - `rethumb_all()` — Regenerate thumbnails for every rendered clip in clips/ — no re-render,
  - `cut_clip(video, start, dur, idx, layout, caption, subs, dims, cap_size, peak_pos, facecam_override, title, platform, ai_meta, thumbs)`
  - `make_clips(video)` — Full local pipeline on a downloaded video. Shared by CLI + web.
  - `show_channel()`
  - `upload_draft(clip, title, idx)`
  - `main()`

## scripts/build_memory.py
  - _Generate PROJECT_MAP.md — a deterministic, no-LLM, no-network code map._
  - `signature(fn)`
  - `first_line(doc)`
  - `describe(path)`
  - `main()`

## serve.py
  - _Local dev launcher: starts the job worker AND the web server together._

## web/app.py
  - _Web front-end for the LoL Shorts Clipper._
  - `current_user()`
  - `user_payload(u)`
  - `account()`
  - `me()`
  - `redeem()`
  - `newsletter()`
  - `process()`
  - `status(job_id)`
  - `clip(name)`
  - `index()`

## web/db.py
  - _Durable state for the clipper web app (SQLite, WAL mode)._
  - `connect()`
  - `init_db()`
  - `get_or_create_user(email)`
  - `get_user_by_token(token)`
  - `usage_this_month(user_id)`
  - `cap_for(user)` — Monthly cap for a user. None means unlimited (whitelisted).
  - `remaining(user)`
  - `record_usage(user_id, job_id)`
  - `active_jobs_count(user_id)` — Queued/running jobs not yet metered. Counted against the cap to stop bursts.
  - `can_process(user)`
  - `create_promo(code, kind, plan, max_redemptions, expires_at)` — kind: 'whitelist' (unlimited bypass) or 'plan' (grants `plan`).
  - `redeem_promo(user_id, code)`
  - `subscribe_newsletter(email, user_id)`
  - `create_job(job_id, user_id, source, is_upload, opts)`
  - `get_job(job_id)`
  - `update_job(job_id, **fields)`
  - `claim_next_job()` — Atomically move the oldest queued job to 'running'. Returns the job dict or None.

## web/worker.py
  - _Job worker for the clipper web app._
  - `process(job)`
  - `main()`

## Dependencies
  - requirements.txt: yt-dlp                      # download YouTube videos, yt-dlp-ejs                  # JS challenge solver — without it YouTube hides formats (needs node/deno), librosa                    # audio energy analysis for highlight detection, numpy                      # RMS / peak math, google-api-python-client, google-auth-httplib2, google-auth-oauthlib
