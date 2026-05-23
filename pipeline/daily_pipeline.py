#!/usr/bin/env python3
"""Daily YouTube Shorts pipeline.

This runner pulls a topic signal, generates a 45-second script, synthesizes a
voiceover, transcribes it into subtitles, renders a vertical short with a random
background clip, generates packaging metadata, and hands the finished video off
to the existing YouTube upload queue in the Node app.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import textwrap
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

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
TARGET_DURATION_SECONDS = 45
TARGET_SIZE = (1080, 1920)
DEFAULT_LLM_MODEL = "openai/gpt-4o-mini"
DEFAULT_PUBLISH_ENDPOINT = "http://127.0.0.1:3456/api/youtube/publish"
DEFAULT_RSS_URL = "https://www.coindesk.com/arc/outboundfeeds/rss/"
DEFAULT_BACKGROUND_DIR = ROOT_DIR / "backgrounds"
RUNS_DIR = ROOT_DIR / "data" / "pipeline" / "runs"
UPLOADS_DIR = ROOT_DIR / "uploads"


@dataclass
class PipelineConfig:
    llm_api_key: str
    llm_model: str
    llm_base_url: str
    openrouter_title: str
    http_referer: str
    elevenlabs_api_key: str
    elevenlabs_voice_id: str
    elevenlabs_model_id: str
    topic_source: str
    rss_url: str
    background_dir: Path
    publish_endpoint: str
    publish_delay_hours: int
    default_privacy_status: str
    default_category_id: str
    transcription_provider: str
    openai_api_key: str
    whisper_model: str
    title_length_limit: int
    target_duration_seconds: int


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and queue a daily YouTube Short.")
    parser.add_argument("--topic", help="Override the topic pull with a manual topic.")
    parser.add_argument(
        "--topic-source",
        choices=["coingecko", "rss"],
        help="Override the configured topic source for this run.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Build the short but skip queueing it.")
    parser.add_argument(
        "--skip-publish",
        action="store_true",
        help="Alias for --dry-run.",
    )
    parser.add_argument(
        "--publish-delay-hours",
        type=int,
        help="Override the scheduled publish delay for this run.",
    )
    args = parser.parse_args()

    config = load_config()
    topic_source = (args.topic_source or config.topic_source).strip().lower()
    publish_delay_hours = args.publish_delay_hours if args.publish_delay_hours is not None else config.publish_delay_hours
    skip_publish = bool(args.dry_run or args.skip_publish)

    ensure_directories()

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    background_source = select_background_source(config.background_dir)
    topic_report = build_topic_report(args.topic, topic_source, config)
    spoken_script = generate_spoken_script(topic_report["topic"], config)
    metadata = generate_metadata(topic_report["topic"], spoken_script, config)

    title = sanitize_title(metadata["title"], config.title_length_limit)
    if "#shorts" not in title.lower():
        title = ensure_short_tag(title, config.title_length_limit)
    description = metadata["description"].strip()
    tags = normalize_tags(metadata.get("tags", []))
    thumbnail_text = metadata.get("thumbnail_text", "")

    voiceover_path = run_dir / "voiceover.mp3"
    synthesize_voiceover(spoken_script, voiceover_path, config)

    transcript_segments = transcribe_audio(voiceover_path, config)
    subtitles_path = run_dir / "subtitles.srt"
    write_srt(transcript_segments, subtitles_path)

    background_clip_path = run_dir / "background.mp4"
    render_vertical_background(background_source, background_clip_path, config.target_duration_seconds)

    final_video_path = UPLOADS_DIR / f"final_short_{run_id}.mp4"
    render_final_short(background_clip_path, voiceover_path, subtitles_path, final_video_path)

    thumbnail_path = None
    if thumbnail_text:
        thumbnail_path = UPLOADS_DIR / f"final_short_{run_id}.jpg"
        create_thumbnail(background_source, thumbnail_text, thumbnail_path)

    publish_at = iso_publish_time(publish_delay_hours)
    publish_payload = {
        "videoPath": relative_upload_path(final_video_path),
        "thumbnailPath": relative_upload_path(thumbnail_path) if thumbnail_path else "",
        "title": title,
        "description": description,
        "tags": tags,
        "privacyStatus": "private" if publish_at else config.default_privacy_status,
        "categoryId": config.default_category_id,
        "publishAt": publish_at,
    }

    queue_response: Dict[str, Any] = {}
    if not skip_publish:
        queue_response = queue_upload(publish_payload, config.publish_endpoint)

    run_summary = {
        "runId": run_id,
        "topicSource": topic_source,
        "topic": topic_report,
        "title": title,
        "description": description,
        "tags": tags,
        "thumbnailPath": relative_upload_path(thumbnail_path) if thumbnail_path else None,
        "finalVideoPath": relative_upload_path(final_video_path),
        "publishAt": publish_at,
        "queued": not skip_publish,
        "queueResponse": queue_response,
    }

    (run_dir / "summary.json").write_text(json.dumps(run_summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(run_summary, indent=2))
    return 0


def load_config() -> PipelineConfig:
    llm_api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not llm_api_key:
        raise SystemExit("OPENROUTER_API_KEY is required for the daily pipeline.")

    elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not elevenlabs_api_key:
        raise SystemExit("ELEVENLABS_API_KEY is required for voiceover generation.")

    elevenlabs_voice_id = os.getenv("ELEVENLABS_VOICE_ID", "").strip()
    if not elevenlabs_voice_id:
        raise SystemExit("ELEVENLABS_VOICE_ID is required for voiceover generation.")

    topic_source = os.getenv("PIPELINE_TOPIC_SOURCE", "coingecko").strip().lower() or "coingecko"
    if topic_source not in {"coingecko", "rss"}:
        topic_source = "coingecko"

    background_dir_value = os.getenv("PIPELINE_BACKGROUND_DIR", str(DEFAULT_BACKGROUND_DIR)).strip()
    background_dir = Path(background_dir_value)
    if not background_dir.is_absolute():
        background_dir = ROOT_DIR / background_dir

    return PipelineConfig(
        llm_api_key=llm_api_key,
        llm_model=os.getenv("PIPELINE_LLM_MODEL", os.getenv("OPENROUTER_MODEL", DEFAULT_LLM_MODEL)).strip() or DEFAULT_LLM_MODEL,
        llm_base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").strip(),
        openrouter_title=os.getenv("OPENROUTER_TITLE", "YouTube Automation Agent").strip() or "YouTube Automation Agent",
        http_referer=os.getenv("HTTP_REFERER", os.getenv("APP_URL", "http://localhost:3456")).strip(),
        elevenlabs_api_key=elevenlabs_api_key,
        elevenlabs_voice_id=elevenlabs_voice_id,
        elevenlabs_model_id=os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2").strip() or "eleven_multilingual_v2",
        topic_source=topic_source,
        rss_url=os.getenv("PIPELINE_RSS_URL", DEFAULT_RSS_URL).strip() or DEFAULT_RSS_URL,
        background_dir=background_dir,
        publish_endpoint=os.getenv("PIPELINE_PUBLISH_ENDPOINT", DEFAULT_PUBLISH_ENDPOINT).strip() or DEFAULT_PUBLISH_ENDPOINT,
        publish_delay_hours=max(0, int(os.getenv("PIPELINE_PUBLISH_DELAY_HOURS", "24"))),
        default_privacy_status=normalize_privacy_status(os.getenv("PIPELINE_PRIVACY_STATUS", "private")),
        default_category_id=os.getenv("PIPELINE_CATEGORY_ID", os.getenv("YOUTUBE_DEFAULT_CATEGORY_ID", "22")).strip() or "22",
        transcription_provider=os.getenv("PIPELINE_TRANSCRIPTION_PROVIDER", "openai").strip().lower() or "openai",
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        whisper_model=os.getenv("PIPELINE_WHISPER_MODEL", "base").strip() or "base",
        title_length_limit=60,
        target_duration_seconds=TARGET_DURATION_SECONDS,
    )


def ensure_directories() -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


def build_topic_report(manual_topic: Optional[str], topic_source: str, config: PipelineConfig) -> Dict[str, Any]:
    if manual_topic:
        return {
            "topic": manual_topic.strip(),
            "source": "manual",
        }

    if topic_source == "rss":
        topic = pull_rss_topic(config.rss_url)
        return {
            "topic": topic,
            "source": config.rss_url,
        }

    topic = pull_coingecko_topic()
    return {
        "topic": topic,
        "source": "coingecko",
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
    movers = sorted(
        [coin for coin in coins if coin.get("price_change_percentage_24h") is not None],
        key=lambda coin: abs(float(coin.get("price_change_percentage_24h") or 0.0)),
        reverse=True,
    )

    if not movers:
        raise SystemExit("CoinGecko returned no 24h movers.")

    headliners = movers[:3]
    lines: List[str] = []
    for coin in headliners:
        name = coin.get("name") or coin.get("symbol") or "Unknown"
        change = float(coin.get("price_change_percentage_24h") or 0.0)
        lines.append(f"{name} {format_percent(change)}")

    return "Today's biggest crypto movers: " + ", ".join(lines) + "."


def pull_rss_topic(feed_url: str) -> str:
    response = requests.get(feed_url, timeout=30)
    response.raise_for_status()
    root = ET.fromstring(response.text)
    items: List[Dict[str, str]] = []

    for item in root.findall(".//item")[:5]:
        title = (item.findtext("title") or "").strip()
        description = (item.findtext("description") or "").strip()
        if title:
            items.append({"title": title, "description": strip_html(description)})

    if not items:
        for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry")[:5]:
            title = (entry.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
            summary = (entry.findtext("{http://www.w3.org/2005/Atom}summary") or "").strip()
            if title:
                items.append({"title": title, "description": strip_html(summary)})

    if not items:
        raise SystemExit("RSS feed did not return any items.")

    lead = items[0]
    return f"News lead: {lead['title']}. {lead['description'][:160].strip()}"


def generate_spoken_script(topic: str, config: PipelineConfig) -> str:
    client = OpenAI(api_key=config.llm_api_key, base_url=config.llm_base_url)
    system_prompt = (
        "You write fast-paced YouTube Short narration about crypto and market news. "
        "Return only the spoken narration text. No title, no bullets, no labels, no markdown, and no stage directions. "
        "Make the hook land in the first 3 seconds. Keep it around 95 to 115 words so the final video fits a 45-second short."
    )
    user_prompt = (
        f"Topic: {topic}\n\n"
        "Write the spoken text for a single YouTube Short. Use a punchy, conversational tone and end with momentum."
    )

    response = client.chat.completions.create(
        model=config.llm_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.7,
        extra_headers={
            "HTTP-Referer": config.http_referer,
            "X-Title": config.openrouter_title,
        },
    )
    content = (response.choices[0].message.content or "").strip()
    return strip_wrappers(content)


def generate_metadata(topic: str, spoken_script: str, config: PipelineConfig) -> Dict[str, Any]:
    client = OpenAI(api_key=config.llm_api_key, base_url=config.llm_base_url)
    system_prompt = (
        "You package YouTube Shorts for clicks and search. Return strict JSON only. "
        "The JSON object must contain these keys: title, description, tags, thumbnail_text. "
        "title must be under 60 characters and include #Shorts. description must be exactly two sentences with natural SEO keywords. "
        "tags must be an array of 8 to 15 short strings. thumbnail_text must be 2 to 5 words, bold, and punchy."
    )
    user_prompt = (
        f"Topic: {topic}\n\n"
        f"Spoken script:\n{spoken_script}\n\n"
        "Generate the packaging for this short."
    )

    response = client.chat.completions.create(
        model=config.llm_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.5,
        extra_headers={
            "HTTP-Referer": config.http_referer,
            "X-Title": config.openrouter_title,
        },
    )
    content = (response.choices[0].message.content or "").strip()
    payload = parse_json_object(content)
    tags = normalize_tags(payload.get("tags", []))
    return {
        "title": str(payload.get("title", "")).strip(),
        "description": str(payload.get("description", "")).strip(),
        "tags": tags,
        "thumbnail_text": str(payload.get("thumbnail_text", "")).strip(),
    }


def synthesize_voiceover(script: str, output_path: Path, config: PipelineConfig) -> None:
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{config.elevenlabs_voice_id}"
    response = requests.post(
        url,
        headers={
            "xi-api-key": config.elevenlabs_api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        json={
            "text": script,
            "model_id": config.elevenlabs_model_id,
            "voice_settings": {
                "stability": 0.45,
                "similarity_boost": 0.8,
                "style": 0.25,
                "use_speaker_boost": True,
            },
        },
        stream=True,
        timeout=180,
    )
    response.raise_for_status()
    with output_path.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                handle.write(chunk)


def transcribe_audio(audio_path: Path, config: PipelineConfig) -> List[Dict[str, Any]]:
    provider = config.transcription_provider
    if provider == "openai":
        if not config.openai_api_key:
            raise SystemExit(
                "OPENAI_API_KEY is required when PIPELINE_TRANSCRIBE_PROVIDER=openai. "
                "Set it or switch to PIPELINE_TRANSCRIBE_PROVIDER=local."
            )
        client = OpenAI(api_key=config.openai_api_key)
        with audio_path.open("rb") as handle:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=handle,
                response_format="verbose_json",
            )
        segments = getattr(transcript, "segments", None) or transcript.get("segments", [])
        return [
            {
                "start": float(segment.get("start", 0.0)),
                "end": float(segment.get("end", 0.0)),
                "text": str(segment.get("text", "")).strip(),
            }
            for segment in segments
            if str(segment.get("text", "")).strip()
        ]

    try:
        from faster_whisper import WhisperModel
    except Exception as exc:  # pragma: no cover - import failure path
        raise SystemExit(
            "Local transcription requires the faster-whisper package. Install pipeline/requirements.txt or use OPENAI_API_KEY with PIPELINE_TRANSCRIBE_PROVIDER=openai."
        ) from exc

    model = WhisperModel(config.whisper_model, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(audio_path), vad_filter=True)
    normalized: List[Dict[str, Any]] = []
    for segment in segments:
        text = str(getattr(segment, "text", "")).strip()
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
        text = wrap_caption(str(segment.get("text", "")).strip())
        blocks.append(f"{index}\n{start} --> {end}\n{text}\n")
    output_path.write_text("\n".join(blocks), encoding="utf-8")


def render_vertical_background(source_path: Optional[Path], output_path: Path, duration_seconds: int) -> None:
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
        return ColorClip(size=TARGET_SIZE, color=(9, 14, 28), duration=duration_seconds)

    clip = VideoFileClip(str(source_path))
    try:
        if clip.duration <= 0:
            raise SystemExit(f"Background clip {source_path} has no duration.")
        if clip.duration < duration_seconds:
            repeats = int(duration_seconds // clip.duration) + 1
            clips = [clip]
            for _ in range(repeats - 1):
                clips.append(clip.copy())
            clip = concatenate_videoclips(clips)
        if clip.duration > duration_seconds:
            max_start = max(0.0, clip.duration - duration_seconds)
            start = random.uniform(0.0, max_start) if max_start > 0 else 0.0
            clip = clip.subclip(start, start + duration_seconds)

        clip = clip.resize(height=TARGET_SIZE[1])
        if clip.w < TARGET_SIZE[0]:
            clip = clip.resize(width=TARGET_SIZE[0])
        clip = clip.crop(x_center=clip.w / 2, y_center=clip.h / 2, width=TARGET_SIZE[0], height=TARGET_SIZE[1])
        return clip
    except Exception:
        clip.close()
        raise


def render_final_short(background_video: Path, audio_path: Path, subtitles_path: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    force_style = (
        "FontName=Arial,FontSize=54,PrimaryColour=&HFFFFFF&,OutlineColour=&H000000&,"
        "BackColour=&H000000&,BorderStyle=3,Outline=4,Shadow=1,Alignment=5,MarginV=160"
    )
    subtitle_filter = f"subtitles={escape_ffmpeg_path(subtitles_path)}:force_style='{force_style}'"
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(background_video),
        "-i",
        str(audio_path),
        "-vf",
        subtitle_filter,
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
            "ffmpeg failed while composing the final short.\n"
            f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )


def create_thumbnail(source_path: Optional[Path], thumbnail_text: str, output_path: Path) -> None:
    if source_path and source_path.exists():
        clip = VideoFileClip(str(source_path))
        try:
            frame = clip.get_frame(min(0.5, max(0.0, clip.duration / 4)))
        finally:
            clip.close()
        image = Image.fromarray(frame).convert("RGB")
    else:
        image = Image.new("RGB", (1280, 720), (10, 14, 25))

    image = image.resize((1280, 720))
    draw = ImageDraw.Draw(image)
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rectangle([(0, 0), (1280, 720)], fill=(0, 0, 0, 85))
    image = Image.alpha_composite(image.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(image)

    font = load_font(72)
    text = thumbnail_text.upper().strip()
    wrapped = wrap_text_for_width(text, font, 1040)
    bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=12, align="center")
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    x = (1280 - text_width) / 2
    y = (720 - text_height) / 2

    shadow_pos = (x + 6, y + 6)
    draw.multiline_text(shadow_pos, wrapped, font=font, fill=(0, 0, 0, 220), spacing=12, align="center")
    draw.multiline_text((x, y), wrapped, font=font, fill=(255, 255, 255, 255), spacing=12, align="center")
    draw.rounded_rectangle([(72, 72), (1208, 648)], radius=32, outline=(255, 255, 255, 90), width=4)
    image.convert("RGB").save(output_path, format="JPEG", quality=92, optimize=True)


def queue_upload(payload: Dict[str, Any], publish_endpoint: str) -> Dict[str, Any]:
    response = requests.post(publish_endpoint, json=payload, timeout=180)
    response.raise_for_status()
    return response.json()


def normalize_tags(value: Any) -> List[str]:
    if isinstance(value, list):
        tags = [str(item).strip() for item in value]
    elif isinstance(value, str):
        tags = re.split(r"[,\n]", value)
        tags = [tag.strip() for tag in tags]
    else:
        tags = []

    cleaned: List[str] = []
    for tag in tags:
        if not tag:
            continue
        if tag not in cleaned:
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
    stripped = text.strip()
    stripped = re.sub(r"^```(?:text)?\s*", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"\s*```$", "", stripped)
    stripped = stripped.strip().strip('"')
    return stripped


def strip_html(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def wrap_caption(text: str) -> str:
    words = text.split()
    if not words:
        return ""
    lines: List[str] = []
    current: List[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        if len(candidate) > 38 and current:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines)


def seconds_to_timestamp(value: float) -> str:
    total_milliseconds = max(0, int(round(value * 1000)))
    hours, remainder = divmod(total_milliseconds, 3600 * 1000)
    minutes, remainder = divmod(remainder, 60 * 1000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def format_percent(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.1f}%"


def iso_publish_time(delay_hours: int) -> Optional[str]:
    if delay_hours <= 0:
        return None
    publish_at = datetime.now(timezone.utc) + timedelta(hours=delay_hours)
    return publish_at.isoformat().replace("+00:00", "Z")


def relative_upload_path(path: Optional[Path]) -> Optional[str]:
    if path is None:
        return None
    try:
        return path.relative_to(ROOT_DIR).as_posix()
    except ValueError:
        return path.as_posix()


def select_background_source(background_dir: Path) -> Optional[Path]:
    if not background_dir.exists():
        return None
    candidates = [path for path in background_dir.rglob("*") if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS]
    if not candidates:
        return None
    return random.choice(candidates)


def escape_ffmpeg_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def load_font(size: int) -> ImageFont.FreeTypeFont:
    font_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]
    for font_path in font_candidates:
        if Path(font_path).exists():
            return ImageFont.truetype(font_path, size=size)
    return ImageFont.load_default()


def wrap_text_for_width(text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    draw = ImageDraw.Draw(Image.new("RGB", (1280, 720)))
    words = text.split()
    lines: List[str] = []
    current: List[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        bbox = draw.multiline_textbbox((0, 0), candidate, font=font, spacing=12, align="center")
        width = bbox[2] - bbox[0]
        if width > max_width and current:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines)


def sanitize_title(title: str, limit: int) -> str:
    cleaned = re.sub(r"\s+", " ", title).strip()
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1].rstrip("-:!?. ") + "…"
    return cleaned


def ensure_short_tag(title: str, limit: int) -> str:
    suffix = " #Shorts"
    candidate = title.strip()
    if len(candidate) + len(suffix) <= limit:
        return candidate + suffix
    trimmed = candidate[: max(0, limit - len(suffix) - 1)].rstrip("-:!?. ")
    return f"{trimmed}{suffix}"


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
