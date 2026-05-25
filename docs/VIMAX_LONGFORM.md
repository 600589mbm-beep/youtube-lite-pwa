# ViMax long-form video engine — design & integration plan

Goal: add a second, story-driven **long-form** video mode (for the kids-show
channel and other narrative content) powered by a ViMax-style pipeline, **without
touching the working Shorts pipeline or its cron/upload path**.

> Status: **design + inert scaffold.** Nothing here is wired into the daily cron
> job. The Shorts engine (`daily_pipeline_voicebox.py` via `daily_pipeline_safe.py`)
> is unchanged. ViMax is opt-in behind a feature flag.

## Decision: separate optional adapter — do NOT integrate now

Recommendation: **wrap ViMax behind a separate adapter module + its own
entrypoint**, not bolt it into the existing pipeline. Reasons:

- The Shorts pipeline is live, scheduled, and just stabilized (black-bg fix, env
  fix). Mixing a heavy, experimental long-form engine into the same code path
  risks the one thing that must keep working: the daily upload.
- Long-form has different shape: minutes not seconds, multi-scene storyboard,
  asset/character consistency, no auto-captions-over-stock-loop. Forcing it
  through `retryable_publish_pipeline()` would bloat that function.
- ViMax's exact runtime/deps are external and still to be pinned. An adapter
  with a single isolated invocation point lets us swap/upgrade ViMax (or fall
  back) without code surgery.

The **only** thing the two modes share is the **publish seam** (see below) — and
that is already factored as an HTTP call, so we reuse it as-is.

## The integration seam (already exists)

Today the Shorts pipeline ends by POSTing JSON to the Node app and polling:

```
POST http://127.0.0.1:3456/api/youtube/publish
  { videoPath, thumbnailPath, title, description, tags,
    privacyStatus, categoryId, publishAt }
GET  http://127.0.0.1:3456/api/youtube/queue   # poll until status == "published"
```

`uploadJob()` in `youtube-publishing.js` is **channel-agnostic** — it uploads
with whatever single OAuth token is stored. So **any engine that produces an MP4
+ metadata can hand off through the exact same queue**, inheriting the proven,
guarded upload (non-fatal thumbnail, no auto-retry of failed jobs, scheduled
publishAt). ViMax long-form plugs in here unchanged.

## Stage mapping: current pipeline → ViMax concepts

| ViMax concept | Shorts pipeline today | Long-form (ViMax) adapter |
|---|---|---|
| **Idea / topic** | `pick_topic()` (CoinGecko / RSS) | Episode brief: theme, characters, age band, target length, learning goal |
| **Script** | `generate_spoken_script()` (95–115 words, one block) | Multi-beat narrative script: acts/scenes, per-character dialogue |
| **Storyboard** | *(none — single stock loop)* | ViMax storyboard: shot list, framing, transitions |
| **Scene planning** | *(none)* | ViMax scene plan: per-scene setting, characters present, camera, duration |
| **Visual asset generation** | `build_background_clip()` → one looped clip | ViMax per-scene asset gen: backgrounds, characters, props (T2I/T2V) |
| **Consistency checks** | *(none)* | ViMax character/style consistency across scenes (ref images / seeds / embeddings) |
| **Voiceover** | Voicebox `synthesize_voiceover()` (one VO) | Per-character / per-scene VO (Voicebox can stay the TTS backend) |
| **Captions** | Whisper → SRT burned in | Optional per-scene captions; usually off for kids narrative |
| **Final assembly** | one ffmpeg call (bg+audio+subs) | ViMax timeline assembly → single MP4 (ffmpeg/moviepy concat of scenes) |
| **Packaging** | `generate_packaging()` (title/desc/tags/thumb) | Reuse `generate_packaging()` or a kids-tuned variant |
| **Publish** | POST → Node queue → poll | **Same seam, unchanged** |

Note Voicebox/OpenRouter/FFmpeg are **reusable** under long-form — ViMax owns
storyboard→scenes→assets→consistency→assembly; the TTS and final-encode steps can
delegate back to the tools we already run.

## Proposed module layout (scaffolded, inert)

```
pipeline/
  video_modes.py        # mode registry + RenderedVideo contract + lazy engine resolve
  vimax_adapter.py      # ViMax-backed long-form engine; flag-gated, inert until configured
  longform_pipeline.py  # SEPARATE entrypoint: brief -> adapter -> shared publisher
                        #   (dry-run/build-only by default; never the Shorts cron)
docs/VIMAX_LONGFORM.md  # this file
```

`video_modes.RenderedVideo` is the contract every engine returns:
`video_path, thumbnail_path, title, description, tags, duration_seconds,
privacy_status, category_id, made_for_kids`.

Flow once enabled:

```
episode brief ─► vimax_adapter (ViMax: script→storyboard→scenes→assets→consistency→assembly)
              ─► RenderedVideo (mp4 + metadata)
              ─► longform_pipeline publisher (the SAME /api/youtube/publish seam)
```

## Feature flags (all default to OFF / Shorts)

| Env var | Default | Effect |
|---|---|---|
| `VIDEO_MODE` | `shorts` | `longform`/`kids_show` selects the ViMax adapter (only honored by `longform_pipeline.py`, never by the Shorts entrypoint). |
| `VIMAX_ENABLED` | `0` | Must be `1` for the adapter to attempt real ViMax work; otherwise it stays inert and explains what's missing. |
| `VIMAX_CMD` / `VIMAX_HOME` | unset | How to invoke the ViMax runtime (CLI path or service). Isolated to one function (`_invoke_vimax`). |
| `LONGFORM_PUBLISH` | `0` | `longform_pipeline.py` builds only; `1` is required to hand off to the upload queue. Extra guard so long-form can never upload by accident. |

## Blockers / open items before long-form can publish

1. **ViMax runtime not pinned.** The adapter's `_invoke_vimax()` is the single
   integration point; its exact CLI/service contract must be verified against the
   ViMax release we choose. Until `VIMAX_ENABLED=1` + `VIMAX_CMD` are set it is inert.
2. **Second channel = second OAuth.** The kids channel is a different YouTube
   channel; the Node app currently holds one `youtube.upload` token. Long-form
   uploads to the kids channel need a separate token bundle (and the app must be
   able to select it). Until then, long-form should build-only or publish
   unlisted to the existing channel for review.
3. **"Made for Kids" compliance.** Kids content must set
   `status.selfDeclaredMadeForKids=true` on upload (COPPA). `uploadJob()` does not
   send this yet — add it (driven by `RenderedVideo.made_for_kids`) before any
   kids upload.
4. **Cost/time + assets.** Long-form asset generation is heavier than a 45s
   short; budget render time (its own cron, generous timeout) and decide on the
   asset model/source. Keep it off the Shorts schedule.

## What stays exactly as-is

- `daily_pipeline_safe.py` / `daily_pipeline_voicebox.py` (Shorts engine).
- `scripts/run-daily-pipeline.sh` + cron `0 6 * * *` (Shorts only).
- The Node upload queue and its guards.
- Analytics plan (`docs/ANALYTICS.md`) — feeds both modes later.
