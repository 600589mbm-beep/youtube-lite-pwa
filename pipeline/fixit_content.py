#!/usr/bin/env python3
"""Fix-It Jimmy (DIY / home-repair) content generator.

CHANNEL ISOLATION: This module is for the `fixit` profile / Fix-It Jimmy channel
ONLY. It contains NO crypto topics, NO crypto content pillars, NO crypto prompts,
and NO crypto visuals. It must never be imported by the crypto Shorts pipeline,
and the crypto pipeline must never import topics/prompts from here. See
docs/CHANNEL_MAP.md.

Importing this module has no side effects and pulls no heavy dependencies; the
optional LLM call is made lazily only when a key is configured and not disabled.
"""

from __future__ import annotations

import json
import os
import random
import re
from typing import Any, Dict, List, Optional

# DIY / home-repair Short topics. Practical, faceless, "Fix-It Jimmy" voice.
# These are deliberately home-repair only — never finance/crypto.
FIXIT_TOPICS: List[str] = [
    "Stop a running toilet in 30 seconds",
    "The 3 screws that fix a sagging cabinet door",
    "Unstick a squeaky door hinge with one household item",
    "Fix a dripping faucet without calling a plumber",
    "Patch a small drywall hole so it disappears",
    "Reset a tripped GFCI outlet that looks dead",
    "Fix a toilet that won't stop running with a 5-dollar part",
    "Silence a squeaky floorboard from above",
    "Re-caulk a tub line so it looks brand new",
    "Fix a drawer that keeps falling off its track",
    "Stop a door from slamming with one quiet trick",
    "Tighten a wobbly toilet without a new floor",
    "Fix a slow drain without harsh chemicals",
    "Replace a worn washer to kill a faucet drip",
    "Level a rocking table or chair in two minutes",
]

# Voice / style guardrail handed to the script model. Home-repair only.
FIXIT_STYLE = (
    "You write narration for FIX-IT JIMMY, a faceless DIY / home-repair Shorts "
    "channel. Friendly, confident handyman voice. Plain language, no jargon. "
    "Give a concrete, safe, step-by-step fix the viewer can do today with common "
    "tools. Absolutely NO finance, crypto, trading, or investing content."
)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def pick_fixit_topic(manual_topic: Optional[str] = None, seed: Optional[int] = None) -> str:
    """Pick a DIY topic. A manual topic wins; otherwise deterministic by seed."""
    if manual_topic and manual_topic.strip():
        return manual_topic.strip()
    rng = random.Random(seed if seed is not None else 0)
    return rng.choice(FIXIT_TOPICS)


def _local_script(topic: str) -> str:
    """Deterministic, offline DIY narration — used when no LLM is configured or
    when --offline is requested. Keeps dry-run fully self-contained."""
    return (
        f"Here's how to {topic[0].lower() + topic[1:]}. "
        "First, grab your basic tools and turn off any water or power to the area. "
        "Take a close look so you know exactly what's loose, worn, or leaking. "
        "Now make the fix step by step, snug but not over-tightened. "
        "Test it once before you pack up — you want it solid, not almost. "
        "That's it. A five minute fix that saves you a service call. "
        "Follow Fix-It Jimmy for one quick home repair every day."
    )


def generate_fixit_script(
    topic: str,
    config: Any = None,
    offline: bool = False,
) -> str:
    """Return ~40s of DIY narration.

    Uses OpenRouter ONLY when a key is present on `config` and offline is False;
    otherwise returns a deterministic local script so dry-run needs no network.
    The prompt is DIY-only (FIXIT_STYLE) — it never references crypto.
    """
    api_key = ""
    if config is not None and not offline:
        api_key = (getattr(config, "openrouter_api_key", "") or "").strip()
    if not api_key:
        return _local_script(topic)

    import requests  # lazy: dry-run/offline never needs the network

    base = (getattr(config, "openrouter_base_url", "") or "https://openrouter.ai/api/v1").rstrip("/")
    model = getattr(config, "openrouter_model", "") or "openai/gpt-4o-mini"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if getattr(config, "http_referer", ""):
        headers["HTTP-Referer"] = config.http_referer
    if getattr(config, "openrouter_title", ""):
        headers["X-Title"] = config.openrouter_title

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": FIXIT_STYLE},
            {
                "role": "user",
                "content": (
                    f"Topic: {topic}\n\nWrite the spoken narration for one ~40-second "
                    "home-repair Short. One short paragraph, no headings, no emojis, "
                    "end by inviting a follow for daily home fixes."
                ),
            },
        ],
        "temperature": 0.7,
    }
    resp = requests.post(f"{base}/chat/completions", headers=headers, json=body, timeout=120)
    resp.raise_for_status()
    text = (resp.json()["choices"][0]["message"]["content"] or "").strip()
    return text or _local_script(topic)


def build_fixit_packaging(topic: str, script: str) -> Dict[str, Any]:
    """Deterministic title/description/tags/thumbnail text for a DIY Short.

    No LLM required (so dry-run is self-contained) and no crypto wording.
    Guarantees a #Shorts tag in the title.
    """
    base_title = topic if len(topic) <= 52 else topic[:49].rstrip() + "..."
    title = f"{base_title} #Shorts"
    description = (
        f"{topic} — a quick Fix-It Jimmy home-repair tip you can do today.\n\n"
        "Practical DIY and home maintenance fixes, no fluff. "
        "Subscribe for one fast home repair every day.\n\n"
        "#DIY #HomeRepair #FixItJimmy #Handyman #HomeImprovement #Shorts"
    )
    tags = [
        "DIY", "home repair", "Fix-It Jimmy", "handyman", "home improvement",
        "how to fix", "home maintenance", "DIY shorts", slugify(topic).replace("-", " "),
    ]
    thumbnail_text = topic.upper()[:40]
    return {
        "title": title,
        "description": description,
        "tags": tags,
        "thumbnail_text": thumbnail_text,
    }


if __name__ == "__main__":
    # Quick self-test: emit a sample DIY packaging payload (no network).
    t = pick_fixit_topic(seed=1)
    s = generate_fixit_script(t, config=None, offline=True)
    print(json.dumps({"topic": t, "script": s, "packaging": build_fixit_packaging(t, s)}, indent=2))
