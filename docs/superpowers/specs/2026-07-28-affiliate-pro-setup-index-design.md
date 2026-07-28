# Affiliate strategy: the Pro Setup Index

_Design doc — 2026-07-28. Status: awaiting user review._

## Goal

Give the LoL Shorts channel a revenue path that works **now**, at its current size, and
that **survives the loss of the channel**.

## Why this shape — the constraints that force it

These are the findings the design has to obey. They were verified during research on
2026-07-28, not assumed.

1. **Shorts has no clickable surface during playback.** No end cards, no overlays. The
   only things a Shorts viewer can act on are the **comments**, the description, and the
   channel page. Any CTA spoken or shown mid-clip fires when nothing is tappable.
2. **AdSense is effectively out of reach short-term.** The Shorts route to YPP is 1,000
   subs **+ 10 million valid public Shorts views in 90 days** (the long-form route is
   1,000 subs + 4,000 watch hours / 12 months). Shorts RPM is roughly an order of
   magnitude below long-form. Affiliate is not an accelerant here; near-term it is the
   only realistic revenue.
3. **The channel is deletable by a third party.** Riot Korea has previously issued
   **copyright strikes** — not merely Content ID claims — against LCK highlight channels,
   with established creators nearly losing their libraries; survivors negotiated
   authorized-distributor status. LCK's stated position is that broadcast content is Riot
   IP and that secondary editing is prohibited. Three strikes terminates a channel. No
   public application path for authorized distribution was found; those appear to be
   case-by-case through regional contacts.
   → **Therefore the revenue asset must not live inside YouTube.**
4. **YouTube's reused-content policy permits transformative clip channels** — curated
   clips with original insight or editing qualify; value-free compilations do not. A
   caption layer that makes an actual claim serves monetization eligibility *and* the
   affiliate funnel with the same work.
5. **The YouTube Data API cannot pin comments.** `comments.setModerationStatus` supports
   only `heldForReview` / `published` / `rejected`. Pinning is UI-only. The design assumes
   a manual pin step rather than promising automation that does not exist.
6. **The audience skews young and low-spend**, and the tempting adjacent offers (elo
   boosting, account/skin shops, esports betting) are Riot ToS violations, wrong for the
   audience, or both. They are excluded by policy, not oversight.

## Architecture

Three components. The clip creates curiosity; the comment is the door; the page converts
and is the asset that persists.

```
Short (pro clip)  ──►  end card: "<Pro>'s setup → pinned comment"
                            │
                            ▼
                  pinned comment (manual pin, auto-posted body)
                            │
                            ▼
              Pro Setup Index page  ──►  affiliate programs
              (owned domain, SEO-indexed, outlives the channel)
```

### Component 1 — Pro Setup Index (the owned asset)

A public site with **one page per pro player**: peripherals (mouse, keyboard, headset,
monitor, mousepad, chair) and in-game config (sensitivity, resolution, keybinds), every
product carrying an affiliate link.

Why a page rather than direct affiliate links in comments:
- **One link per clip regardless of product count** — no per-video link management.
- **It ranks on its own.** "<pro> settings" is durable evergreen search demand; the page
  earns after the Short is dead, and *after the channel is gone*.
- **Programs can be swapped** without re-editing a single published clip.

**Reuses existing infrastructure.** `seo-ops` is already folder-per-site
(`sites/<domain>/` with `site.yaml`, `backlog.yaml`, `metrics.csv`, `changelog.csv`,
`dashboard.json`, `report.*`), so this drops in as a second site folder and inherits the
nightly measurement, the report, and the `/seo/<slug>` dashboard page. Publication reuses
the established cPanel/HostGator + GitHub Actions FTPS path.

**Constraint carried over:** never `#000` backgrounds — `#252525` per the standing rule.

### Component 2 — Clip-side end card

A burned-in visual card over the **final ~1.5s** of the clip: `"<Pro>'s settings → pinned
comment"`.

- **Drawn over the existing final frames — it adds no runtime.** This is the deliberate
  rejection of the 5-second AI-voice outro considered earlier: on a 30–45s Short that
  outro is 11–17% of runtime spent on a CTA at the exact moment nothing is clickable,
  taxing reach across the entire catalogue to reach the fraction who would act.
- Silent. The caster audio is the clip's voice; no synthetic narration is introduced.
- Implemented in the existing ffmpeg filter graph alongside the caption compositing.

### Component 3 — Comment workflow (semi-automated)

On upload, post the comment body automatically; pin it by hand.

- The clipper **already holds Google OAuth** (`clipper.py:1301`, scopes
  `youtube.upload` + `youtube.readonly`). Posting comments needs
  `youtube.force-ssl` added — a scope change and re-consent, not new infrastructure.
- Comment body: the specific settings hook plus the page link, e.g.
  *"<Pro> plays 45 sens on a <mouse> — full setup: <url>"*. Specific beats generic, which
  is the one tactic both researched videos independently confirmed.
- **Manual step:** pinning, a few seconds per upload in Studio (see constraint 5).
- **FTC disclosure is mandatory** — affiliate relationships must be disclosed clearly.
  Disclosure goes in the comment body and on every page, not buried in a footer.

## Affiliate program selection

Verified 2026-07-28. Rates change; re-verify before signing.

| Program | Rate | Notes |
|---|---|---|
| **ExitLag** | 20%, up to **30%** for creators | Best fit. Recurring subs; ping tools are genuinely relevant to LoL players. $50 min payout, 30-day maturation |
| **Razer** | up to 15% | Free to join |
| **Secretlab** | up to 12% | Only a 7-day conversion window |
| **Logitech G** | 4–10% | 30-day cookie |
| **SteelSeries** | 4–8% | 30-day cookie |
| Amazon Associates | **2.5%** PC components / 2% games | Fallback only — for products with no direct program |

**Excluded by policy:** elo boosting, account/skin shops, esports betting.
**Unverified — do not use pending manual check:** RankedKings (advertises 25%, but their
affiliate page 404s and research could not establish whether they sell coaching or
boosting; boosting would violate Riot's ToS).

**Sequencing:** ExitLag first — it is the only researched option that is both
high-percentage and recurring, and it needs no physical-product logistics. Peripherals
follow once pages exist to host them.

## Attribution

Each pro page gets a **distinct affiliate sub-ID** so revenue is traceable to the specific
page, and therefore to the pro. That closes the loop: the pros who actually earn become
the pros to clip and to build pages for next. Without sub-IDs the whole thing is
unmeasurable and page-building stays guesswork.

## Out of scope

- AdSense/YPP optimization — blocked on the 10M-view threshold; not this project.
- Selling the clipper SaaS itself (`BUSINESS.md`). Its buyers are creators; this channel's
  audience is players. Different funnel, deliberately not merged into this one.
- The HPC hook/cold-open clipper work — separate branch off `framing-overhaul`, own spec.
- Negotiating authorized-VOD-distributor status with Riot.

## Success criteria

1. First affiliate commission attributable to a specific pro page.
2. At least one pro page receiving organic search traffic — proof the asset works
   independently of the channel, which is the entire risk rationale.
3. No measurable retention regression on clips carrying the end card versus those without.

## Open questions for the user

1. **Domain** for the Pro Setup Index — new registration, or an addon domain on the
   existing HostGator account (reuses the deploy pipeline directly)?
2. **Which pros to launch with?** Absent channel analytics this is guesswork. Granting
   YouTube Analytics access (`yt-analytics.readonly`, another scope on existing OAuth)
   would replace it with a ranked list of which players actually pull views.
3. **Footage mix** — does the Riot strike history change the pro-VOD weighting, or hold
   course and accept the risk?
