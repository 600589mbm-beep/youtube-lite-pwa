# Channel map — which profile is which

The app supports multiple YouTube channels via **auth profiles**
(`data/youtube-auth.<profile>.json`). Each profile is a separate channel with a
separate token. **Content must never cross channels.** Authoritative mapping:

| Profile | Channel | Channel ID | Content | Token file | Entry point |
|---|---|---|---|---|---|
| `crypto` (default) | Crypto Shorts | _(existing)_ | Crypto/markets Shorts ONLY | `data/youtube-auth.crypto.json` (legacy `youtube-auth.json`) | `pipeline/daily_pipeline_safe.py` (cron — **do not touch**) |
| `fixit` | **Fix-It Jimmy** | `UCKP48Yc9xDuPHudt9qGWRMA` | DIY / home-repair ONLY — no crypto | `data/youtube-auth.fixit.json` | `pipeline/fixit_pipeline.py` |
| `kids` | Kids / longform | `UCGXmL8spGRle5y0k2bvNnUw` | Kids / longform (ViMax) — separate | `data/youtube-auth.kids.json` | `pipeline/longform_pipeline.py` |

## Hard rules

- **crypto** content goes to the **crypto** profile only. The crypto cron sends no
  `profile`, so it uses the default (`crypto`). Leave that cron and code untouched.
- **fixit** = Fix-It Jimmy. DIY/home-repair only. Built by `fixit_pipeline.py`,
  which hard-codes `profile=fixit` and refuses any other profile. It uses NO crypto
  topics, prompts, content pillars, or crypto visuals (`fixit_content.py` +
  a neutral generated background).
- **kids** is the earlier kids/longform channel. Do **not** route Fix-It Jimmy
  content to `kids`, and do not route DIY content through the longform/ViMax lane.
- A job for an unconnected channel stays `queued` (it does not fail) and uploads
  once that profile is connected.

## Fix-It Jimmy pipeline (`fixit_pipeline.py`)

Safety mirrors `longform_pipeline.py` — dry-run by default, gated publish:

```
python pipeline/fixit_pipeline.py                 # content dry-run: prints topic/script/packaging, builds nothing
python pipeline/fixit_pipeline.py --offline       # same, force local script (no LLM/network)
python pipeline/fixit_pipeline.py --build         # render the Short (Voicebox + neutral bg + captions), no upload
FIXIT_PUBLISH=1 python pipeline/fixit_pipeline.py --build --publish   # queue to profile=fixit ONLY
```

`--publish` uploads **only if** `FIXIT_PUBLISH=1` **and** a live status check shows
`profile=fixit` connected; otherwise it refuses and prints the connect URL. See
`docs/FIXIT_CHANNEL_SETUP.md` for the one-time Chrome OAuth connect step.
