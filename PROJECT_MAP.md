# PROJECT_MAP — youtube-shorts-clipper
_Generated 2026-07-29 by scripts/build_memory.py — do not edit by hand._

## clipper.py
  - _LoL YouTube Shorts Clipper — turn a YouTube VOD into 9:16 highlight clips._
  - `ff(*args)`
  - `download_video(url, on_progress)` — Download the best video+audio up to 1080p (any container), merged.
  - `find_hype_moments(video, clip_len, top_n, peak_pos)`
  - `track_path(video, start, dur, src_w, src_h, crop_w)` — Eased crop-x trajectory that follows the action (replaces focus_x).
  - `zoom_geometry(dims)` — Band plan for the 'zoom' layout (the Korean solo-queue Short look):
  - `full_video_height(dims)` — Height (px) of the source frame when scaled to the full 1080 width.
  - `caption_anchor(layout, dims)` — Where captions belong for each layout, as (ASS alignment, margin px).
  - `build_vf(layout, dims, crop_x, facecam, ass_path, caption, cap_size, cap_an, cap_margin, sendcmd, crop_w, suffix, endcard, endcard_from)` — Build the 9:16 reframing filter chain for one segment.
  - `make_dynamic_captions(clip, an, margin_v, fontsize)` — Transcribe spoken words and write an .ass with word-by-word reveal where the
  - `detect_facecam(video, start, dur, src_w, src_h)` — Best-effort: find a streamer facecam box via face detection on sampled frames.
  - `has_existing_captions(video, start, dur, dims)` — True if the source already has captions, so we don't add a duplicate layer:
  - `write_metadata(clip, title_base, idx, platform, hook, meta)` — Write a sidecar .txt with a ready-to-paste title + caption for the
  - `make_thumbnail(video, start, dur, peak_pos, idx, clip_out, title, transcript)` — AI thumbnail for a freshly cut clip, taken from the CLEAN source video
  - `make_hero_thumbnail(video, moments, dims, title, transcript, out_path)` — ONE hero thumbnail for the whole source, composed like a per-clip thumb.
  - `rethumb_all()` — Regenerate thumbnails for every rendered clip in clips/ — no re-render,
  - `cut_clip(video, start, dur, idx, layout, caption, subs, dims, cap_size, peak_pos, facecam_override, title, platform, ai_meta, thumbs, teaser, endcard)`
  - `make_clips(video)` — Full local pipeline on a downloaded video. Shared by CLI + web.
  - `make_sample(video, at)` — Render a labeled framing comparison set to clips/samples/ so the user can
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

## tests/test_captions.py
  - _Caption placement. Run: .venv/bin/python tests/test_captions.py_
  - `test_crop_captions_bottom_lower_third()`
  - `test_zoom_captions_above_hud_not_top_bar()`

## tests/test_endcard.py
  - _End-to-end: the affiliate end card appears only over the final seconds._
  - `test_endcard_only_covers_the_final_seconds()`
  - `test_endcard_lands_at_the_end_of_the_finished_short()` — With a teaser prepended the card must still close the OUTPUT, not fire
  - `test_endcard_is_absent_from_the_teaser_branch()` — The flash must never carry the CTA.
  - `test_endcard_scrim_is_not_pure_black()`

## tests/test_hook.py
  - _HPC hook-overhaul unit tests. Run: .venv/bin/python tests/test_hook.py_
  - `test_build_vf_empty_suffix_is_byte_identical()`
  - `test_build_vf_suffix_renames_every_internal_label()` — A suffixed graph must share NO link label with the unsuffixed one —
  - `test_build_vf_suffix_renames_the_crop_instance()` — sendcmd addresses `crop@dyn` BY NAME, so the teaser's crop must not
  - `test_refine_start_moves_to_the_liveliest_second()`
  - `test_refine_start_is_a_noop_on_flat_audio()`
  - `test_refine_start_keeps_the_spike_inside_its_band()` — The loud second before the fight must not be allowed to drag the start so
  - `test_refine_start_never_runs_past_the_video()`
  - `test_teaser_window_ends_just_after_the_spike()`
  - `test_teaser_window_clamps_at_the_video_start()`
  - `test_teaser_window_never_overruns_the_clip()`
  - `test_teaser_is_short_enough_not_to_resolve()` — A guard on the tuning itself, not the code: a cold open that runs long
  - `test_detail_scales_carry_explicit_scaler_flags()` — Regression guard for a silent quality bug.
  - `test_blurred_backdrops_do_not_pay_for_lanczos()` — The `full`/`fit` backdrops are boxblurred immediately after scaling, so a

## tests/test_sample.py
  - _Sample harness renders a labeled comparison set._
  - `test_make_sample_writes_comparison_set()`

## tests/test_teaser_render.py
  - _End-to-end: the cold-open teaser really is prepended, and really is the climax._
  - `test_teaser_prepends_the_climax()`
  - `test_teaser_graph_is_valid_for_every_layout()` — The teaser doubles the reframing graph inside one -filter_complex. The

## tests/test_tracking.py
  - _Standalone tracking-logic tests. Run: .venv/bin/python tests/test_tracking.py_
  - `test_column_motion_masks_minimap_corner()`
  - `test_column_motion_masks_hud_strip()`
  - `test_aim_targets_follows_moving_blob()`
  - `test_aim_targets_center_bias_prefers_central_action()`
  - `test_ease_deadzone_holds()`
  - `test_ease_velocity_capped()`
  - `test_ease_clamps_out_of_range_targets()`
  - `test_write_sendcmd_format(tmp_path)`
  - `test_track_path_static_fallback_when_no_motion(monkeypatch)`
  - `test_track_path_moving_writes_script()`

## tests/test_tracking_render.py
  - _End-to-end: sendcmd-driven crop follows a moving subject._
  - `test_tracked_crop_follows_moving_bar()`

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
