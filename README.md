# YouTube Automation Agent

This repository now has two parts:

- `index.html` - the public landing page that explains the system
- `app.html` - the VPS control room that talks to OpenRouter from the server side

The backend is a small Node.js app that serves the pages, checks health, and generates a YouTube content pack through OpenRouter without exposing your API key to the browser.

## What the VPS app does

- Serves the public landing page at `/landing`
- Serves the OpenRouter control room at `/`
- Exposes `/api/health`
- Exposes `/api/generate` for content pack generation
- Uses `OPENROUTER_API_KEY` only on the server

## Files

- `index.html` - marketing/landing page
- `app.html` - live OpenRouter console for the VPS
- `server.js` - Express server and OpenRouter integration
- `package.json` - Node.js app metadata and scripts
- `.env.example` - environment variable template
- `manifest.webmanifest` - PWA metadata for the landing page
- `sw.js` - service worker for the static site shell
- `icon.svg` - app icon
- `HOSTINGER_VPS.md` - VPS setup guide with Hostinger-specific steps

## Environment variables

Copy `.env.example` to `.env` and fill in your values.

```bash
PORT=3456
APP_URL=https://your-domain.example
OPENROUTER_API_KEY=sk-or-v1-your-key-here
OPENROUTER_MODEL=openai/gpt-4o-mini
OPENROUTER_TITLE=YouTube Automation Agent
HTTP_REFERER=https://your-domain.example
```

## Local run

```bash
npm install
npm start
```

Then open:

- Control room: `http://localhost:3456/`
- Landing page: `http://localhost:3456/landing`
- Health check: `http://localhost:3456/api/health`

## Hostinger VPS deployment

For the full step-by-step VPS setup, use [HOSTINGER_VPS.md](./HOSTINGER_VPS.md).

Quick version:

1. Create or open a Hostinger VPS with Node.js support.
2. Upload the repository to the server or pull it from GitHub.
3. Install dependencies with `npm install`.
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

7. Check the app:

- Control room: `/`
- Landing page: `/landing`
- Health check: `/api/health`

If you are using Hostinger CloudPanel, make sure the app port matches the `PORT` value in `.env`.

## OpenRouter notes

The backend uses OpenRouter's Chat Completions endpoint and sends the recommended attribution headers server-side.

- API reference: https://openrouter.ai/docs/api/reference/overview/
- Endpoint used here: `https://openrouter.ai/api/v1/chat/completions`

## Important

- Keep your OpenRouter key on the server only.
- The app currently generates content packs. If you want, the next step is wiring those drafts into YouTube OAuth and publishing.
- The landing page remains available for sharing, while the control room is what you run on the VPS.
