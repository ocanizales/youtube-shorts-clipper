# Selling the LoL Shorts Clipper

You now have two products in one repo:
1. **CLI tool** (`clipper.py`) — power users, your own channel.
2. **Web app** (`web/app.py`) — drag-drop / URL-paste, the thing you can sell.

This doc is the plan for turning the web app into a paid product.

---

## 1. What you're selling

"Paste a YouTube VOD → get ready-to-post vertical Shorts in minutes."
Target buyers, in order of willingness to pay:

| Audience | Pain | Pitch |
|----------|------|-------|
| LoL / FPS streamers | No time to edit clips | Auto-clip your VODs while you sleep |
| Faceless Shorts channels | Need volume of content | Batch 10 clips per video, auto-captioned |
| Small editing agencies | Manual scrubbing is slow | First-pass highlight detection |

Start with **one game (LoL)** and one audience (streamers). Niche sells.

---

## 2. Pricing (credit-based SaaS)

A "credit" = one processed video. Costs are dominated by compute, not API.

| Tier | Price | Credits/mo | Notes |
|------|-------|-----------|-------|
| Free | $0 | 3 videos, watermark | acquisition + virality |
| Creator | $12/mo | 50 videos | no watermark, all layouts |
| Pro | $29/mo | 200 videos | subtitles, priority queue |
| Agency | $79/mo | unlimited\* | multi-channel, API access |

Add a watermark to free-tier output (a `drawtext` overlay) — it doubles as
marketing on every shared clip.

---

## 3. From local script to hosted SaaS

The current `web/app.py` runs the work **synchronously in a thread** — fine for
you, not for many users. Production path:

```
Browser ──> API (Flask/FastAPI) ──> Job queue (Redis + RQ/Celery)
                                          │
                                   GPU/CPU workers (ffmpeg)
                                          │
                                   Object storage (S3 / R2) ──> signed download URLs
```

Concrete upgrades, cheapest first:
1. **Add accounts + credits** — Supabase or Clerk for auth; a `credits` column.
2. **Add a queue** — Redis + RQ. Move `run_job` into a worker process so the web
   box stays responsive and you can scale workers independently.
3. **Move storage off the box** — upload clips to Cloudflare R2 (no egress fees);
   serve via signed URLs instead of `/clips/<name>`.
4. **Containerize** — one Dockerfile with ffmpeg baked in; deploy workers on
   Fly.io / Railway / a cheap GPU host (RunPod) for faster encodes.
5. **Payments** — Stripe Checkout + a webhook that tops up credits.

> The pipeline code (`make_clips`) does not change — only how it's invoked and
> where output lands. That separation is already in place.

---

## 4. Cost & margin reality check

Per video (~1 hr VOD, 5 clips), rough order of magnitude:
- yt-dlp download: bandwidth only (~1–2 GB in).
- ffmpeg encode: the real cost — ~1–3 min CPU per clip. A $5–10/mo CPU box
  handles dozens of videos/day; batch overnight.
- Storage/egress: pennies on R2.

At $12/mo for 50 videos you're selling ~$0.24/video against a few cents of
compute — healthy margin **as long as you cap free usage and queue jobs**.

---

## 5. Legal / ToS (read before charging money)

- **YouTube ToS**: downloading videos via yt-dlp is against YouTube's ToS.
  Safer positioning: users upload **their own** VODs (drag-drop path you already
  built), or you integrate the official YouTube API for content the user owns.
  Leading with "upload your VOD" avoids the riskiest part.
- **Copyright**: clips of someone else's gameplay/commentary are their content.
  Sell the *tool*, make users responsible for what they upload (standard ToS).
- **Riot Games**: LoL footage is Riot's IP but Riot's Legal Jibber Jabber policy
  broadly permits non-commercial fan content; a paid editing tool is a grey area
  — keep branding generic ("Shorts Clipper"), not "League" in the product name.

---

## 6. Go-to-market (first 30 days)

1. Post 5–10 of your own auto-clipped Shorts. If they perform, that's the demo.
2. Put a 30-second screen recording of the drag-drop flow on TikTok/X.
3. Soft launch in r/LeagueOfLegends content-creator threads + a couple of
   editing Discords. Offer free Pro to the first 20 who give feedback.
4. Only build billing once people are using the free tier daily.

Build distribution before you build features. The tool already works.
