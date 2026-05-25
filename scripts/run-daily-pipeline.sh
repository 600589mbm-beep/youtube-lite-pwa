#!/usr/bin/env bash
#
# Safe unattended wrapper for the Voicebox-backed daily YouTube Shorts pipeline.
#
#   - cd's into the app dir and loads .env
#   - uses the repo .venv interpreter
#   - pins a reliable PATH + FFMPEG_BINARY
#   - flock guard so two renders can never overlap
#   - timeout cap so a hung render can't run forever
#   - logs to logs/daily-pipeline.log
#   - DATE GUARD: auto-disables after DISABLE_AFTER (7-day unattended window)
#
# Voicebox-only. Do NOT add ElevenLabs as a required path here.
#
# Pass --dry-run (or set DRY_RUN=1) to build the short WITHOUT queuing an upload.

set -euo pipefail

APP_DIR="/root/youtube-lite-pwa-run"
LOG_DIR="${APP_DIR}/logs"
LOG_FILE="${LOG_DIR}/daily-pipeline.log"
LOCK_FILE="${LOG_DIR}/daily-pipeline.lock"

# --- 7-day unattended window -------------------------------------------------
# After this date the wrapper exits without running. Bump or remove to extend.
DISABLE_AFTER="2026-06-01"   # inclusive last run date (set 2026-05-25)

# --- runtime cap -------------------------------------------------------------
PIPELINE_TIMEOUT="${PIPELINE_TIMEOUT:-1800s}"   # 30 min hard ceiling per run

mkdir -p "${LOG_DIR}"

log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*" >> "${LOG_FILE}"; }

# --- date guard --------------------------------------------------------------
TODAY="$(date -u +%Y-%m-%d)"
if [[ "${TODAY}" > "${DISABLE_AFTER}" ]]; then
  log "SKIP: today ${TODAY} is past DISABLE_AFTER ${DISABLE_AFTER}; unattended window closed. Remove the cron entry or bump DISABLE_AFTER to re-enable."
  exit 0
fi

cd "${APP_DIR}"

# --- environment -------------------------------------------------------------
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

# Load .env (the pipeline also loads it via dotenv, but exporting here keeps the
# venv subprocess and any child tools consistent).
if [[ -f "${APP_DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "${APP_DIR}/.env"
  set +a
fi

# Reliable ffmpeg: prefer system binary, fall back to the imageio-ffmpeg one.
if [[ -z "${FFMPEG_BINARY:-}" ]]; then
  if command -v ffmpeg >/dev/null 2>&1; then
    export FFMPEG_BINARY="$(command -v ffmpeg)"
  elif [[ -x "${APP_DIR}/.venv/lib/python3.12/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-x86_64-v7.0.2" ]]; then
    export FFMPEG_BINARY="${APP_DIR}/.venv/lib/python3.12/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-x86_64-v7.0.2"
  fi
fi

PY="${APP_DIR}/.venv/bin/python"
[[ -x "${PY}" ]] || PY="python3"

# --- args --------------------------------------------------------------------
PIPELINE_ARGS=("pipeline/daily_pipeline_safe.py")
if [[ "${DRY_RUN:-0}" == "1" ]]; then
  PIPELINE_ARGS+=("--dry-run")
fi
PIPELINE_ARGS+=("$@")

# --- run under flock so renders never overlap --------------------------------
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  log "SKIP: another pipeline run holds ${LOCK_FILE}; not starting a second render."
  exit 0
fi

log "START: ${PY} ${PIPELINE_ARGS[*]} (ffmpeg=${FFMPEG_BINARY:-unset}, timeout=${PIPELINE_TIMEOUT})"
set +e
timeout "${PIPELINE_TIMEOUT}" "${PY}" "${PIPELINE_ARGS[@]}" >> "${LOG_FILE}" 2>&1
rc=$?
set -e

if [[ ${rc} -eq 124 ]]; then
  log "FAIL: pipeline exceeded ${PIPELINE_TIMEOUT} and was killed by timeout."
elif [[ ${rc} -ne 0 ]]; then
  log "FAIL: pipeline exited ${rc}."
else
  log "OK: pipeline completed cleanly."
fi
exit ${rc}
