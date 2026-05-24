# Daily Automation Pipeline

This folder contains the cron-safe Python runner that turns one daily topic signal into a finished YouTube Short and queues it through the existing VPS upload API.

## Daily flow

1. Pull a topic signal from CoinGecko or an RSS feed.
2. Generate a punchy 45-second spoken script.
3. Synthesize a voiceover with Voicebox or ElevenLabs.
4. Transcribe the voiceover into subtitle timestamps.
5. Resolve `ffmpeg` explicitly so cron does not depend on a perfect PATH.
6. Select a background clip and render a vertical 9:16 short.
7. Create metadata, tags, and thumbnail text.
8. Save the final MP4 into `uploads/`.
9. Queue the finished video through `/api/youtube/publish`.
10. Wait for the Node queue to confirm the upload is published, then clean up the final render and thumbnail.

## Required environment

Set these values in the VPS `.env` file:

- `OPENROUTER_API_KEY`
- `VOICEBOX_URL`
- `VOICEBOX_PROFILE_ID`
- `VOICEBOX_LANGUAGE`
- `PIPELINE_PUBLISH_ENDPOINT`
- `PIPELINE_QUEUE_ENDPOINT`
- `PIPELINE_PUBLISH_DELAY_HOURS`
- `PIPELINE_PUBLISH_TIMEOUT_MINUTES`
- `PIPELINE_POLL_SECONDS`
- `PIPELINE_TOPIC_SOURCE`
- `PIPELINE_BACKGROUND_DIR`
- `PIPELINE_TRANSCRIBE_PROVIDER`
- `FFMPEG_BINARY` if cron cannot find `ffmpeg`

Optional but recommended:

- `OPENAI_API_KEY` for Whisper transcription when `PIPELINE_TRANSCRIBE_PROVIDER=openai`
- `PIPELINE_WHISPER_MODEL` for local transcription when `PIPELINE_TRANSCRIBE_PROVIDER=local`
- `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID` only if you want the old ElevenLabs fallback

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r pipeline/requirements.txt
```

## Run once

```bash
python3 pipeline/daily_pipeline_safe.py --dry-run
```

## Cron example

Run the pipeline every day at 6:00 AM server time:

```cron
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 6 * * * cd /root/youtube-lite-pwa-run && set -a && . ./.env && set +a && ./.venv/bin/python pipeline/daily_pipeline_safe.py >> data/pipeline/cron.log 2>&1
```

Adjust the repository path to match your VPS.
