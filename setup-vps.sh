#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

say() {
  printf '\n%s\n' "$*"
}

prompt() {
  local __name="$1"
  local __label="$2"
  local __default="${3:-}"
  local __secret="${4:-false}"
  local __value=""

  if [[ "$__secret" == "true" ]]; then
    read -rsp "$__label${__default:+ [$__default]}: " __value
    printf '\n'
  else
    read -rp "$__label${__default:+ [$__default]}: " __value
  fi

  if [[ -z "$__value" ]]; then
    __value="$__default"
  fi

  printf -v "$__name" '%s' "$__value"
}

require_value() {
  local __label="$1"
  local __value="$2"
  if [[ -z "$__value" ]]; then
    printf '\nMissing required value: %s\n' "$__label" >&2
    exit 1
  fi
}

if [[ ! -f package.json ]]; then
  echo "Run this script from the repository root." >&2
  exit 1
fi

if [[ $EUID -ne 0 ]]; then
  echo "This setup script should be run as root on the VPS." >&2
  exit 1
fi

if [[ ! -f .env.example ]]; then
  echo "Missing .env.example. The repo looks incomplete." >&2
  exit 1
fi

say "YouTube Automation Agent VPS setup"
say "This will write your .env, install dependencies, start PM2, and enable boot restart."

if [[ -f .env ]]; then
  read -rp ".env already exists. Overwrite it? [y/N]: " overwrite
  if [[ ! "$overwrite" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
  fi
fi

prompt PORT "Port" "3456"
prompt APP_URL "Public URL for this app" "https://your-domain.example"
prompt OPENROUTER_API_KEY "OpenRouter API key" "" true
prompt OPENROUTER_MODEL "OpenRouter model" "openai/gpt-4o-mini"
prompt HTTP_REFERER "HTTP referer" "$APP_URL"
prompt YOUTUBE_CLIENT_ID "YouTube OAuth client ID" "" true
prompt YOUTUBE_CLIENT_SECRET "YouTube OAuth client secret" "" true
prompt YOUTUBE_REDIRECT_URI "YouTube redirect URI" "${APP_URL%/}/auth/youtube/callback"
prompt YOUTUBE_REFRESH_TOKEN "YouTube refresh token (optional)" "" true
prompt YOUTUBE_DEFAULT_PRIVACY_STATUS "Default privacy status" "private"
prompt YOUTUBE_DEFAULT_CATEGORY_ID "Default YouTube category ID" "22"
prompt SITE_NAME "Site name" "YouTube Automation Agent"

APP_URL="${APP_URL%/}"
HTTP_REFERER="${HTTP_REFERER%/}"
YOUTUBE_REDIRECT_URI="${YOUTUBE_REDIRECT_URI%/}"

require_value "APP_URL" "$APP_URL"
require_value "OPENROUTER_API_KEY" "$OPENROUTER_API_KEY"
require_value "YOUTUBE_CLIENT_ID" "$YOUTUBE_CLIENT_ID"
require_value "YOUTUBE_CLIENT_SECRET" "$YOUTUBE_CLIENT_SECRET"

say "Writing .env"
cat > .env <<EOF
NODE_ENV=production
LOG_LEVEL=info
PORT=$PORT
APP_URL=$APP_URL
OPENROUTER_API_KEY=$OPENROUTER_API_KEY
OPENROUTER_MODEL=$OPENROUTER_MODEL
OPENROUTER_TITLE=YouTube Automation Agent
HTTP_REFERER=$HTTP_REFERER
YOUTUBE_CLIENT_ID=$YOUTUBE_CLIENT_ID
YOUTUBE_CLIENT_SECRET=$YOUTUBE_CLIENT_SECRET
YOUTUBE_REDIRECT_URI=$YOUTUBE_REDIRECT_URI
YOUTUBE_REFRESH_TOKEN=$YOUTUBE_REFRESH_TOKEN
YOUTUBE_DEFAULT_PRIVACY_STATUS=$YOUTUBE_DEFAULT_PRIVACY_STATUS
YOUTUBE_DEFAULT_CATEGORY_ID=$YOUTUBE_DEFAULT_CATEGORY_ID
SITE_NAME=$SITE_NAME
EOF
chmod 600 .env

mkdir -p data uploads logs

say "Installing dependencies"
npm install

if ! command -v pm2 >/dev/null 2>&1; then
  say "Installing PM2 globally"
  npm install -g pm2
fi

say "Starting the app with PM2"
npm run pm2:start
pm2 save

PM2_BIN="$(command -v pm2)"
SERVICE_FILE="/etc/systemd/system/youtube-automation-agent-pm2.service"

say "Creating a boot-start service for PM2"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=YouTube Automation Agent PM2 Resurrect
After=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
Environment=PM2_HOME=/root/.pm2
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=$PM2_BIN resurrect
ExecStop=$PM2_BIN kill

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable youtube-automation-agent-pm2.service

say "Setup complete"
say "Health check: curl http://127.0.0.1:${PORT}/api/health"
say "YouTube dashboard: ${APP_URL}/youtube"
say "If you need to reconnect later, keep the same .env values and run: pm2 restart youtube-automation-agent --update-env"
