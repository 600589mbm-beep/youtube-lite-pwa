# Hostinger VPS Setup

This repository is now set up to run as a Node.js app on a Hostinger VPS.

## Recommended Hostinger path

Hostinger's help center confirms Node.js support on VPS hosting and recommends the Ubuntu 22.04 Node.js + OpenLiteSpeed template for a fast setup. If you are using CloudPanel, Hostinger also documents a Node.js app flow that uses PM2 on the site user.

Helpful docs:

- Node.js on Hostinger VPS: https://support.hostinger.com/en/articles/1583661-is-node-js-supported-at-hostinger
- CloudPanel Node.js app setup: https://support.hostinger.com/en/articles/9553137-how-to-set-up-a-node-js-application-using-hostinger-cloudpanel

## Files that matter

- `server.js` - Node.js app and OpenRouter backend
- `app.html` - control room UI
- `index.html` - public landing page
- `ecosystem.config.cjs` - PM2 config
- `.env.example` - environment template

## Deployment steps

1. Provision a Hostinger VPS.
2. If you are starting fresh, choose the Node.js-capable template or a VPS with Node.js support.
3. Upload the repository to the server or pull it from GitHub.
4. Enter the repository root on the VPS.
5. Copy `.env.example` to `.env` and fill in your real values.
6. Install dependencies:

```bash
npm install
```

7. Install PM2 if it is not already installed:

```bash
npm install -g pm2
```

8. Start the app using the PM2 ecosystem file:

```bash
pm2 start ecosystem.config.cjs --env production
pm2 save
```

9. Check the app:

- Control room: `/`
- Landing page: `/landing`
- Health check: `/api/health`

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
```

## Operational notes

- Keep the OpenRouter API key on the VPS only.
- Use PM2 so the process restarts automatically if the VPS reboots.
- Keep a backup copy of `.env` outside the repo.
- If you later add YouTube OAuth, store those credentials on the server too.
