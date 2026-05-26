#!/usr/bin/env python3
"""Copyright-safe animated background + thumbnail generation for crypto Shorts.

Replaces the old flat near-black ``ColorClip`` fallback with several generated,
headless, download-free animated background styles, plus bold crypto-news card
generation (used for the YouTube thumbnail JPG and an in-video intro frame).

Styles (1080x1920, vertical):
- neon_candles   : neon candlestick grid on a faint chart grid
- market_heatmap : grid of green/red coin tiles pulsing like a market heatmap
- coin_vortex    : coin-glow particles orbiting a center vortex
- ticker_wall    : multiple scrolling exchange ticker rows
- liquid_gold    : flowing gold/blue liquid-finance gradient

Everything is numpy + Pillow, works on a GPU-less VPS with no network. The center
band stays calm and the subtitle renderer paints a black caption box, so captions
remain readable.
"""

from __future__ import annotations

import random
import re
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
from moviepy.editor import VideoClip, VideoFileClip, concatenate_videoclips
from PIL import Image, ImageDraw, ImageFont

# Brightness floor (mean luma 0-255). Anything below this is treated as "black".
NEAR_BLACK_LUMA = 14.0

_TICKER_SYMBOLS = [
    "BTC", "ETH", "SOL", "XRP", "BNB", "ADA", "DOGE", "AVAX", "LINK", "TON",
    "DOT", "MATIC", "UNI", "LTC", "ATOM", "XLM", "ETC", "FIL", "APT", "ARB",
    "OP", "INJ", "SUI", "SEI", "RNDR", "TIA", "NEAR", "HBAR", "ICP", "FTM",
    "GRT", "AAVE", "MKR", "SAND", "MANA", "AXS", "ALGO", "XTZ", "EOS", "ZEC",
]


# --------------------------------------------------------------------------- #
# shared drawing helpers
# --------------------------------------------------------------------------- #
def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _vgrad(top, bottom, width: int, height: int) -> np.ndarray:
    ramp = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None, None]
    g = np.array(top, np.float32)[None, None, :] * (1.0 - ramp) + np.array(bottom, np.float32)[None, None, :] * ramp
    return np.broadcast_to(g, (height, width, 3)).astype(np.float32).copy()


def _glow_kernel(radius: float) -> np.ndarray:
    size = max(3, int(radius * 2) + 1)
    coords = np.arange(size, dtype=np.float32)
    yy, xx = np.meshgrid(coords, coords, indexing="ij")
    center = (size - 1) / 2.0
    d2 = (xx - center) ** 2 + (yy - center) ** 2
    sigma = max(1.0, radius / 1.8)
    return np.exp(-d2 / (2.0 * sigma * sigma)).astype(np.float32)


def _add_glow(frame: np.ndarray, cx: float, cy: float, kernel: np.ndarray, color: np.ndarray) -> None:
    h, w = frame.shape[:2]
    r = kernel.shape[0] // 2
    x0, y0 = int(round(cx - r)), int(round(cy - r))
    x1, y1 = x0 + kernel.shape[1], y0 + kernel.shape[0]
    fx0, fy0, fx1, fy1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if fx1 <= fx0 or fy1 <= fy0:
        return
    k = kernel[fy0 - y0 : fy1 - y0, fx0 - x0 : fx1 - x0]
    frame[fy0:fy1, fx0:fx1, :] += k[..., None] * color


# --------------------------------------------------------------------------- #
# per-style frame builders: builder(W, H, rng) -> (make_frame(t)->uint8, accent)
# --------------------------------------------------------------------------- #
def _build_neon_candles(W, H, rng):
    base = _vgrad((16, 22, 44), (26, 16, 50), W, H)
    grid = np.array([16, 26, 40], np.float32)
    for gx in range(0, W, 90):
        base[:, gx : gx + 1, :] += grid
    for gy in range(0, H, 90):
        base[gy : gy + 1, :, :] += grid
    chart_top, chart_bot = int(H * 0.40), int(H * 0.74)
    ch = chart_bot - chart_top
    cw, gap = 30, 18
    step = cw + gap
    n = W // step + 4
    candles = []
    lvl = 0.5
    for _ in range(n):
        nl = min(0.92, max(0.08, lvl + rng.uniform(-0.16, 0.16)))
        candles.append((lvl, nl))
        lvl = nl
    speed = step * 0.5
    accent = np.array([0, 230, 180], np.float32)

    def mf(t):
        frame = base + 5.0 * np.sin(t * 0.6)
        off = (speed * t) % step
        for i, (o, c) in enumerate(candles):
            cx = int(i * step - off)
            if cx + cw < 0 or cx > W:
                continue
            oy = chart_top + (1.0 - o) * ch
            cy = chart_top + (1.0 - c) * ch
            col = np.array([0, 220, 150], np.float32) if c >= o else np.array([235, 70, 90], np.float32)
            ty, by = int(min(oy, cy)), max(int(max(oy, cy)), int(min(oy, cy)) + 4)
            x0, x1 = max(0, cx), min(W, cx + cw)
            if x1 > x0:
                frame[max(0, ty - 6) : min(H, by + 6), x0:x1, :] += col * 0.15
                frame[ty:by, x0:x1, :] += col * 0.9
                wx = min(W - 1, max(0, cx + cw // 2))
                frame[max(chart_top, ty - 30) : min(chart_bot, by + 30), wx : wx + 3, :] += col * 0.7
        np.clip(frame, 0, 255, out=frame)
        return frame.astype(np.uint8)

    return mf, accent


def _build_market_heatmap(W, H, rng):
    base = _vgrad((14, 18, 34), (18, 14, 40), W, H)
    cols, rows, pad = 5, 9, 10
    cellw, cellh = W // cols, H // rows
    tiles = []
    idx = 0
    for r in range(rows):
        for c in range(cols):
            tiles.append({"r": r, "c": c, "phase": rng.uniform(0, 6.28), "speed": rng.uniform(0.3, 0.9),
                          "sym": _TICKER_SYMBOLS[idx % len(_TICKER_SYMBOLS)]})
            idx += 1
    font, font2 = _load_font(30), _load_font(22)
    accent = np.array([60, 200, 120], np.float32)

    def mf(t):
        frame = base.copy()
        vals = {}
        for tile in tiles:
            v = float(np.sin(t * tile["speed"] + tile["phase"]))
            r, c = tile["r"], tile["c"]
            y0, y1 = r * cellh + pad, (r + 1) * cellh - pad
            x0, x1 = c * cellw + pad, (c + 1) * cellw - pad
            if v >= 0:
                col = np.array([20, 120, 70], np.float32) * v + np.array([24, 40, 46], np.float32)
            else:
                col = np.array([150, 45, 55], np.float32) * (-v) + np.array([44, 26, 32], np.float32)
            frame[y0:y1, x0:x1, :] = col
            vals[(r, c)] = v
        np.clip(frame, 0, 255, out=frame)
        img = Image.fromarray(frame.astype(np.uint8), "RGB")
        d = ImageDraw.Draw(img)
        for tile in tiles:
            r, c = tile["r"], tile["c"]
            x0, y0 = c * cellw + pad + 14, r * cellh + pad + 10
            d.text((x0, y0), tile["sym"], font=font, fill=(240, 245, 250))
            d.text((x0, y0 + 34), f"{vals[(r, c)] * 8:+.1f}%", font=font2, fill=(225, 235, 240))
        return np.asarray(img)

    return mf, accent


def _build_coin_vortex(W, H, rng):
    base = _vgrad((20, 16, 40), (30, 18, 52), W, H)
    cx0, cy0 = W / 2.0, H / 2.0
    palette = [(95, 72, 18), (20, 72, 82), (60, 30, 92), (90, 80, 30)]
    parts = []
    for _ in range(34):
        parts.append({"R": rng.uniform(80, 820), "ang": rng.uniform(0, 6.28),
                      "omega": rng.uniform(0.15, 0.5) * rng.choice([-1, 1]),
                      "kernel": _glow_kernel(rng.uniform(8, 26)),
                      "color": np.array(rng.choice(palette), np.float32),
                      "asp": rng.uniform(0.5, 0.95)})
    accent = np.array([240, 190, 60], np.float32)

    def mf(t):
        frame = base + 4.0 * np.sin(t * 0.5)
        for p in parts:
            a = p["ang"] + p["omega"] * t
            x = cx0 + p["R"] * np.cos(a)
            y = cy0 + p["R"] * p["asp"] * np.sin(a)
            _add_glow(frame, x, y, p["kernel"], p["color"])
        np.clip(frame, 0, 255, out=frame)
        return frame.astype(np.uint8)

    return mf, accent


def _build_ticker_wall(W, H, rng):
    base = _vgrad((12, 16, 32), (16, 14, 36), W, H)
    rows = 9
    rh = H // rows
    font = _load_font(34)
    bands = []
    for r in range(rows):
        items = "   ".join(f"{rng.choice(_TICKER_SYMBOLS)} {rng.choice('▲▼')}{rng.uniform(0.1, 9.9):.1f}%" for _ in range(8))
        bands.append({"y": r * rh, "text": items + "    " + items,
                      "speed": rng.uniform(35, 90) * rng.choice([-1, 1]),
                      "tint": (0, 150, 95) if r % 2 == 0 else (160, 55, 70)})
    accent = np.array([80, 160, 255], np.float32)

    def mf(t):
        img = Image.fromarray(base.astype(np.uint8), "RGB")
        d = ImageDraw.Draw(img, "RGBA")
        for b in bands:
            d.rectangle([0, b["y"] + 4, W, b["y"] + rh - 4], fill=(*b["tint"], 55))
            tw = d.textlength(b["text"], font=font)
            if b["speed"] >= 0:
                x = -((b["speed"] * t) % tw)
            else:
                x = -(tw - ((-b["speed"] * t) % tw))
            ty = b["y"] + rh * 0.28
            d.text((x, ty), b["text"], font=font, fill=(225, 235, 245, 235))
            d.text((x + tw, ty), b["text"], font=font, fill=(225, 235, 245, 235))
        return np.asarray(img.convert("RGB"))

    return mf, accent


def _build_liquid_gold(W, H, rng):
    yy, xx = np.mgrid[0:H, 0:W]
    xn = (xx / float(W)).astype(np.float32)
    yn = (yy / float(H)).astype(np.float32)
    gold = np.array([225, 165, 55], np.float32)
    blue = np.array([30, 70, 150], np.float32)
    p1, p2, p3 = rng.uniform(0, 6.28), rng.uniform(0, 6.28), rng.uniform(0, 6.28)
    accent = np.array([240, 200, 90], np.float32)

    def mf(t):
        field = (np.sin(xn * 6 + t * 0.7 + p1) + np.sin(yn * 5 - t * 0.5 + p2) + np.sin((xn + yn) * 4 + t * 0.4 + p3)) / 3.0
        m = ((field + 1.0) / 2.0)[..., None]
        frame = gold[None, None, :] * m + blue[None, None, :] * (1.0 - m)
        frame *= 0.7
        np.clip(frame, 8, 255, out=frame)
        return frame.astype(np.uint8)

    return mf, accent


_STYLE_BUILDERS = {
    "neon_candles": _build_neon_candles,
    "market_heatmap": _build_market_heatmap,
    "coin_vortex": _build_coin_vortex,
    "ticker_wall": _build_ticker_wall,
    "liquid_gold": _build_liquid_gold,
}
STYLES = tuple(_STYLE_BUILDERS.keys())

_STYLE_LABEL = {
    "neon_candles": "MARKET",
    "market_heatmap": "HEATMAP",
    "coin_vortex": "CRYPTO",
    "ticker_wall": "LIVE",
    "liquid_gold": "ALERT",
}

_STYLE_ACCENT = {
    "neon_candles": (0, 230, 180),
    "market_heatmap": (60, 200, 120),
    "coin_vortex": (240, 190, 60),
    "ticker_wall": (80, 160, 255),
    "liquid_gold": (240, 200, 90),
}

# Aggressive pacing: a fresh data widget (and a brightness "punch") every beat.
_BEAT_INTERVAL = 1.7
_WIDGET_TYPES = ("barbox", "coderow", "statcard", "heatmini")
_STAT_LABELS = ["LIQUIDATIONS", "24H VOL", "OPEN INT", "FUNDING", "LONGS", "SHORTS", "NET FLOW", "WHALE TX"]
_GREEN, _RED = (60, 210, 120), (225, 70, 85)


def _plan_beats(width: int, height: int, n_beats: int, seed: Optional[int]):
    """Deterministically pre-plan one data widget per beat. Anchors stay in the
    top band and lower corners so they never collide with the center captions."""
    rng = random.Random((seed or 0) ^ 0x9E3779B9)
    anchors = [
        (int(width * 0.05), int(height * 0.12)), (int(width * 0.52), int(height * 0.12)),
        (int(width * 0.05), int(height * 0.23)), (int(width * 0.52), int(height * 0.23)),
        (int(width * 0.06), int(height * 0.70)), (int(width * 0.52), int(height * 0.70)),
    ]
    beats = []
    for _ in range(max(1, n_beats)):
        beats.append({
            "type": rng.choice(_WIDGET_TYPES),
            "anchor": rng.choice(anchors),
            "vals": [rng.uniform(-1, 1) for _ in range(6)],
            "label": rng.choice(_STAT_LABELS),
            "sym": rng.choice(_TICKER_SYMBOLS),
            "amt": rng.uniform(0.2, 9.9),
            "up": rng.random() > 0.5,
        })
    return beats


def _draw_widget(draw, spec, alpha: float, accent, font_s, font_m) -> None:
    a = int(max(0.0, min(1.0, alpha)) * 255)
    ax, ay = spec["anchor"]
    acc = tuple(int(x) for x in accent)
    up = spec["up"]
    if spec["type"] == "barbox":
        w, h = 230, 120
        draw.rounded_rectangle([ax, ay, ax + w, ay + h], radius=10, fill=(10, 14, 26, int(a * 0.7)), outline=(*acc, a), width=2)
        bw = 24
        for i, v in enumerate(spec["vals"]):
            bh = int(abs(v) * (h - 30)) + 6
            x0 = ax + 14 + i * 35
            col = _GREEN if v >= 0 else _RED
            draw.rectangle([x0, ay + h - 12 - bh, x0 + bw, ay + h - 12], fill=(*col, a))
    elif spec["type"] == "coderow":
        rows = [f"EXEC {spec['sym']}/USDT", f"{'BUY' if up else 'SELL'} {spec['amt']:.2f}", f"px {spec['vals'][0] * 100 + 100:.2f}"]
        for i, ln in enumerate(rows):
            draw.text((ax, ay + i * 32), ln, font=font_s, fill=((120, 255, 160, a) if up else (255, 140, 150, a)))
    elif spec["type"] == "statcard":
        w, h = 250, 96
        draw.rounded_rectangle([ax, ay, ax + w, ay + h], radius=12, fill=(10, 14, 26, int(a * 0.72)), outline=(*acc, a), width=2)
        draw.text((ax + 14, ay + 10), spec["label"], font=font_s, fill=(210, 220, 235, a))
        col = _GREEN if up else _RED
        draw.text((ax + 14, ay + 42), f"{'+' if up else '-'}${spec['amt']:.1f}M", font=font_m, fill=(*col, a))
    else:  # heatmini
        cols, rows, cw = 5, 3, 38
        for r in range(rows):
            for c in range(cols):
                v = spec["vals"][(r * cols + c) % 6]
                col = _GREEN if v >= 0 else _RED
                x0, y0 = ax + c * cw, ay + r * cw
                draw.rectangle([x0, y0, x0 + cw - 4, y0 + cw - 4], fill=(col[0], col[1], col[2], int(a * (0.4 + 0.5 * abs(v)))))


def choose_style(seed: Optional[int] = None) -> str:
    return random.Random(seed).choice(STYLES)


def make_crypto_background(duration_seconds: int, size: Tuple[int, int], fps: int = 30,
                           seed: Optional[int] = None, style: Optional[str] = None) -> VideoClip:
    """Animated, copyright-safe crypto background. ``style`` is one of STYLES;
    if omitted/unknown it is chosen (seeded) for you."""
    width, height = size
    if style not in _STYLE_BUILDERS:
        style = choose_style(seed)
    rng = random.Random(seed)
    base_frame, accent = _STYLE_BUILDERS[style](width, height, rng)

    beats = _plan_beats(width, height, int(duration_seconds / _BEAT_INTERVAL) + 2, seed)
    font_s, font_m = _load_font(26), _load_font(40)

    def beat_frame(t):
        arr = base_frame(t)
        b = int(t / _BEAT_INTERVAL)
        local = t - b * _BEAT_INTERVAL
        # brightness "punch" at the start of each beat -> a felt motion beat
        if local < 0.12:
            arr = np.clip(arr.astype(np.float32) * (1.0 + 0.06 * (1.0 - local / 0.12)), 0, 255).astype(np.uint8)
        img = Image.fromarray(arr, "RGB").convert("RGBA")
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        if b < len(beats):
            _draw_widget(draw, beats[b], min(1.0, local / 0.25), accent, font_s, font_m)
        return np.asarray(Image.alpha_composite(img, overlay).convert("RGB"))

    return VideoClip(beat_frame, duration=duration_seconds).set_fps(fps)


def make_cta_card(output_path, style: Optional[str] = None, seed: Optional[int] = None,
                  size: Tuple[int, int] = (1080, 1920)) -> str:
    """Transparent overlay with a bottom 'Subscribe for daily crypto alpha' banner,
    shown for the last ~2.5s right before the loop restarts."""
    width, height = size
    style = style if style in _STYLE_BUILDERS else choose_style(seed)
    acc = _STYLE_ACCENT.get(style, (0, 230, 180))
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    by, bh, mx = int(height * 0.80), int(height * 0.10), int(width * 0.08)
    draw.rounded_rectangle([mx, by, width - mx, by + bh], radius=26, fill=(10, 12, 22, 235), outline=(*acc, 255), width=6)
    f1, f2 = _load_font(int(bh * 0.40)), _load_font(int(bh * 0.24))
    t1 = "▶  SUBSCRIBE"
    draw.text(((width - draw.textlength(t1, font=f1)) / 2, by + int(bh * 0.12)), t1, font=f1, fill=(*acc, 255))
    t2 = "DAILY CRYPTO ALPHA"
    draw.text(((width - draw.textlength(t2, font=f2)) / 2, by + int(bh * 0.60)), t2, font=f2, fill=(245, 248, 252, 255))
    img.save(str(output_path), format="PNG")
    return style


# --------------------------------------------------------------------------- #
# crypto-news cards (thumbnail JPG + in-video intro frame)
# --------------------------------------------------------------------------- #
def _style_backdrop(style: str, size: Tuple[int, int], seed: Optional[int] = None):
    width, height = size
    rng = random.Random(seed)
    builder = _STYLE_BUILDERS.get(style, _build_neon_candles)
    make_frame, accent = builder(width, height, rng)
    return np.asarray(make_frame(0.5)), accent


def _wrap_by_width(draw, text: str, font, max_width: int) -> str:
    lines, current = [], []
    for word in text.split():
        candidate = " ".join(current + [word])
        if current and draw.textlength(candidate, font=font) > max_width:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines)


def _compose_card(size: Tuple[int, int], headline: str, style: str, seed: Optional[int] = None) -> Image.Image:
    width, height = size
    if style not in _STYLE_BUILDERS:
        style = choose_style(seed)
    # Strip stray markdown the LLM sometimes leaves in thumbnail_text (**, #, _, `).
    headline = re.sub(r"[*_`#~]+", "", headline or "").strip() or "CRYPTO UPDATE"
    frame, accent = _style_backdrop(style, size, seed)
    acc = tuple(int(x) for x in accent)
    img = Image.fromarray(frame, "RGB").convert("RGBA")
    img = Image.alpha_composite(img, Image.new("RGBA", (width, height), (0, 0, 0, 130)))
    draw = ImageDraw.Draw(img)

    # badge pill (top-left)
    badge_font = _load_font(int(height * 0.030) + 10)
    label = _STYLE_LABEL.get(style, "CRYPTO")
    bx, by = int(width * 0.06), int(height * 0.10)
    bw = draw.textlength(label, font=badge_font)
    draw.rounded_rectangle([bx, by, bx + bw + 44, by + badge_font.size + 24], radius=14, fill=(*acc, 235))
    draw.text((bx + 22, by + 10), label, font=badge_font, fill=(10, 12, 20, 255))

    # headline
    hfont = _load_font(int(min(width, height) * 0.085))
    htext = _wrap_by_width(draw, (headline or "CRYPTO UPDATE").upper(), hfont, int(width * 0.86))
    bbox = draw.multiline_textbbox((0, 0), htext, font=hfont, spacing=14, align="left")
    th = bbox[3] - bbox[1]
    tx = int(width * 0.07)
    ty = int(height * 0.55 - th / 2) if height > width else int(height * 0.5 - th / 2)
    # accent underline bar above headline
    draw.rectangle([tx, ty - 26, tx + int(width * 0.42), ty - 12], fill=(*acc, 255))
    for dx, dy in ((6, 6), (3, 3)):
        draw.multiline_text((tx + dx, ty + dy), htext, font=hfont, fill=(0, 0, 0, 235), spacing=14, align="left")
    draw.multiline_text((tx, ty), htext, font=hfont, fill=(255, 255, 255, 255), spacing=14, align="left")

    # frame border
    draw.rounded_rectangle([int(width * 0.03), int(height * 0.03), int(width * 0.97), int(height * 0.97)],
                           radius=28, outline=(*acc, 160), width=6)
    return img.convert("RGB")


def make_thumbnail(source_path, headline: str, output_path, style: Optional[str] = None,
                   seed: Optional[int] = None) -> str:
    """Bold 1280x720 crypto-news thumbnail. ``source_path`` is accepted for
    backward compatibility but the card uses a generated style backdrop."""
    style = style if style in _STYLE_BUILDERS else choose_style(seed)
    card = _compose_card((1280, 720), headline, style, seed)
    card.save(str(output_path), format="JPEG", quality=90, optimize=True)
    return style


def make_intro_card(headline: str, output_path, style: Optional[str] = None,
                    seed: Optional[int] = None, size: Tuple[int, int] = (1080, 1920)) -> str:
    """Vertical 1080x1920 crypto-news card to overlay briefly inside the video so
    YouTube's auto-thumbnail picker has a strong frame to choose."""
    style = style if style in _STYLE_BUILDERS else choose_style(seed)
    card = _compose_card(size, headline, style, seed)
    card.save(str(output_path), format="PNG")
    return style


# --------------------------------------------------------------------------- #
# footage validation (unchanged behavior)
# --------------------------------------------------------------------------- #
def _clip_is_near_black(clip) -> bool:
    try:
        duration = float(clip.duration or 0)
    except Exception:
        return True
    if duration <= 0:
        return True
    for frac in (0.1, 0.5, 0.9):
        ts = duration * frac
        try:
            frame = clip.get_frame(min(ts, max(0.0, duration - 0.05)))
        except Exception:
            return True
        luma = float(np.mean(frame[..., 0]) * 0.299 + np.mean(frame[..., 1]) * 0.587 + np.mean(frame[..., 2]) * 0.114)
        if luma >= NEAR_BLACK_LUMA:
            return False
    return True


def load_footage_clip(source_path: Optional[Path], duration_seconds: int, size: Tuple[int, int]):
    """Load and fit stock footage to the target size, or return ``None`` when the
    footage is missing/unreadable/zero-duration/near-black."""
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
