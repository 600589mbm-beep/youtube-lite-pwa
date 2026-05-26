# Crypto Shorts creative format

The faceless, AI-driven crypto Shorts brand format produced by
`pipeline/daily_pipeline_voicebox.py`. Shorts mode only (long-form lives in the
separate ViMax lane — see `docs/VIMAX_LONGFORM.md`).

## Narration (script prompt)
`generate_spoken_script()` instructs the LLM to:
- **3-second hook**: open on tension or a bold claim — never a generic intro.
- **Aggressive pacing**: short declarative sentences, a new beat every ~1.5–2s.
- **Trigger words** where true: PUMP, DUMP, WHALES, CRASH, ETF, LIQUIDATION, REKT, 100X, MILLION.
- **Seamless loop**: the last line flows back into the first; no sign-off.
- **No financial advice**: never buy/sell/hold or price-target recommendations — analytical and exciting only.
- ~95–115 words (~45s).

## Content pillars (rotated per run, seeded by run id)
`choose_pillar(seed)` → one of:
- **alpha** — alpha / watchlist analysis: what's moving and why (no advice).
- **predictive** — predictive markets & hard data: Polymarket-style probabilities and market-implied odds (no fabricated exact numbers).
- **drama** — crypto drama / rekt: hacks, exploits, liquidations, trader wins/losses, mini-doc arc.

Reported in `summary.json` as `contentPillar` and logged to stderr.

## Captions (ASS, keyword highlighting)
`write_ass()` burns big bold high-contrast captions (translucent box, centered).
Trigger keywords are recolored by sentiment via inline ASS `\c` tags:
- **green** (`&H78FF28&`) bullish: PUMP, 100X, MOON, RALLY, ETF, ATH, SURGE…
- **red** (`&H463CFF&`) bearish: DUMP, CRASH, REKT, LIQUIDATION, HACK, RUG…
- **yellow** (`&H28DCFF&`) attention: WHALES, MILLION, BILLION, ALERT, MASSIVE…

Rendered with ffmpeg's `ass=` filter. Captions stay synced to the Whisper
timings; the overlays below never shift timestamps or audio.

## Visuals (data as B-roll, aggressive pacing)
`visual_background.py` generates one of 5 styles per run (`choose_style(seed)`):
neon_candles, market_heatmap, coin_vortex, ticker_wall, liquid_gold — all
headless, copyright-safe, 1080×1920, never black.

Layered on top, every `_BEAT_INTERVAL` (1.7s):
- a **fresh data widget** (mini bar chart, exec/code rows, stat card like
  "LIQUIDATIONS +$4.2M", or a mini heatmap), placed in the top band / lower
  corners so it never collides with the center captions;
- a brief **brightness "punch"** at the beat onset — a felt motion beat.

## Overlays in the final render (`render_final_short`)
1. **Intro card** (`make_intro_card`): full-screen crypto-news card overlaid
   ~t=1.0–1.8s → a strong frame for YouTube's auto-thumbnail picker.
2. **CTA banner** (`make_cta_card`): "▶ SUBSCRIBE — DAILY CRYPTO ALPHA" shown for
   the last ~2.6s, right before the loop restarts.
3. **Thumbnail** (`make_thumbnail`): bold 1280×720 card (badge + accent bar +
   headline over a style backdrop), saved as the upload JPG.

## Posting cadence
- `SHORTS_PER_DAY` / `POSTING_FREQUENCY_DAILY` env → `config.shorts_per_day`
  (default 1). **Read but not auto-acted-on**: the cron still fires once/day.
- Current cron (unchanged): `0 6 * * *` → `scripts/run-daily-pipeline.sh`,
  auto-stops after `DISABLE_AFTER=2026-06-01`.
- **To safely run 2/day**: add a *second* guarded cron entry at a different hour,
  e.g. `0 18 * * *`, pointing at the same wrapper. The wrapper's `flock` guard
  prevents overlapping renders and the queue's duplicate behavior is unchanged.
  Do NOT loop the pipeline twice inside one wrapper run (risks a duplicate before
  the first upload confirms). Cadence was left at 1/day in this change.

## Growth mechanics summary
3-second hook · loop retention · keyword-highlighted captions · data B-roll beats
· end CTA before the loop · faceless AI brand. No financial advice anywhere.
