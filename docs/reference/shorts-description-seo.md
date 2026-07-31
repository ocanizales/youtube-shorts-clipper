# YouTube Shorts description optimization

Supplied by the user 2026-07-31 as the standing rules for description output.
Implemented in [`clipper.py`](../../clipper.py) — `build_description`,
`shorts_hashtags`, `_front_line` — and guarded by `tests/test_description.py`.

## The rules

Place **3–5 relevant hashtags (including `#Shorts`) and the primary keyword
within the first 100–125 characters** to maximize preview visibility. Use the
remaining space (up to 5,000 characters) for detailed context, secondary
keywords, and calls-to-action that the search algorithm uses to categorize.

**1. Front-load keywords.** The first sentence must contain the main topic and
value proposition — it is the only text most viewers see before clicking "more."

**2. Hashtag strategy.** Include `#Shorts` for proper feed categorization, one
broad category hashtag (e.g. `#Gaming`), and 2–3 niche-specific hashtags (e.g.
`#Faker`, `#T1`) to target the right audience without looking spammy.

**3. Description structure.** Keep the initial hook concise; add links or CTAs in
the middle section (characters 126–500); use the rest for SEO-rich text, credits,
or disclaimers.

**4. Avoid title clutter.** Put hashtags in the description rather than the
title, preserving title characters for a compelling, keyword-rich headline that
drives clicks.

## How this repo implements them

```
line 1  (<= 125 chars)  <primary keyword hook> #Shorts #<niche> #<niche> #Gaming
                        ^ the only part a viewer sees unexpanded

blank
CTA / link              endcard text, or "Full match link in the pinned comment."
blank
SEO body                the AI's 2-3 sentences: who, what, which team/event
blank
credits / disclaimer    "Clipped from the full VOD. All game footage belongs to Riot Games."
```

| Constant | Value | Why |
|---|---|---|
| `SHORTS_PREVIEW_CHARS` | 125 | Hard budget for line 1. |
| `SHORTS_DESC_MAX` | 5000 | YouTube's ceiling; the output is truncated to it. |
| `SHORTS_TAGS_MIN/MAX` | 3 / 5 | The 3–5 band. |

Decisions worth keeping:

* **The hook gets trimmed, never the tags.** If the hook will not fit beside the
  hashtags, it is cut at a word boundary. Tags are what categorize the video;
  the hook only sells it. `test_hashtags_survive_when_the_hook_is_too_long`.
* **Niche tags come from the transcript**, via `lol_kb.niche_hashtags` — a Faker
  clip earns `#Faker #T1`, not a fourth generic `#Gaming`. Falls back to
  `#LeagueOfLegends #LoL` when the commentary names nobody.
* **`#Shorts` is first, the broad category tag is last.** `#Shorts` is what puts
  the video in the Shorts feed, so it never risks being the one that gets cut.
* **Clip index is `[3]`, not `(#3)`.** YouTube parses `#` in a title as a
  hashtag — the old suffix burned title characters and filed clips under a junk
  `#3` feed. Directly rule 4.
* **The model is told not to invent.** A 3B model given "Worlds" will happily add
  "the 2023 finals". The prompt forbids inventing years, scores, stages, and
  results, because a fabricated detail in a published description is worse than
  a vague one. (Observed and fixed during implementation.)
