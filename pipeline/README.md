# Daily Automation Pipeline

This folder contains the cron-ready Python runner that turns one daily topic signal into a finished YouTube Short and queues it through the existing VPS upload API.

## Daily flow

1. Pull a topic signal from CoinGecko or an RSS feed.
2. Generate a punchy 45-second spoken script.
3. Synthesize a voiceover with ElevenLabs.
4. Transcribe the voiceover into subtitle timestamps.
5. Select a background clip and render a vertical 9:16 short.
6. Create metadata, tags, and a thumbnail.
7. Save the final MP4 into `uploads/`.
8. Queue the finished video through `/api/youtube/publish`.

## Required environment

Set these values in the VPS `.env` file:

- `OPENROUTER_API_KEY`
- `ELEVENLABS_API_KEY`
- `ELEVENLABS_VOICE_ID`
- `PIPELINE_PUBLISH_ENDPOINT`
- `PIPELINE_PUBLISH_DELAY_HOURS`
- `PIPELINE_TOPIC_SOURCE`
- `PIPELINE_BACKGROUND_DIR`
- `PIPELINE_TRANSCRIBE_PROVIDER`

Optional but recommended:

- `OPENAI_API_KEY` for Whisper transcription when `PIPELINE_TRANSCRIBE_PROVIDER=openai`
- `PIPELINE_WHISPER_MODEL` for local transcription when `PIPELINE_TRANSCRIBE_PROVIDER=local`

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r pipeline/requirements.txt
```

## Run once

```bash
python3 pipeline/daily_pipeline.py --dry-run
```

## Cron example

Run the pipeline every day at 6:00 AM server time:

```cron
0 6 * * * cd /root/youtube-lite-pwa-run && ./.venv/bin/python pipeline/daily_pipeline.py >> data/pipeline/cron.log 2>&1
```

Adjust the repository path to match your VPS.
