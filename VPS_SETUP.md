# VPS Setup

Use this on the VPS after pulling the latest repository changes.

## One-time setup

```bash
cd ~/youtube-lite-pwa-run
git pull
bash setup-vps.sh
```

The script will prompt for:
- your public app URL
- OpenRouter API key
- Google OAuth client ID and secret
- the YouTube redirect URI
- optional refresh token

## After it finishes

- Open `http://127.0.0.1:3456/api/health` to confirm the app is running locally.
- Open `https://your-domain.example/youtube` to connect the YouTube channel.
- If you change values later, edit `.env` and run:

```bash
pm2 restart youtube-automation-agent --update-env
```

## Notes

- The setup script saves the PM2 process list and enables a boot-time systemd helper.
- Keep your OpenRouter and Google OAuth secrets only in `.env` on the VPS.
- If you rotate any API key, rerun the setup script or update `.env` and restart PM2.
