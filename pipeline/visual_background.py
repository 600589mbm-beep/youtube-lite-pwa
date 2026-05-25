#!/usr/bin/env python3
"""Copyright-safe animated background generation for crypto Shorts.

The daily pipeline historically fell back to a flat near-black ``ColorClip``
whenever no stock footage existed in ``backgrounds/`` (which is the normal state
on the VPS). That produced black videos. This module replaces that fallback with
a fully generated, headless, download-free animated background:

- a slowly shifting dark gradient (never black: enforced brightness floor)
- floating "coin" glow particles drifting upward
- a scrolling candlestick chart in the lower third
- a subtle moving ticker band near the top

Everything is drawn with numpy + Pillow, so it works on a GPU-less VPS with no
network access and no third-party assets. The center band is kept calm and the
subtitle renderer already paints a black caption box, so captions stay readable.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
from moviepy.editor import VideoClip, VideoFileClip, concatenate_videoclips
from PIL import Image, ImageDraw, ImageFont

# Brightness floor (mean luma 0-255). Anything below this is treated as "black".
NEAR_BLACK_LUMA = 14.0

_TICKER_SYMBOLS = ["BTC", "ETH", "SOL", "XRP", "BNB", "ADA", "DOGE", "AVAX", "LINK", "TON"]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _glow_kernel(radius: float) -> np.ndarray:
    size = max(3, int(radius * 2) + 1)
    coords = np.arange(size, dtype=np.float32)
    yy, xx = np.meshgrid(coords, coords, indexing="ij")
    center = (size - 1) / 2.0
    d2 = (xx - center) ** 2 + (yy - center) ** 2
    sigma = max(1.0, radius / 1.8)
    return np.exp(-d2 / (2.0 * sigma * sigma)).astype(np.float32)


def _add_glow(frame: np.ndarray, cx: float, cy: float, kernel: np.ndarray, color: np.ndarray) -> None:
    """Additively composite a radial glow kernel onto ``frame`` (clipped at edges)."""
    h, w = frame.shape[:2]
    r = kernel.shape[0] // 2
    x0, y0 = int(round(cx - r)), int(round(cy - r))
    x1, y1 = x0 + kernel.shape[1], y0 + kernel.shape[0]
    fx0, fy0, fx1, fy1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if fx1 <= fx0 or fy1 <= fy0:
        return
    k = kernel[fy0 - y0 : fy1 - y0, fx0 - x0 : fx1 - x0]
    frame[fy0:fy1, fx0:fx1, :] += k[..., None] * color


def make_crypto_background(
    duration_seconds: int,
    size: Tuple[int, int],
    fps: int = 30,
    seed: Optional[int] = None,
) -> VideoClip:
    """Return a MoviePy clip with an animated, copyright-safe crypto background."""
    width, height = size
    rng = random.Random(seed)

    # --- static gradient base (never black) ---------------------------------
    # Deep navy at the top easing into a cool teal/purple at the bottom. The
    # minimum channel values keep mean luma well above the near-black floor.
    top = np.array([18, 24, 46], dtype=np.float32)
    bottom = np.array([28, 18, 52], dtype=np.float32)
    ramp = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None, None]
    base = top[None, None, :] * (1.0 - ramp) + bottom[None, None, :] * ramp
    base = np.broadcast_to(base, (height, width, 3)).astype(np.float32).copy()

    # --- floating coin particles --------------------------------------------
    n_particles = 26
    particles = []
    for _ in range(n_particles):
        radius = rng.uniform(8, 26)
        particles.append(
            {
                "x": rng.uniform(0, width),
                "y0": rng.uniform(0, height),
                "speed": rng.uniform(18, 70),      # px/sec upward
                "drift": rng.uniform(10, 34),      # horizontal sway amplitude
                "phase": rng.uniform(0, 2 * np.pi),
                "kernel": _glow_kernel(radius),
                "color": np.array(
                    rng.choice([
                        (90, 70, 18),   # gold-ish coin
                        (20, 70, 80),   # teal
                        (60, 30, 90),   # violet
                    ]),
                    dtype=np.float32,
                ),
            }
        )

    # --- scrolling candlestick chart (lower third) --------------------------
    chart_top = int(height * 0.62)
    chart_bottom = int(height * 0.86)
    chart_h = chart_bottom - chart_top
    candle_w = 26
    candle_gap = 16
    step = candle_w + candle_gap
    n_candles = width // step + 4
    candles = []
    level = 0.5
    for _ in range(n_candles):
        move = rng.uniform(-0.16, 0.16)
        new_level = min(0.92, max(0.08, level + move))
        candles.append({"open": level, "close": new_level, "wick": rng.uniform(0.04, 0.12)})
        level = new_level
    scroll_speed = step * 0.6  # px/sec leftward

    ticker_font = _load_font(34)
    ticker_text = "   ".join(f"{s} {rng.choice('▲▼')}{rng.uniform(0.1,7.9):.1f}%" for s in _TICKER_SYMBOLS)

    def make_frame(t: float) -> np.ndarray:
        # Gentle global brightness pulse (keeps it alive without flicker).
        pulse = 6.0 * np.sin(t * 0.7)
        frame = base + pulse

        # candlesticks
        offset = (scroll_speed * t) % step
        for i, candle in enumerate(candles):
            cx = int(i * step - offset)
            if cx + candle_w < 0 or cx > width:
                continue
            o = chart_top + (1.0 - candle["open"]) * chart_h
            c = chart_top + (1.0 - candle["close"]) * chart_h
            bull = candle["close"] >= candle["open"]
            color = np.array([40, 150, 95], np.float32) if bull else np.array([170, 60, 70], np.float32)
            top_y, bot_y = int(min(o, c)), int(max(o, c))
            bot_y = max(bot_y, top_y + 3)
            x0 = max(0, cx)
            x1 = min(width, cx + candle_w)
            if x1 > x0:
                frame[top_y:bot_y, x0:x1, :] += color * 0.85
                # wick
                wx = min(width - 1, max(0, cx + candle_w // 2))
                wick_top = max(chart_top, int(top_y - candle["wick"] * chart_h))
                wick_bot = min(chart_bottom, int(bot_y + candle["wick"] * chart_h))
                frame[wick_top:wick_bot, wx : wx + 3, :] += color * 0.6

        # floating particles
        for p in particles:
            y = (p["y0"] - p["speed"] * t) % (height + 80) - 40
            x = p["x"] + p["drift"] * np.sin(t * 0.5 + p["phase"])
            _add_glow(frame, x, y, p["kernel"], p["color"])

        np.clip(frame, 0, 255, out=frame)
        img = Image.fromarray(frame.astype(np.uint8), "RGB")

        # moving ticker band near the top
        draw = ImageDraw.Draw(img, "RGBA")
        band_y = int(height * 0.06)
        draw.rectangle([0, band_y, width, band_y + 52], fill=(0, 0, 0, 120))
        tw = draw.textlength(ticker_text, font=ticker_font)
        tx = -((45.0 * t) % (tw + width))
        draw.text((tx, band_y + 8), ticker_text, font=ticker_font, fill=(210, 220, 235, 230))
        draw.text((tx + tw + width, band_y + 8), ticker_text, font=ticker_font, fill=(210, 220, 235, 230))

        return np.asarray(img)

    return VideoClip(make_frame, duration=duration_seconds).set_fps(fps)


def _clip_is_near_black(clip) -> bool:
    """Sample a few frames; True if the clip is effectively black."""
    try:
        duration = float(clip.duration or 0)
    except Exception:
        return True
    if duration <= 0:
        return True
    sample_times = [duration * frac for frac in (0.1, 0.5, 0.9)]
    for ts in sample_times:
        try:
            frame = clip.get_frame(min(ts, max(0.0, duration - 0.05)))
        except Exception:
            return True
        # Rec. 601 luma
        luma = float(np.mean(frame[..., 0]) * 0.299 + np.mean(frame[..., 1]) * 0.587 + np.mean(frame[..., 2]) * 0.114)
        if luma >= NEAR_BLACK_LUMA:
            return False
    return True


def load_footage_clip(source_path: Optional[Path], duration_seconds: int, size: Tuple[int, int]):
    """Load and fit stock footage to the target size.

    Returns a ready-to-use clip, or ``None`` when the footage is missing,
    unreadable, zero-duration, or visually near-black — in which case the
    caller should generate an animated background instead.
    """
    if source_path is None:
        return None
    try:
        clip = VideoFileClip(str(source_path))
    except Exception:
        return None
    try:
        if not clip.duration or clip.duration <= 0:
            clip.close()
            return None

        if clip.duration < duration_seconds:
            repeats = int(duration_seconds // clip.duration) + 1
            clip = concatenate_videoclips([clip.copy() for _ in range(repeats)])

        if clip.duration > duration_seconds:
            max_start = max(0.0, clip.duration - duration_seconds)
            start = random.uniform(0.0, max_start) if max_start > 0 else 0.0
            clip = clip.subclip(start, start + duration_seconds)

        clip = clip.resize(height=size[1])
        if clip.w < size[0]:
            clip = clip.resize(width=size[0])
        clip = clip.crop(x_center=clip.w / 2, y_center=clip.h / 2, width=size[0], height=size[1])

        if _clip_is_near_black(clip):
            clip.close()
            return None
        return clip
    except Exception:
        try:
            clip.close()
        except Exception:
            pass
        return None
