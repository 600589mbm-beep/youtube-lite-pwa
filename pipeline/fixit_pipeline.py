#!/usr/bin/env python3
"""Fix-It Jimmy DIY Shorts entrypoint — SEPARATE from the crypto Shorts cron.

Builds ONE DIY / home-repair Short for the Fix-It Jimmy channel and, only when
explicitly told to AND the channel is actually connected, hands it off through
the SAME proven upload queue the crypto pipeline uses — but to `profile=fixit`
ONLY. It uses NO crypto topics, prompts, content pillars, or crypto visuals.

Safety model (mirrors longform_pipeline.py):
  * Default = content dry-run: print topic/script/packaging/payload, build
    nothing, upload nothing, exit 0.
  * --build      : also render the video (Voicebox voice + neutral background +
                   captions). Still uploads nothing.
  * --publish    : hand off to the upload queue, but ONLY if BOTH:
                       - env FIXIT_PUBLISH=1, AND
                       - a live status check shows profile=fixit connected.
                   Always targets profile=fixit; refuses any other profile.

  python pipeline/fixit_pipeline.py                       # dry-run preview
  python pipeline/fixit_pipeline.py --build               # render only, no upload
  FIXIT_PUBLISH=1 python pipeline/fixit_pipeline.py --build --publish

See docs/CHANNEL_MAP.md and docs/FIXIT_CHANNEL_SETUP.md.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixit_content import (  # noqa: E402
    build_fixit_packaging,
    generate_fixit_script,
    pick_fixit_topic,
)

ROOT_DIR = Path(__file__).resolve().parents[1]
FIXIT_RUNS = ROOT_DIR / "data" / "pipeline" / "fixit_runs"

FIXIT_PROFILE = "fixit"  # hard-coded: this entrypoint only ever targets Fix-It Jimmy
APP_INTERNAL_URL = os.getenv("APP_INTERNAL_URL", "http://127.0.0.1:3456").rstrip("/")
PUBLISH_ENDPOINT = os.getenv("PIPELINE_PUBLISH_ENDPOINT", f"{APP_INTERNAL_URL}/api/youtube/publish")
QUEUE_ENDPOINT = os.getenv("PIPELINE_QUEUE_ENDPOINT", f"{APP_INTERNAL_URL}/api/youtube/queue")
STATUS_ENDPOINT = os.getenv("PIPELINE_STATUS_ENDPOINT", f"{APP_INTERNAL_URL}/api/youtube/status")

TARGET_SIZE = (1080, 1920)
TARGET_SECONDS = 45


def fixit_status() -> dict:
    """Live status for profile=fixit (connected?, profiles list)."""
    url = f"{STATUS_ENDPOINT}?{urlencode({'profile': FIXIT_PROFILE})}"
    with urlopen(url, timeout=15) as resp:  # noqa: S310 (localhost app)
        return json.loads(resp.read().decode("utf-8"))


def render_neutral_background(output_path: Path, seconds: int, ffmpeg_binary: str, seed: int = 0) -> None:
    """Generate a clean, copyright-safe NEUTRAL vertical background.

    Deliberately NOT make_crypto_background: a calm steel-blue/teal "workshop"
    gradient — no crypto charts, tickers, gold/green coin styling, or badges.
    Falls back to a solid color if the gradients filter is unavailable.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    w, h = TARGET_SIZE
    gradient = (
        f"gradients=s={w}x{h}:c0=0x14304a:c1=0x2f6f8f:"
        f"x0=0:y0=0:x1={w}:y1={h}:d=6:speed=0.015:nb_colors=2:seed={seed}"
    )
    common_tail = ["-t", str(seconds), "-r", "30", "-pix_fmt", "yuv420p",
                   "-c:v", "libx264", "-preset", "veryfast", str(output_path)]
    try:
        cmd = [ffmpeg_binary, "-y", "-f", "lavfi", "-i", gradient, *common_tail]
        completed = subprocess.run(cmd, capture_output=True, text=True)
        if completed.returncode == 0:
            return
    except Exception:
        pass
    # Fallback: flat neutral steel-blue.
    cmd = [ffmpeg_binary, "-y", "-f", "lavfi", "-i",
           f"color=c=0x1b3a57:s={w}x{h}:r=30", *common_tail]
    completed = subprocess.run(cmd, capture_output=True, text=True)
    if completed.returncode != 0:
        raise SystemExit(f"ffmpeg failed building neutral background:\n{completed.stderr}")


def _build_video(topic: str, script: str, packaging: dict, run_dir: Path):
    """Render the DIY Short with reused, channel-neutral helpers. Returns
    (final_video_path, payload-without-profile)."""
    # Lazy import: keeps dry-run import-clean and avoids heavy deps until --build.
    from daily_pipeline_voicebox import (
        load_config, resolve_ffmpeg_binary, synthesize_voiceover,
        transcribe_audio, write_srt, render_final_short, relative_path,
        schedule_publish_time, UPLOADS_DIR,
    )

    config = load_config()
    ffmpeg_binary = resolve_ffmpeg_binary(config.ffmpeg_binary)
    os.environ.setdefault("IMAGEIO_FFMPEG_EXE", ffmpeg_binary)
    os.environ.setdefault("FFMPEG_BINARY", ffmpeg_binary)
    run_dir.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

    voiceover_path = run_dir / "voiceover.mp3"
    synthesize_voiceover(script, voiceover_path, config)  # neutral Voicebox TTS

    segments = transcribe_audio(voiceover_path, config)
    subtitles_path = run_dir / "subtitles.srt"
    write_srt(segments, subtitles_path)

    background_path = run_dir / "background.mp4"
    render_neutral_background(background_path, TARGET_SECONDS, ffmpeg_binary,
                              seed=abs(hash(topic)) % 1000)

    run_id = run_dir.name
    final_video_path = UPLOADS_DIR / f"fixit_short_{run_id}.mp4"
    # No intro/cta cards -> none of the crypto-news overlays are applied.
    render_final_short(background_path, voiceover_path, subtitles_path,
                       final_video_path, ffmpeg_binary)

    publish_at = schedule_publish_time(config.publish_delay_hours)
    payload = {
        "videoPath": relative_path(final_video_path),
        "thumbnailPath": "",
        "title": packaging["title"],
        "description": packaging["description"],
        "tags": packaging["tags"],
        "privacyStatus": "private" if publish_at else config.default_privacy_status,
        "categoryId": config.default_category_id,
        "publishAt": publish_at,
    }
    return final_video_path, payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build ONE Fix-It Jimmy DIY Short (not the crypto cron).")
    parser.add_argument("--topic", help="Manual DIY topic (default: a built-in DIY topic).")
    parser.add_argument("--seed", type=int, default=None, help="Deterministic topic pick.")
    parser.add_argument("--offline", action="store_true", help="Force local script (no LLM/network).")
    parser.add_argument("--build", action="store_true", help="Render the video (still no upload).")
    parser.add_argument("--publish", action="store_true",
                        help="Queue to profile=fixit (needs FIXIT_PUBLISH=1 AND fixit connected).")
    args = parser.parse_args(argv)

    topic = pick_fixit_topic(args.topic, args.seed)
    script = generate_fixit_script(topic, config=_maybe_config(args.offline), offline=args.offline)
    packaging = build_fixit_packaging(topic, script)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = FIXIT_RUNS / run_id

    summary = {
        "runId": run_id,
        "profile": FIXIT_PROFILE,
        "channel": "Fix-It Jimmy (UCKP48Yc9xDuPHudt9qGWRMA)",
        "topic": topic,
        "script": script,
        "packaging": packaging,
        "built": False,
        "queued": False,
    }

    # --- Content dry-run (default) -----------------------------------------
    if not args.build and not args.publish:
        summary["note"] = "content dry-run: nothing built, nothing uploaded"
        print(json.dumps(summary, indent=2))
        return 0

    # --- Build (render) -----------------------------------------------------
    final_video_path, payload = _build_video(topic, script, packaging, run_dir)
    payload["profile"] = FIXIT_PROFILE  # ALWAYS fixit — never crypto/kids
    summary["built"] = True
    summary["payload"] = payload
    summary["finalVideoPath"] = payload["videoPath"]

    if not args.publish:
        summary["note"] = "build-only: rendered, not uploaded (no --publish)"
        print(json.dumps(summary, indent=2))
        return 0

    # --- Publish gate -------------------------------------------------------
    if os.getenv("FIXIT_PUBLISH", "0").strip() != "1":
        summary["note"] = "publish refused: FIXIT_PUBLISH=1 not set"
        print(json.dumps(summary, indent=2))
        return 0

    status = fixit_status()
    if not status.get("connected"):
        summary["note"] = "publish refused: profile=fixit NOT connected"
        summary["connectUrl"] = status.get("connectUrlForProfile")
        print(json.dumps(summary, indent=2))
        return 0

    # Hard guard: never let a non-fixit profile slip through to upload.
    assert payload.get("profile") == FIXIT_PROFILE, "refusing: payload profile is not fixit"

    from daily_pipeline_voicebox import queue_upload, wait_for_publish
    result = queue_upload(payload, PUBLISH_ENDPOINT)
    job_id = ((result or {}).get("job") or {}).get("id")
    if not job_id:
        raise SystemExit("Publish API returned no job id; aborting before confirmation.")
    wait_for_publish(job_id, QUEUE_ENDPOINT, timeout_minutes=120, poll_seconds=20)

    summary["queued"] = True
    summary["jobId"] = job_id
    summary["queueResult"] = result
    summary["note"] = "queued to profile=fixit"
    print(json.dumps(summary, indent=2))
    return 0


def _maybe_config(offline: bool):
    """Load config only if we might call the LLM; dry-run/offline needs none."""
    if offline:
        return None
    try:
        from daily_pipeline_voicebox import load_config
        return load_config()
    except Exception:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
