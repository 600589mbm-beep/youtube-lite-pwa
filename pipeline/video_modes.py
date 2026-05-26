#!/usr/bin/env python3
"""Video-mode registry and the shared engine contract.

This is the seam that lets the project grow a second video mode (long-form /
kids-show via ViMax) WITHOUT touching the live Shorts pipeline. Importing this
module has no side effects and pulls no heavy dependencies — engines are
resolved lazily so an unconfigured ViMax never affects Shorts.

See docs/VIMAX_LONGFORM.md.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

SHORTS = "shorts"
LONGFORM = "longform"
KIDS_SHOW = "kids_show"
KNOWN_MODES = (SHORTS, LONGFORM, KIDS_SHOW)

# Modes handled by the ViMax adapter / longform entrypoint.
LONGFORM_MODES = (LONGFORM, KIDS_SHOW)


@dataclass
class RenderedVideo:
    """What every engine returns; consumed by the shared publisher.

    Mirrors the Node /api/youtube/publish payload so the long-form path reuses
    the exact same proven upload seam as Shorts.
    """

    video_path: str
    title: str
    description: str
    tags: List[str] = field(default_factory=list)
    thumbnail_path: Optional[str] = None
    duration_seconds: Optional[float] = None
    privacy_status: str = "private"
    category_id: str = "22"
    publish_at: Optional[str] = None
    made_for_kids: bool = False  # COPPA: set on upload for kids content
    profile: Optional[str] = None  # channel profile (e.g. "kids"); None -> Node default (crypto)

    def to_publish_payload(self) -> dict:
        payload = {
            "videoPath": self.video_path,
            "thumbnailPath": self.thumbnail_path or "",
            "title": self.title,
            "description": self.description,
            "tags": list(self.tags),
            "privacyStatus": self.privacy_status,
            "categoryId": self.category_id,
            "publishAt": self.publish_at,
        }
        # Node honors these for multi-channel + COPPA (youtube-publishing.js).
        if self.profile:
            payload["profile"] = self.profile
        if self.made_for_kids:
            payload["madeForKids"] = True
        return payload


def resolve_mode(name: Optional[str] = None) -> str:
    mode = (name or os.getenv("VIDEO_MODE", SHORTS)).strip().lower() or SHORTS
    if mode not in KNOWN_MODES:
        raise ValueError(f"Unknown VIDEO_MODE {mode!r}; known: {', '.join(KNOWN_MODES)}")
    return mode


def resolve_engine(mode: str):
    """Lazily return the engine for a mode.

    Shorts intentionally has NO engine here — it stays on its own untouched
    entrypoint (daily_pipeline_safe.py). Only long-form modes are routed through
    the ViMax adapter so the live path is never imported/altered by accident.
    """
    if mode == SHORTS:
        raise NotImplementedError(
            "Shorts runs on daily_pipeline_safe.py and is intentionally not "
            "routed through this registry. Use the existing entrypoint."
        )
    if mode in LONGFORM_MODES:
        from vimax_adapter import ViMaxEngine  # lazy: keeps Shorts import-clean
        return ViMaxEngine(mode=mode)
    raise ValueError(f"No engine registered for mode {mode!r}.")
