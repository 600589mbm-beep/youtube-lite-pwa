# YouTube Automation Agent

This repository now has four parts:

- `index.html` - the public landing page that explains the system
- `app.html` - the VPS control room that talks to OpenRouter from the server side
- `youtube.html` - the YouTube OAuth and publish workspace
- `pipeline/daily_pipeline.py` - the cron-ready daily short builder

The backend is a small Node.js app that serves the pages, checks health, generates a YouTube content pack through OpenRouter, and uploads or schedules videos through the YouTube Data API without exposing your API keys to the browser.

## What the VPS app does

- Serves the public landing page at `/landing`
- Serves the OpenRouter control room at `/`
- Serves the YouTube publisher at `/youtube`
- Exposes `/api/health`
- Exposes `/api/generate` for content pack generation
- Exposes `/api/youtube/status` for channel connection state
- Exposes `/api/youtube/queue` for queued publish jobs
- Exposes `/api/youtube/publish` to queue a video upload
- Exposes `/api/youtube/disconnect` to clear the stored OAuth token
- Uses `OPENROUTER_API_KEY`, `ELEVENLABS_API_KEY`, and the YouTube OAuth tokens only on the server
- Lets the Python pipeline build a finished short and hand it off to the existing upload queue

## Files

- `index.html` - marketing/landing page
- `app.html` - live OpenRouter console for the VPS
- `youtube.html` - YouTube OAuth and publish workspace
- `youtube-publishing.js` - server-side YouTube OAuth, queue, and upload logic
- `pipeline/daily_pipeline.py` - daily cron pipeline for topic pull, script generation, voiceover, subtitles, rendering, and queueing
- `pipeline/requirements.txt` - Python dependencies for the pipeline
- `server.js` - Express server and route wiring
- `package.json` - Node.js app metadata and scripts
- `.env.example` - environment variable template
- `manifest.webmanifest` - PWA metadata for the landing page
- `sw.js` - service worker for the static site shell
- `icon.svg` - app icon
- `data/` - runtime token and queue state
- `uploads/` - rendered video and thumbnail files
- `HOSTINGER_VPS.md` - VPS setup guide with Hostinger-specific steps

## Environment variables

Copy `.env.example` to `.env` and fill in your values.

```bash
PORT=3456
APP_URL=https://your-domain.example

# OpenRouter
OPENROUTER_API_KEY=sk-or-v1-your-key-here
OPENROUTER_MODEL=openai/gpt-4o-mini
OPENROUTER_TITLE=YouTube Automation Agent
HTTP_REFERER=https://your-domain.example

# ElevenLabs voiceover generation
ELEVENLABS_API_KEY=your-elevenlabs-api-key
ELEVENLABS_VOICE_ID=your-elevenlabs-voice-id
ELEVENLABS_MODEL_ID=eleven_multilingual_v2

# Daily automation pipeline
PIPELINE_TOPIC_SOURCE=coingecko
PIPELINE_RSS_URL=https://www.coindesk.com/arc/outboundfeeds/rss/
PIPELINE_LLM_MODEL=openai/gpt-4o-mini
PIPELINE_BACKGROUND_DIR=backgrounds
PIPELINE_PUBLISH_ENDPOINT=http://127.0.0.1:3456/api/youtube/publish
PIPELINE_PUBLISH_DELAY_HOURS=24
PIPELINE_PRIVACY_STATUS=private
PIPELINE_CATEGORY_ID=22
PIPELINE_TRANSCRIBE_PROVIDER=openai
PIPELINE_WHISPER_MODEL=base

# Optional Whisper transcription fallback
OPENAI_API_KEY=your-openai-api-key

# YouTube OAuth
YOUTUBE_CLIENT_ID=your-google-oauth-client-id
YOUTUBE_CLIENT_SECRET=your-google-oauth-client-secret
YOUTUBE_REDIRECT_URI=https://your-domain.example/auth/youtube/callback
YOUTUBE_REFRESH_TOKEN=
YOUTUBE_DEFAULT_PRIVACY_STATUS=private
YOUTUBE_DEFAULT_CATEGORY_ID=22
```

## Local run

```bash
npm install
npm start
```

Then open:

- Control room: `http://localhost:3456/`
- YouTube publisher: `http://localhost:3456/youtube`
- Landing page: `http://localhost:3456/landing`
- Health check: `http://localhost:3456/api/health`

## Daily pipeline

The Python runner in `pipeline/daily_pipeline.py` follows the exact production flow you described:

1. Pull a topic from CoinGecko or an RSS feed.
2. Generate a 45-second spoken script.
3. Synthesize an MP3 with ElevenLabs.
4. Transcribe the audio into subtitle timestamps.
5. Pick a background clip and render a 9:16 short.
6. Create title, description, tags, and thumbnail text.
7. Export the final video into `uploads/`.
8. Queue the finished file through `/api/youtube/publish` for upload and scheduling.

Install the Python dependencies with:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r pipeline/requirements.txt
```

Then run it once with:

```bash
python3 pipeline/daily_pipeline.py --dry-run
```

A cron example is included in `pipeline/cron.example`.

## Hostinger VPS deployment

For the full step-by-step VPS setup, use [HOSTINGER_VPS.md](./HOSTINGER_VPS.md).

Quick version:

1. Create or open a Hostinger VPS with Node.js support.
2. Upload the repository to the server or pull it from GitHub.
3. Install Node dependencies with `npm install`.
4. Set the environment variables from `.env.example`.
5. Install PM2 if it is not already available:

```bash
npm install -g pm2
```

6. Start the app:

```bash
npm run pm2:start
pm2 save
```

7. Open the YouTube publisher at `/youtube`, click `Connect channel`, and finish the Google OAuth flow.
8. Put rendered MP4 files into `uploads/` and queue them from the publisher page.
9. For full automation, install the Python dependencies and add the cron job from `pipeline/cron.example`.

If you are using Hostinger CloudPanel, make sure the app port matches the `PORT` value in `.env`.

## YouTube notes

- The OAuth redirect URI must match the Google Cloud Console entry exactly.
- The publisher requests the `https://www.googleapis.com/auth/youtube.upload` scope.
- Scheduled uploads should use `privacyStatus=private` with a future `publishAt` value.
- Unverified API projects created after 28 July 2020 can keep uploads private until the project is audited.

Helpful docs:

- OAuth for web server apps: https://developers.google.com/identity/protocols/oauth2/web-server
- YouTube OAuth guide: https://developers.google.com/youtube/v3/guides/authentication
- Video upload reference: https://developers.google.com/youtube/v3/docs/videos/insert

## OpenRouter notes

The backend uses OpenRouter's Chat Completions endpoint and sends the recommended attribution headers server-side.

- API reference: https://openrouter.ai/docs/api/reference/overview/
- Endpoint used here: `https://openrouter.ai/api/v1/chat/completions`

## Important

- Keep your OpenRouter key, ElevenLabs key, and YouTube OAuth tokens on the server only.
- The landing page remains available for sharing, while the control room and publisher are what you run on the VPS.
- If you want, the next step after this is wiring the pipeline to a template pack for background footage, captions, and thumbnail styles.
