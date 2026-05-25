#!/usr/bin/env python3
"""Cron-safe daily YouTube Shorts pipeline using Voicebox for voiceover generation.

This runner is designed for VPS cron jobs. It:
- pulls a topic from CoinGecko or RSS
- generates a 45-second script
- synthesizes voiceover with Voicebox
- transcribes the audio into subtitles
- renders a vertical short with ffmpeg and MoviePy
- generates title/description/tags/thumbnail text
- queues the finished video through the existing Node upload API
- waits for the Node queue to mark the job published, then cleans up files

The script is intentionally defensive about the three common VPS traps:
- it resolves ffmpeg explicitly instead of assuming PATH is complete
- it retries flaky network calls
- it deletes temp assets after upload completes so the VPS does not fill up
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests
from dotenv import load_dotenv
from moviepy.editor import ColorClip, VideoFileClip, concatenate_videoclips
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont

ROOT_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT_DIR / ".env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)
load_dotenv()

UPLOADS_DIR = ROOT_DIR / "uploads"
DATA_DIR = ROOT_DIR / "data" / "pipeline"
RUNS_DIR = DATA_DIR / "runs"
BACKGROUND_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
TARGET_SIZE = (1080, 1920)
TARGET_SECONDS = 45
DEFAULT_TOPIC_SOURCE = "coingecko"
DEFAULT_RSS_URL = "https://www.coindesk.com/arc/outboundfeeds/rss/"
DEFAULT_PUBLISH_ENDPOINT = "http://127.0.0.1:3456/api/youtube/publish"
DEFAULT_QUEUED_ENDPOINT = "http://127.0.0.1:3456/api/youtube/queue"
DEFAULT_LLM_MODEL = "openai/gpt-4o-mini"
DEFAULT_WHISPER_MODEL = "base"
DEFAULT_PUBLISH_TIMEOUT_MINUTES = 60
DEFAULT_POLL_SECONDS = 15
DEFAULT_VOICEBOX_URL = "http://127.0.0.1:17493"


@dataclass
class PipelineConfig:
    openrouter_api_key: str
    openrouter_model: str
    openrouter_title: str
    http_referer: str
    openrouter_base_url: str
    voicebox_url: str
    voicebox_profile_id: str
    voicebox_language: str
    voicebox_engine: str
    openai_api_key: str
    transcription_provider: str
    whisper_model: str
    topic_source: str
    rss_url: str
    background_dir: Path
    publish_endpoint: str
    queue_endpoint: str
    publish_delay_hours: int
    publish_timeout_minutes: int
    poll_seconds: int
    default_category_id: str
    default_privacy_status: str
    ffmpeg_binary: str


def main() -> int:
    parser = argparse.ArgumentParser(description="Build, queue, and clean up a daily YouTube Short.")
    parser.add_argument("--topic", help="Override the daily topic with a manual prompt.")
    parser.add_argument("--topic-source", choices=["coingecko", "rss"], help="Override the topic source for this run.")
    parser.add_argument("--dry-run", action="store_true", help="Build the short but skip the queue/publish step.")
    parser.add_argument("--skip-publish", action="store_true", help="Alias for --dry-run.")
    parser.add_argument("--publish-delay-hours", type=int, help="Override how far in the future the YouTube publishAt time is set.")
    args = parser.parse_args()

    config = load_config()
    ffmpeg_binary = resolve_ffmpeg_binary(config.ffmpeg_binary)
    os.environ.setdefault("IMAGEIO_FFMPEG_EXE", ffmpeg_binary)
    os.environ.setdefault("FFMPEG_BINARY", ffmpeg_binary)

    ensure_directories()

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    topic = pick_topic(args.topic, args.topic_source or config.topic_source, config)
    spoken_script = retry_call("OpenRouter script generation", lambda: generate_spoken_script(topic, config))
    packaging = retry_call("OpenRouter packaging generation", lambda: generate_packaging(topic, spoken_script, config))

    title = sanitize_title(packaging.get("title", ""), 60)
    if "#shorts" not in title.lower():
        title = ensure_short_tag(title, 60)
    description = normalize_text(packaging.get("description", ""))
    tags = normalize_tags(packaging.get("tags", []))
    thumbnail_text = sanitize_thumbnail_text(packaging.get("thumbnail_text", ""))

    voiceover_path = run_dir / "voiceover.mp3"
    retry_call("Voicebox voiceover", lambda: synthesize_voiceover(spoken_script, voiceover_path, config))

    transcript_segments = retry_call("Whisper transcription", lambda: transcribe_audio(voiceover_path, config))
    subtitles_path = run_dir / "subtitles.srt"
    write_srt(transcript_segments, subtitles_path)

    background_source = choose_background_clip(config.background_dir)
    background_path = run_dir / "background.mp4"
    render_background(background_source, background_path, TARGET_SECONDS)

    final_video_path = UPLOADS_DIR / f"final_short_{run_id}.mp4"
    render_final_short(background_path, voiceover_path, subtitles_path, final_video_path, ffmpeg_binary)

    thumbnail_path: Optional[Path] = None
    if thumbnail_text:
        thumbnail_path = UPLOADS_DIR / f"final_short_{run_id}.jpg"
        create_thumbnail(background_source, thumbnail_text, thumbnail_path)

    publish_at = schedule_publish_time(args.publish_delay_hours if args.publish_delay_hours is not None else config.publish_delay_hours)
    publish_payload = {
        "videoPath": relative_path(final_video_path),
        "thumbnailPath": relative_path(thumbnail_path) if thumbnail_path else "",
        "title": title,
        "description": description,
        "tags": tags,
        "privacyStatus": "private" if publish_at else config.default_privacy_status,
        "categoryId": config.default_category_id,
        "publishAt": publish_at,
    }

    queue_result: Dict[str, Any] = {}
    if not (args.dry_run or args.skip_publish):
        queue_result = retry_call("Node publish queue", lambda: queue_upload(publish_payload, config.publish_endpoint))
        cleanup_paths([voiceover_path, subtitles_path, background_path])

        job_id = (queue_result.get("job") or {}).get("id")
        if not job_id:
            raise SystemExit("The publish API did not return a job id, so the pipeline cannot confirm cleanup safely.")

        retry_call(
            "YouTube publish confirmation",
            lambda: wait_for_publish(job_id, config.queue_endpoint, config.publish_timeout_minutes, config.poll_seconds),
            attempts=max(1, config.publish_timeout_minutes * 60 // max(1, config.poll_seconds)),
            base_delay=max(1, config.poll_seconds),
            stop_on_false=True,
        )

        cleanup_paths([final_video_path, thumbnail_path])
    else:
        cleanup_paths([voiceover_path, subtitles_path, background_path])

    summary = {
        "runId": run_id,
        "topic": topic,
        "title": title,
        "description": description,
        "tags": tags,
        "thumbnailText": thumbnail_text,
        "finalVideoPath": relative_path(final_video_path),
        "thumbnailPath": relative_path(thumbnail_path) if thumbnail_path else None,
        "publishAt": publish_at,
        "queued": bool(queue_result),
        "queueResult": queue_result,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


def load_config() -> PipelineConfig:
    openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not openrouter_api_key:
        raise SystemExit("OPENROUTER_API_KEY is required for the pipeline.")

    voicebox_url = os.getenv("VOICEBOX_URL", DEFAULT_VOICEBOX_URL).strip() or DEFAULT_VOICEBOX_URL
    voicebox_profile_id = os.getenv("VOICEBOX_PROFILE_ID", "").strip()
    if not voicebox_profile_id:
        raise SystemExit("VOICEBOX_PROFILE_ID is required for the pipeline.")

    background_dir = Path(os.getenv("PIPELINE_BACKGROUND_DIR", str(ROOT_DIR / "backgrounds")).strip())
    if not background_dir.is_absolute():
        background_dir = ROOT_DIR / background_dir

    topic_source = os.getenv("PIPELINE_TOPIC_SOURCE", DEFAULT_TOPIC_SOURCE).strip().lower() or DEFAULT_TOPIC_SOURCE
    if topic_source not in {"coingecko", "rss"}:
        topic_source = DEFAULT_TOPIC_SOURCE

    ffmpeg_binary = os.getenv("FFMPEG_BINARY", "").strip()
    voicebox_language = os.getenv("VOICEBOX_LANGUAGE", "").strip()
    # Voicebox engine to request. Defaults to the lightweight CPU engine
    # (Kokoro, 82M) so generation fits in memory on a GPU-less VPS. Heavier
    # engines like qwen 1.7B can OOM under constrained container memory.
    voicebox_engine = os.getenv("VOICEBOX_ENGINE", "kokoro").strip() or "kokoro"

    return PipelineConfig(
        openrouter_api_key=openrouter_api_key,
        openrouter_model=os.getenv("PIPELINE_LLM_MODEL", os.getenv("OPENROUTER_MODEL", DEFAULT_LLM_MODEL)).strip() or DEFAULT_LLM_MODEL,
        openrouter_title=os.getenv("OPENROUTER_TITLE", "YouTube Automation Agent").strip() or "YouTube Automation Agent",
        http_referer=os.getenv("HTTP_REFERER", os.getenv("APP_URL", "http://localhost:3456")).strip(),
        openrouter_base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").strip() or "https://openrouter.ai/api/v1",
        voicebox_url=voicebox_url,
        voicebox_profile_id=voicebox_profile_id,
        voicebox_language=voicebox_language,
        voicebox_engine=voicebox_engine,
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        transcription_provider=os.getenv("PIPELINE_TRANSCRIBE_PROVIDER", "openai").strip().lower() or "openai",
        whisper_model=os.getenv("PIPELINE_WHISPER_MODEL", DEFAULT_WHISPER_MODEL).strip() or DEFAULT_WHISPER_MODEL,
        topic_source=topic_source,
        rss_url=os.getenv("PIPELINE_RSS_URL", DEFAULT_RSS_URL).strip() or DEFAULT_RSS_URL,
        background_dir=background_dir,
        publish_endpoint=os.getenv("PIPELINE_PUBLISH_ENDPOINT", DEFAULT_PUBLISH_ENDPOINT).strip() or DEFAULT_PUBLISH_ENDPOINT,
        queue_endpoint=os.getenv("PIPELINE_QUEUE_ENDPOINT", DEFAULT_QUEUED_ENDPOINT).strip() or DEFAULT_QUEUED_ENDPOINT,
        publish_delay_hours=max(0, int(os.getenv("PIPELINE_PUBLISH_DELAY_HOURS", "24"))),
        publish_timeout_minutes=max(1, int(os.getenv("PIPELINE_PUBLISH_TIMEOUT_MINUTES", str(DEFAULT_PUBLISH_TIMEOUT_MINUTES)))),
        poll_seconds=max(5, int(os.getenv("PIPELINE_POLL_SECONDS", str(DEFAULT_POLL_SECONDS)))),
        default_category_id=os.getenv("PIPELINE_CATEGORY_ID", os.getenv("YOUTUBE_DEFAULT_CATEGORY_ID", "22")).strip() or "22",
        default_privacy_status=normalize_privacy_status(os.getenv("PIPELINE_PRIVACY_STATUS", "private")),
        ffmpeg_binary=ffmpeg_binary,
    )


def synthesize_voiceover(script: str, output_path: Path, config: PipelineConfig) -> None:
    endpoint = f"{config.voicebox_url.rstrip('/')}/generate"
    payload: Dict[str, Any] = {
        "text": script,
        "profile_id": config.voicebox_profile_id,
    }
    if config.voicebox_language:
        payload["language"] = config.voicebox_language

    if config.voicebox_engine:
        payload["engine"] = config.voicebox_engine

    response = requests.post(endpoint, json=payload, timeout=180)
    response.raise_for_status()

    data: Dict[str, Any] = {}
    try:
        parsed = response.json()
        if isinstance(parsed, dict):
            data = parsed
    except ValueError:
        data = {}

    # Fast paths: a Voicebox build that answers synchronously with a usable
    # local path, URL, or raw audio body. Tried first for forward-compat.
    if _write_audio_from_response(data, response, output_path):
        return

    # Async path (current Voicebox API): POST /generate returns immediately
    # with {"id": ..., "status": "generating"}. Poll the generation status,
    # then download the finished audio from /audio/{id}.
    generation_id = data.get("id")
    if not generation_id:
        raise SystemExit("Voicebox did not return an audio file or a generation id.")

    base = config.voicebox_url.rstrip("/")
    deadline = time.monotonic() + 600  # 10 min cap for CPU synthesis
    while time.monotonic() < deadline:
        status_resp = requests.get(f"{base}/generate/{generation_id}/status", timeout=30)
        status_resp.raise_for_status()
        body = status_resp.text.strip()
        if body.startswith("data:"):  # SSE-style framing
            body = body[len("data:"):].strip()
        try:
            status_data = json.loads(body)
        except ValueError:
            status_data = {}
        status = str(status_data.get("status", "")).lower()
        if status in {"complete", "completed", "done", "success", "succeeded"}:
            break
        if status in {"failed", "error", "cancelled", "canceled"}:
            raise SystemExit(
                f"Voicebox generation {generation_id} failed: {status_data.get('error') or status}"
            )
        time.sleep(3)
    else:
        raise SystemExit(f"Voicebox generation {generation_id} timed out.")

    audio_resp = requests.get(f"{base}/audio/{generation_id}", timeout=180)
    audio_resp.raise_for_status()
    if not audio_resp.content:
        raise SystemExit(f"Voicebox returned empty audio for generation {generation_id}.")
    output_path.write_bytes(audio_resp.content)


def _write_audio_from_response(data: Dict[str, Any], response, output_path: Path) -> bool:
    """Write audio if the /generate response already carries it. Returns True on success."""
    audio_path = data.get("audio_path")
    if audio_path:
        source = Path(str(audio_path)).expanduser()
        if not source.is_absolute():
            source = (ROOT_DIR / source).resolve()
        if source.exists():
            shutil.copyfile(source, output_path)
            return True

    for candidate_key in ("audio_url", "download_url", "url"):
        audio_url = data.get(candidate_key)
        if audio_url:
            download = requests.get(str(audio_url), timeout=180)
            download.raise_for_status()
            output_path.write_bytes(download.content)
            return True

    content_type = response.headers.get("content-type", "").lower()
    if content_type.startswith("audio/") and response.content:
        output_path.write_bytes(response.content)
        return True

    return False


def transcribe_audio(audio_path: Path, config: PipelineConfig) -> List[Dict[str, Any]]:
    provider = config.transcription_provider
    if provider == "local":
        return transcribe_local(audio_path, config.whisper_model)

    if not config.openai_api_key:
        return transcribe_local(audio_path, config.whisper_model)

    client = OpenAI(api_key=config.openai_api_key, timeout=120)
    with audio_path.open("rb") as handle:
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=handle,
            response_format="verbose_json",
        )

    segments = getattr(transcript, "segments", None)
    if segments is None and isinstance(transcript, dict):
        segments = transcript.get("segments", [])
    segments = segments or []

    normalized: List[Dict[str, Any]] = []
    for segment in segments:
        text = normalize_text(segment.get("text", ""))
        if not text:
            continue
        normalized.append(
            {
                "start": float(segment.get("start", 0.0)),
                "end": float(segment.get("end", 0.0)),
                "text": text,
            }
        )
    return normalized


def transcribe_local(audio_path: Path, whisper_model: str) -> List[Dict[str, Any]]:
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:  # pragma: no cover - import path depends on VPS
        raise SystemExit(
            "Local transcription requires faster-whisper. Install pipeline/requirements.txt or set OPENAI_API_KEY."
        ) from exc

    model = WhisperModel(whisper_model, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(audio_path), vad_filter=True)
    normalized: List[Dict[str, Any]] = []
    for segment in segments:
        text = normalize_text(getattr(segment, "text", ""))
        if not text:
            continue
        normalized.append(
            {
                "start": float(getattr(segment, "start", 0.0)),
                "end": float(getattr(segment, "end", 0.0)),
                "text": text,
            }
        )
    return normalized


def write_srt(segments: Iterable[Dict[str, Any]], output_path: Path) -> None:
    blocks: List[str] = []
    for index, segment in enumerate(segments, start=1):
        start = seconds_to_timestamp(float(segment.get("start", 0.0)))
        end = seconds_to_timestamp(float(segment.get("end", 0.0)))
        text = wrap_caption(str(segment.get("text", "")))
        blocks.append(f"{index}\n{start} --> {end}\n{text}\n")
    output_path.write_text("\n".join(blocks), encoding="utf-8")


def render_background(source_path: Optional[Path], output_path: Path, duration_seconds: int) -> None:
    clip = build_background_clip(source_path, duration_seconds)
    try:
        clip.write_videofile(
            str(output_path),
            codec="libx264",
            audio=False,
            fps=30,
            preset="veryfast",
            threads=2,
            logger=None,
        )
    finally:
        clip.close()


def build_background_clip(source_path: Optional[Path], duration_seconds: int):
    if source_path is None:
        return ColorClip(size=TARGET_SIZE, color=(10, 14, 25), duration=duration_seconds)

    clip = VideoFileClip(str(source_path))
    if clip.duration <= 0:
        clip.close()
        raise SystemExit(f"Background clip {source_path} has no duration.")

    if clip.duration < duration_seconds:
        repeats = int(duration_seconds // clip.duration) + 1
        clip = concatenate_videoclips([clip.copy() for _ in range(repeats)])

    if clip.duration > duration_seconds:
        max_start = max(0.0, clip.duration - duration_seconds)
        start = random.uniform(0.0, max_start) if max_start > 0 else 0.0
        clip = clip.subclip(start, start + duration_seconds)

    clip = clip.resize(height=TARGET_SIZE[1])
    if clip.w < TARGET_SIZE[0]:
        clip = clip.resize(width=TARGET_SIZE[0])
    clip = clip.crop(x_center=clip.w / 2, y_center=clip.h / 2, width=TARGET_SIZE[0], height=TARGET_SIZE[1])
    return clip


def create_thumbnail(source_path: Optional[Path], thumbnail_text: str, output_path: Path) -> None:
    if source_path and source_path.exists():
        clip = VideoFileClip(str(source_path))
        try:
            frame = clip.get_frame(min(0.5, max(0.0, clip.duration / 4)))
        finally:
            clip.close()
        image = Image.fromarray(frame).convert("RGBA")
    else:
        image = Image.new("RGBA", (1280, 720), (10, 14, 25, 255))

    image = image.resize((1280, 720))
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 90))
    image = Image.alpha_composite(image, overlay)
    draw = ImageDraw.Draw(image)

    font = load_font(74)
    text = wrap_text_for_width(thumbnail_text.upper(), font, 1040)
    bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=12, align="center")
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    x = (1280 - width) / 2
    y = (720 - height) / 2

    draw.rounded_rectangle([(64, 64), (1216, 656)], radius=34, outline=(255, 255, 255, 100), width=4)
    draw.multiline_text((x + 6, y + 6), text, font=font, fill=(0, 0, 0, 220), spacing=12, align="center")
    draw.multiline_text((x, y), text, font=font, fill=(255, 255, 255, 255), spacing=12, align="center")
    image.convert("RGB").save(output_path, format="JPEG", quality=92, optimize=True)


def render_final_short(background_path: Path, audio_path: Path, subtitles_path: Path, output_path: Path, ffmpeg_binary: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg_binary,
        "-y",
        "-i",
        str(background_path),
        "-i",
        str(audio_path),
        "-vf",
        f"subtitles={escape_ffmpeg_path(subtitles_path)}:force_style='{subtitle_style()}'",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        raise SystemExit(
            "ffmpeg failed while rendering the final short.\n"
            f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )


def queue_upload(payload: Dict[str, Any], publish_endpoint: str) -> Dict[str, Any]:
    response = requests.post(publish_endpoint, json=payload, timeout=180)
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else {"job": data}


def retry_http_get(url: str) -> Dict[str, Any]:
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else {"jobs": data}


def wait_for_publish(job_id: str, queue_endpoint: str, timeout_minutes: int, poll_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_minutes * 60
    while time.monotonic() < deadline:
        payload = retry_http_get(queue_endpoint)
        jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
        for job in jobs:
            if job.get("id") != job_id:
                continue
            status = normalize_text(job.get("status", "")).lower()
            if status == "published":
                return True
            if status == "failed":
                raise SystemExit(f"The queued upload failed: {job.get('lastError') or 'unknown error'}")
        time.sleep(poll_seconds)
    raise SystemExit("Timed out while waiting for the queued upload to finish.")


def retry_call(label: str, func, attempts: int = 3, base_delay: int = 2, stop_on_false: bool = False):
    last_exc: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            result = func()
            if stop_on_false and not result:
                raise SystemExit(f"{label} did not complete successfully.")
            return result
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= attempts:
                raise
            sleep_for = base_delay * attempt + random.uniform(0, 1.5)
            print(f"[{label}] attempt {attempt} failed: {exc}", file=sys.stderr)
            time.sleep(sleep_for)
    if last_exc:
        raise last_exc
    raise SystemExit(f"{label} failed unexpectedly.")


def cleanup_paths(paths: Iterable[Optional[Path]]) -> None:
    for path in paths:
        if not path:
            continue
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


def normalize_tags(value: Any) -> List[str]:
    if isinstance(value, list):
        tags = [normalize_text(item) for item in value]
    elif isinstance(value, str):
        tags = [normalize_text(item) for item in re.split(r"[,\n]", value)]
    else:
        tags = []

    cleaned: List[str] = []
    for tag in tags:
        if tag and tag not in cleaned:
            cleaned.append(tag)
        if len(cleaned) >= 15:
            break
    return cleaned


def parse_json_object(text: str) -> Dict[str, Any]:
    candidates = [text.strip()]
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
    if fenced and fenced.group(1):
        candidates.append(fenced.group(1).strip())

    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1 and last > first:
        candidates.append(text[first : last + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue

    raise SystemExit(f"The LLM did not return valid JSON:\n{text}")


def strip_wrappers(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:text)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip().strip('"')


def strip_html(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", cleaned).strip()


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def sanitize_thumbnail_text(value: Any) -> str:
    cleaned = normalize_text(value)
    words = cleaned.split()
    return " ".join(words[:5])


def sanitize_title(value: str, limit: int) -> str:
    cleaned = normalize_text(value)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip("-:!?. ") + "…"


def ensure_short_tag(title: str, limit: int) -> str:
    suffix = " #Shorts"
    if len(title) + len(suffix) <= limit:
        return title + suffix
    trimmed = title[: max(0, limit - len(suffix) - 1)].rstrip("-:!?. ")
    return f"{trimmed}{suffix}"


def normalize_privacy_status(value: str) -> str:
    candidate = normalize_text(value).lower()
    return candidate if candidate in {"private", "public", "unlisted"} else "private"


def schedule_publish_time(delay_hours: int) -> Optional[str]:
    if delay_hours <= 0:
        return None
    publish_at = datetime.now(timezone.utc) + timedelta(hours=delay_hours)
    return publish_at.isoformat().replace("+00:00", "Z")


def wrap_caption(text: str) -> str:
    words = normalize_text(text).split()
    if not words:
        return ""
    lines: List[str] = []
    current: List[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        if current and len(candidate) > 38:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines)


def seconds_to_timestamp(seconds: float) -> str:
    millis = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(millis, 3600 * 1000)
    minutes, remainder = divmod(remainder, 60 * 1000)
    secs, ms = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def subtitle_style() -> str:
    return (
        "FontName=Arial,FontSize=54,PrimaryColour=&HFFFFFF&,OutlineColour=&H000000&," 
        "BackColour=&H000000&,BorderStyle=3,Outline=4,Shadow=1,Alignment=5,MarginV=160"
    )


def escape_ffmpeg_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def choose_background_clip(background_dir: Path) -> Optional[Path]:
    if not background_dir.exists():
        return None
    candidates = [p for p in background_dir.rglob("*") if p.is_file() and p.suffix.lower() in BACKGROUND_EXTS]
    if not candidates:
        return None
    return random.choice(candidates)


def load_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def wrap_text_for_width(text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    draw = ImageDraw.Draw(Image.new("RGB", (1280, 720)))
    words = normalize_text(text).split()
    if not words:
        return ""
    lines: List[str] = []
    current: List[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        box = draw.multiline_textbbox((0, 0), candidate, font=font, spacing=12, align="center")
        width = box[2] - box[0]
        if current and width > max_width:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines)


def generate_spoken_script(topic: str, config: PipelineConfig) -> str:
    client = OpenAI(api_key=config.openrouter_api_key, base_url=config.openrouter_base_url, timeout=120)
    response = client.chat.completions.create(
        model=config.openrouter_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You write punchy YouTube Shorts narration about crypto and market news. "
                    "Return only the spoken text. No title, no bullets, no markdown, and no stage directions. "
                    "Hook the viewer in the first 3 seconds and keep the response around 95 to 115 words."
                ),
            },
            {
                "role": "user",
                "content": f"Topic: {topic}\n\nWrite the spoken text for one 45-second YouTube Short.",
            },
        ],
        temperature=0.7,
        extra_headers={
            "HTTP-Referer": config.http_referer,
            "X-Title": config.openrouter_title,
        },
    )
    return strip_wrappers(response.choices[0].message.content or "")


def generate_packaging(topic: str, spoken_script: str, config: PipelineConfig) -> Dict[str, Any]:
    client = OpenAI(api_key=config.openrouter_api_key, base_url=config.openrouter_base_url, timeout=120)
    response = client.chat.completions.create(
        model=config.openrouter_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You package YouTube Shorts for clicks and search. Return strict JSON only with keys: "
                    "title, description, tags, thumbnail_text. title must be under 60 characters and include #Shorts. "
                    "description must be exactly two sentences with natural SEO keywords. tags must be an array of 8 to 15 strings. "
                    "thumbnail_text must be 2 to 5 words, bold, and punchy."
                ),
            },
            {
                "role": "user",
                "content": f"Topic: {topic}\n\nSpoken script:\n{spoken_script}\n\nGenerate the packaging.",
            },
        ],
        temperature=0.5,
        extra_headers={
            "HTTP-Referer": config.http_referer,
            "X-Title": config.openrouter_title,
        },
    )
    payload = parse_json_object(response.choices[0].message.content or "")
    return {
        "title": normalize_text(payload.get("title", "")),
        "description": normalize_text(payload.get("description", "")),
        "tags": normalize_tags(payload.get("tags", [])),
        "thumbnail_text": sanitize_thumbnail_text(payload.get("thumbnail_text", "")),
    }


def pull_coingecko_topic() -> str:
    response = requests.get(
        "https://api.coingecko.com/api/v3/coins/markets",
        params={
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 20,
            "page": 1,
            "sparkline": "false",
            "price_change_percentage": "24h",
        },
        timeout=30,
    )
    response.raise_for_status()
    coins = response.json()
    movers = [coin for coin in coins if coin.get("price_change_percentage_24h") is not None]
    if not movers:
        raise SystemExit("CoinGecko did not return a usable market list.")

    movers.sort(key=lambda coin: abs(float(coin.get("price_change_percentage_24h") or 0.0)), reverse=True)
    top = movers[:3]
    parts = []
    for coin in top:
        name = coin.get("name") or coin.get("symbol") or "Unknown"
        change = float(coin.get("price_change_percentage_24h") or 0.0)
        parts.append(f"{name} {change:+.1f}%")

    return "Today's biggest crypto movers: " + ", ".join(parts) + "."


def pull_rss_topic(feed_url: str) -> str:
    response = requests.get(feed_url, timeout=30)
    response.raise_for_status()
    root = ET.fromstring(response.text)

    for item in root.findall(".//item"):
        title = normalize_text(item.findtext("title", default=""))
        description = normalize_text(strip_html(item.findtext("description", default="")))
        if title:
            return f"News lead: {title}. {description[:160].strip()}"

    for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry"):
        title = normalize_text(entry.findtext("{http://www.w3.org/2005/Atom}title", default=""))
        summary = normalize_text(strip_html(entry.findtext("{http://www.w3.org/2005/Atom}summary", default="")))
        if title:
            return f"News lead: {title}. {summary[:160].strip()}"

    raise SystemExit("RSS feed did not return any items.")


def pick_topic(manual_topic: Optional[str], topic_source: str, config: PipelineConfig) -> str:
    if manual_topic and manual_topic.strip():
        return manual_topic.strip()

    if topic_source == "rss":
        return retry_call("RSS topic fetch", lambda: pull_rss_topic(config.rss_url))

    return retry_call("CoinGecko topic fetch", pull_coingecko_topic)


def resolve_ffmpeg_binary(configured: str) -> str:
    candidates = [configured, shutil.which("ffmpeg"), "/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/bin/ffmpeg"]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(Path(candidate).resolve())
    raise SystemExit("ffmpeg was not found. Set FFMPEG_BINARY in .env or install ffmpeg on the VPS.")


def ensure_directories() -> None:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)


def render_background(source_path: Optional[Path], output_path: Path, duration_seconds: int) -> None:
    clip = build_background_clip(source_path, duration_seconds)
    try:
        clip.write_videofile(
            str(output_path),
            codec="libx264",
            audio=False,
            fps=30,
            preset="veryfast",
            threads=2,
            logger=None,
        )
    finally:
        clip.close()


def build_background_clip(source_path: Optional[Path], duration_seconds: int):
    if source_path is None:
        return ColorClip(size=TARGET_SIZE, color=(10, 14, 25), duration=duration_seconds)

    clip = VideoFileClip(str(source_path))
    if clip.duration <= 0:
        clip.close()
        raise SystemExit(f"Background clip {source_path} has no duration.")

    if clip.duration < duration_seconds:
        repeats = int(duration_seconds // clip.duration) + 1
        clip = concatenate_videoclips([clip.copy() for _ in range(repeats)])

    if clip.duration > duration_seconds:
        max_start = max(0.0, clip.duration - duration_seconds)
        start = random.uniform(0.0, max_start) if max_start > 0 else 0.0
        clip = clip.subclip(start, start + duration_seconds)

    clip = clip.resize(height=TARGET_SIZE[1])
    if clip.w < TARGET_SIZE[0]:
        clip = clip.resize(width=TARGET_SIZE[0])
    clip = clip.crop(x_center=clip.w / 2, y_center=clip.h / 2, width=TARGET_SIZE[0], height=TARGET_SIZE[1])
    return clip


def create_thumbnail(source_path: Optional[Path], thumbnail_text: str, output_path: Path) -> None:
    if source_path and source_path.exists():
        clip = VideoFileClip(str(source_path))
        try:
            frame = clip.get_frame(min(0.5, max(0.0, clip.duration / 4)))
        finally:
            clip.close()
        image = Image.fromarray(frame).convert("RGBA")
    else:
        image = Image.new("RGBA", (1280, 720), (10, 14, 25, 255))

    image = image.resize((1280, 720))
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 90))
    image = Image.alpha_composite(image, overlay)
    draw = ImageDraw.Draw(image)

    font = load_font(74)
    text = wrap_text_for_width(thumbnail_text.upper(), font, 1040)
    bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=12, align="center")
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    x = (1280 - width) / 2
    y = (720 - height) / 2

    draw.rounded_rectangle([(64, 64), (1216, 656)], radius=34, outline=(255, 255, 255, 100), width=4)
    draw.multiline_text((x + 6, y + 6), text, font=font, fill=(0, 0, 0, 220), spacing=12, align="center")
    draw.multiline_text((x, y), text, font=font, fill=(255, 255, 255, 255), spacing=12, align="center")
    image.convert("RGB").save(output_path, format="JPEG", quality=92, optimize=True)


def render_final_short(background_path: Path, audio_path: Path, subtitles_path: Path, output_path: Path, ffmpeg_binary: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg_binary,
        "-y",
        "-i",
        str(background_path),
        "-i",
        str(audio_path),
        "-vf",
        f"subtitles={escape_ffmpeg_path(subtitles_path)}:force_style='{subtitle_style()}'",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        raise SystemExit(
            "ffmpeg failed while rendering the final short.\n"
            f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )


def relative_path(path: Optional[Path]) -> Optional[str]:
    if not path:
        return None
    try:
        return path.relative_to(ROOT_DIR).as_posix()
    except ValueError:
        return path.as_posix()


def queue_upload(payload: Dict[str, Any], publish_endpoint: str) -> Dict[str, Any]:
    response = requests.post(publish_endpoint, json=payload, timeout=180)
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else {"job": data}


def retry_http_get(url: str) -> Dict[str, Any]:
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else {"jobs": data}


def wait_for_publish(job_id: str, queue_endpoint: str, timeout_minutes: int, poll_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_minutes * 60
    while time.monotonic() < deadline:
        payload = retry_http_get(queue_endpoint)
        jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
        for job in jobs:
            if job.get("id") != job_id:
                continue
            status = normalize_text(job.get("status", "")).lower()
            if status == "published":
                return True
            if status == "failed":
                raise SystemExit(f"The queued upload failed: {job.get('lastError') or 'unknown error'}")
        time.sleep(poll_seconds)
    raise SystemExit("Timed out while waiting for the queued upload to finish.")


def retry_call(label: str, func, attempts: int = 3, base_delay: int = 2, stop_on_false: bool = False):
    last_exc: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            result = func()
            if stop_on_false and not result:
                raise SystemExit(f"{label} did not complete successfully.")
            return result
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= attempts:
                raise
            sleep_for = base_delay * attempt + random.uniform(0, 1.5)
            print(f"[{label}] attempt {attempt} failed: {exc}", file=sys.stderr)
            time.sleep(sleep_for)
    if last_exc:
        raise last_exc
    raise SystemExit(f"{label} failed unexpectedly.")


def cleanup_paths(paths: Iterable[Optional[Path]]) -> None:
    for path in paths:
        if not path:
            continue
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


def normalize_tags(value: Any) -> List[str]:
    if isinstance(value, list):
        tags = [normalize_text(item) for item in value]
    elif isinstance(value, str):
        tags = [normalize_text(item) for item in re.split(r"[,\n]", value)]
    else:
        tags = []

    cleaned: List[str] = []
    for tag in tags:
        if tag and tag not in cleaned:
            cleaned.append(tag)
        if len(cleaned) >= 15:
            break
    return cleaned


def parse_json_object(text: str) -> Dict[str, Any]:
    candidates = [text.strip()]
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
    if fenced and fenced.group(1):
        candidates.append(fenced.group(1).strip())

    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1 and last > first:
        candidates.append(text[first : last + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue

    raise SystemExit(f"The LLM did not return valid JSON:\n{text}")


def strip_wrappers(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:text)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip().strip('"')


def strip_html(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", cleaned).strip()


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def sanitize_thumbnail_text(value: Any) -> str:
    cleaned = normalize_text(value)
    words = cleaned.split()
    return " ".join(words[:5])


def sanitize_title(value: str, limit: int) -> str:
    cleaned = normalize_text(value)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip("-:!?. ") + "…"


def ensure_short_tag(title: str, limit: int) -> str:
    suffix = " #Shorts"
    if len(title) + len(suffix) <= limit:
        return title + suffix
    trimmed = title[: max(0, limit - len(suffix) - 1)].rstrip("-:!?. ")
    return f"{trimmed}{suffix}"


def normalize_privacy_status(value: str) -> str:
    candidate = normalize_text(value).lower()
    return candidate if candidate in {"private", "public", "unlisted"} else "private"


def schedule_publish_time(delay_hours: int) -> Optional[str]:
    if delay_hours <= 0:
        return None
    publish_at = datetime.now(timezone.utc) + timedelta(hours=delay_hours)
    return publish_at.isoformat().replace("+00:00", "Z")


def subtitle_style() -> str:
    return (
        "FontName=Arial,FontSize=54,PrimaryColour=&HFFFFFF&,OutlineColour=&H000000&,"
        "BackColour=&H000000&,BorderStyle=3,Outline=4,Shadow=1,Alignment=5,MarginV=160"
    )


def run_pipeline() -> int:
    parser = argparse.ArgumentParser(description="Cron-safe daily YouTube Short pipeline.")
    parser.add_argument("--topic", help="Override the topic signal with a manual topic.")
    parser.add_argument("--topic-source", choices=["coingecko", "rss"], help="Override the topic source.")
    parser.add_argument("--dry-run", action="store_true", help="Build the short but do not queue it.")
    parser.add_argument("--skip-publish", action="store_true", help="Alias for --dry-run.")
    parser.add_argument("--publish-delay-hours", type=int, help="Override the publishAt delay.")
    args = parser.parse_args()

    config = load_config()
    ffmpeg_binary = resolve_ffmpeg_binary(config.ffmpeg_binary)
    os.environ.setdefault("IMAGEIO_FFMPEG_EXE", ffmpeg_binary)
    os.environ.setdefault("FFMPEG_BINARY", ffmpeg_binary)

    ensure_directories()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    topic_source = args.topic_source or config.topic_source
    topic = pick_topic(args.topic, topic_source, config)
    publish_delay_hours = args.publish_delay_hours if args.publish_delay_hours is not None else config.publish_delay_hours
    dry_run = bool(args.dry_run or args.skip_publish)

    summary = retryable_publish_pipeline(topic, config, ffmpeg_binary, run_dir, publish_delay_hours, dry_run)
    summary.update({"runId": run_id, "dryRun": dry_run})
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


def retryable_publish_pipeline(topic: str, config: PipelineConfig, ffmpeg_binary: str, run_dir: Path, publish_delay_hours: int, dry_run: bool) -> Dict[str, Any]:
    script = retry_call("OpenRouter script generation", lambda: generate_spoken_script(topic, config))
    packaging = retry_call("OpenRouter packaging generation", lambda: generate_packaging(topic, script, config))

    title = sanitize_title(packaging.get("title", ""), 60)
    if "#shorts" not in title.lower():
        title = ensure_short_tag(title, 60)

    voiceover_path = run_dir / "voiceover.mp3"
    subtitles_path = run_dir / "subtitles.srt"
    background_path = run_dir / "background.mp4"
    thumbnail_path: Optional[Path] = None

    retry_call("Voicebox voiceover", lambda: synthesize_voiceover(script, voiceover_path, config))
    segments = retry_call("Whisper transcription", lambda: transcribe_audio(voiceover_path, config))
    write_srt(segments, subtitles_path)

    background_source = choose_background_clip(config.background_dir)
    render_background(background_source, background_path, TARGET_SECONDS)

    final_video_path = UPLOADS_DIR / f"final_short_{run_dir.name}.mp4"
    render_final_short(background_path, voiceover_path, subtitles_path, final_video_path, ffmpeg_binary)

    thumbnail_text = packaging.get("thumbnail_text", "")
    if thumbnail_text:
        thumbnail_path = UPLOADS_DIR / f"final_short_{run_dir.name}.jpg"
        create_thumbnail(background_source, thumbnail_text, thumbnail_path)

    publish_at = schedule_publish_time(publish_delay_hours)
    payload = {
        "videoPath": relative_path(final_video_path),
        "thumbnailPath": relative_path(thumbnail_path) if thumbnail_path else "",
        "title": title,
        "description": normalize_text(packaging.get("description", "")),
        "tags": normalize_tags(packaging.get("tags", [])),
        "privacyStatus": "private" if publish_at else config.default_privacy_status,
        "categoryId": config.default_category_id,
        "publishAt": publish_at,
    }

    queue_result: Dict[str, Any] = {}
    if not dry_run:
        queue_result = retry_call("Node publish queue", lambda: queue_upload(payload, config.publish_endpoint))
        cleanup_paths([voiceover_path, subtitles_path, background_path])

        job_id = ((queue_result or {}).get("job") or {}).get("id")
        if not job_id:
            raise SystemExit("The publish API did not return a job id, so cleanup cannot be confirmed safely.")

        retry_call(
            "YouTube publish confirmation",
            lambda: wait_for_publish(job_id, config.queue_endpoint, config.publish_timeout_minutes, config.poll_seconds),
            attempts=max(1, config.publish_timeout_minutes * 60 // max(1, config.poll_seconds)),
            base_delay=max(1, config.poll_seconds),
            stop_on_false=True,
        )
        cleanup_paths([final_video_path, thumbnail_path])
    else:
        cleanup_paths([voiceover_path, subtitles_path, background_path])

    return {
        "topic": topic,
        "title": title,
        "description": normalize_text(packaging.get("description", "")),
        "tags": normalize_tags(packaging.get("tags", [])),
        "thumbnailText": normalize_text(packaging.get("thumbnail_text", "")),
        "finalVideoPath": relative_path(final_video_path),
        "thumbnailPath": relative_path(thumbnail_path) if thumbnail_path else None,
        "publishAt": publish_at,
        "queued": bool(queue_result),
        "queueResult": queue_result,
    }


def main() -> int:
    return run_pipeline()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
