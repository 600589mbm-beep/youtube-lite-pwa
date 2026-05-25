# Analytics-driven topic/title optimization (plan)

Goal: let future runs of the daily Shorts pipeline pick topics, hooks, titles,
and publish times that real YouTube performance data says work — a closed loop
from `daily_pipeline_voicebox.py` packaging → upload → analytics → next run.

> Status: **planning + scaffold only.** Not wired into the daily cron job. Do
> not enable until the OAuth scopes below are granted (see "Blocking: scopes").

## Blocking: OAuth scopes

The current credential (`data/youtube-auth.json`) holds **only**:

```
https://www.googleapis.com/auth/youtube.upload
```

That scope **cannot read** video lists or analytics. To pull performance data we
must re-auth and add (read-only, no extra write power):

| Scope | Purpose |
|-------|---------|
| `https://www.googleapis.com/auth/youtube.readonly` | List the channel's videos, map our uploads to videoIds (also recovers the orphan), read titles/publish times. |
| `https://www.googleapis.com/auth/yt-analytics.readonly` | YouTube Analytics API: impressions, CTR, watch time, average view %, retention, traffic sources. |

Re-auth is **additive** — keep `youtube.upload` and request all three together so
uploads keep working. The new consent screen must be re-approved by the channel
owner. Until then, `analytics_optimizer.py` runs in "no-scope" mode and only
prints guidance; it never calls the API.

## Metrics to optimize (priority order)

1. **CTR (impressions click-through rate)** — strongest signal for title/hook/thumbnail quality.
2. **Average view duration / average percentage viewed** — did the hook hold? Shorts live or die here.
3. **Retention curve (audienceRetention)** — where viewers drop; informs script pacing (first 3s especially).
4. **Views in first 24h** — early velocity; YouTube's distribution flywheel.
5. **Impressions** — how much YouTube chose to surface it (downstream of the above).
6. **Publish time** — bucket performance by hour-of-day / day-of-week to tune the cron / `PIPELINE_PUBLISH_DELAY_HOURS`.
7. **Title/hook pattern** — tag each title with features (question? number? ticker symbol? emoji? word count) and regress against CTR + AVD.

## Loop design (once scopes exist)

```
runs/<id>/summary.json   ──┐  (topic, title, tags, thumbnailText, publishAt)
                           ├─► analytics_optimizer.py
YouTube Analytics API   ──┘      • join uploads→videoIds (youtube.readonly)
                                 • pull metrics per video (yt-analytics.readonly)
                                 • score title/hook/topic/publish-hour buckets
                                 • write data/analytics/insights.json
                                          │
daily_pipeline_voicebox.py packaging ◄────┘  (read insights.json as priors:
   generate_packaging() / pick_topic()        bias topic source, title style,
                                               and schedule_publish_time hour)
```

Keep it advisory first: write `insights.json`, eyeball it for a week, then let
`generate_packaging`/`pick_topic` read it as soft priors. Never let analytics
trigger an upload — the upload path stays exactly as guarded today.

## Files

- `pipeline/analytics_optimizer.py` — scaffold; scope-gated, inert by default.
- `data/analytics/insights.json` — produced by the optimizer (gitignored under `data/`).
