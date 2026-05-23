# Hostinger VPS Setup

This repository is ready to run as a Node.js app on a Hostinger VPS, with a separate Python pipeline for daily Shorts automation.

## Recommended Hostinger path

Hostinger's help center confirms Node.js support on VPS hosting and documents a Node.js app flow for CloudPanel. If you are starting from a fresh VPS, choose a Node.js-capable setup or follow the CloudPanel app flow.

Helpful docs:

- Node.js on Hostinger VPS: https://support.hostinger.com/en/articles/1583661-is-node-js-supported-at-hostinger
- CloudPanel Node.js app setup: https://support.hostinger.com/en/articles/9553137-how-to-set-up-a-node-js-application-using-hostinger-cloudpanel

## Files that matter

- `server.js` - Node.js app and OpenRouter backend
- `youtube-publishing.js` - server-side YouTube OAuth, queue, and upload logic
- `youtube.html` - YouTube OAuth and publish workspace
- `app.html` - control room UI
- `index.html` - public landing page
- `pipeline/daily_pipeline.py` - daily Python automation runner
- `pipeline/requirements.txt` - Python dependencies for the daily runner
- `pipeline/cron.example` - example cron entry
- `ecosystem.config.cjs` - PM2 config
- `.env.example` - environment template

## Deployment steps

1. Provision a Hostinger VPS.
2. If you are starting fresh, choose a Node.js-capable template or a VPS with Node.js support.
3. Upload the repository to the server or pull it from GitHub.
4. Enter the repository root on the VPS.
5. Copy `.env.example` to `.env` and fill in your real values.
6. In Google Cloud Console, create a web OAuth client for YouTube and add `https://your-domain.example/auth/youtube/callback` as an authorized redirect URI.
7. Install dependencies:

```bash
npm install
```

8. Install PM2 if it is not already available:

```bash
npm install -g pm2
```

9. Start the app using the PM2 script:

```bash
npm run pm2:start
pm2 save
```

10. Open the publisher at `/youtube`, click `Connect channel`, and finish the Google consent flow.
11. Put rendered MP4 files into `uploads/` and queue them from the publisher page.
12. For the daily pipeline, install Python dependencies and add a cron job.

## Python pipeline setup

Install ffmpeg and Python tools if they are not already present:

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg python3 python3-venv python3-pip
```

Create a virtual environment and install the pipeline dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r pipeline/requirements.txt
```

Test the pipeline once before adding cron:

```bash
python3 pipeline/daily_pipeline.py --dry-run
```

## Cron job

Use `crontab -e` and add the example from `pipeline/cron.example`. The command should point at your real repository path and your virtual environment.

## Reverse proxy

If you are mapping a custom domain to the app, point the domain or proxy to the Node.js port from `.env`.

## Environment variables

At minimum, set these values on the server:

```bash
OPENROUTER_API_KEY=sk-or-v1-your-key
OPENROUTER_MODEL=openai/gpt-4o-mini
OPENROUTER_TITLE=YouTube Automation Agent
HTTP_REFERER=https://your-domain.example
PORT=3456
APP_URL=https://your-domain.example
ELEVENLABS_API_KEY=your-elevenlabs-api-key
ELEVENLABS_VOICE_ID=your-elevenlabs-voice-id
PIPELINE_TOPIC_SOURCE=coingecko
PIPELINE_RSS_URL=https://www.coindesk.com/arc/outboundfeeds/rss/
PIPELINE_PUBLISH_ENDPOINT=http://127.0.0.1:3456/api/youtube/publish
PIPELINE_PUBLISH_DELAY_HOURS=24
PIPELINE_BACKGROUND_DIR=backgrounds
PIPELINE_TRANSCRIBE_PROVIDER=openai
PIPELINE_WHISPER_MODEL=base
OPENAI_API_KEY=your-openai-api-key
YOUTUBE_CLIENT_ID=your-google-oauth-client-id
YOUTUBE_CLIENT_SECRET=your-google-oauth-client-secret
YOUTUBE_REDIRECT_URI=https://your-domain.example/auth/youtube/callback
YOUTUBE_REFRESH_TOKEN=
YOUTUBE_DEFAULT_PRIVACY_STATUS=private
YOUTUBE_DEFAULT_CATEGORY_ID=22
```

## Operational notes

- Keep the OpenRouter API key, ElevenLabs API key, and YouTube OAuth tokens on the VPS only.
- Use PM2 so the process restarts automatically if the VPS reboots.
- Keep a backup copy of `.env` outside the repo.
- Store rendered videos and thumbnails in `uploads/`.
- The queue state and OAuth token cache live in `data/`.
- The pipeline saves run artifacts under `data/pipeline/`.
- If your Google Cloud project is unverified, uploaded videos can remain private until the YouTube audit is complete.
