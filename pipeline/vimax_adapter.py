#!/usr/bin/env python3
"""ViMax-backed long-form video engine (scaffold, inert by default).

This adapter is the ONLY place the project talks to ViMax. It maps the ViMax
story pipeline (idea -> script -> storyboard -> scene planning -> asset
generation -> consistency -> assembly) to a single `RenderedVideo` that the
shared publisher can upload through the existing queue.

INERT BY DEFAULT. Until `VIMAX_ENABLED=1` and `VIMAX_CMD` (or VIMAX_HOME) are
configured, `build()` raises a clear, non-destructive error instead of doing
anything. Importing this module is side-effect-free and is never imported by the
Shorts pipeline. See docs/VIMAX_LONGFORM.md.

The exact ViMax runtime contract must be verified against the ViMax release you
pin — it is isolated to `_invoke_vimax()` so swapping it never ripples outward.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from video_modes import RenderedVideo


@dataclass
class EpisodeBrief:
    """Input to the long-form engine — a story request, not a topic ticker."""

    theme: str
    target_seconds: int = 360            # long-form default (6 min), vs 45 for Shorts
    age_band: str = "preschool"          # kids-show default
    characters: List[str] = field(default_factory=list)
    learning_goal: str = ""
    made_for_kids: bool = True


class ViMaxNotConfigured(RuntimeError):
    """Raised when ViMax work is requested but the runtime is not configured."""


class ViMaxEngine:
    """Long-form engine. Stage methods document the ViMax mapping; the heavy work
    is deferred to `_invoke_vimax`, which stays inert until configured."""

    def __init__(self, mode: str = "longform") -> None:
        self.mode = mode
        self.enabled = os.getenv("VIMAX_ENABLED", "0").strip() == "1"
        self.vimax_cmd = os.getenv("VIMAX_CMD", "").strip()
        self.vimax_home = os.getenv("VIMAX_HOME", "").strip()

    # --- readiness ----------------------------------------------------------
    def status(self) -> dict:
        return {
            "mode": self.mode,
            "enabled": self.enabled,
            "vimax_cmd": self.vimax_cmd or None,
            "vimax_home": self.vimax_home or None,
            "ready": self.is_ready(),
            "missing": self.missing(),
        }

    def is_ready(self) -> bool:
        return not self.missing()

    def missing(self) -> List[str]:
        gaps: List[str] = []
        if not self.enabled:
            gaps.append("VIMAX_ENABLED=1")
        if not (self.vimax_cmd or self.vimax_home):
            gaps.append("VIMAX_CMD or VIMAX_HOME")
        elif self.vimax_cmd and not shutil.which(self.vimax_cmd) and not Path(self.vimax_cmd).exists():
            gaps.append(f"VIMAX_CMD not found: {self.vimax_cmd}")
        return gaps

    # --- public API ---------------------------------------------------------
    def build(self, brief: EpisodeBrief, workdir: Path) -> RenderedVideo:
        """Run the full long-form build and return a publishable video.

        Deferred to ViMax via `_invoke_vimax`. Raises ViMaxNotConfigured until
        the runtime is wired up, so this can never silently misbehave.
        """
        if not self.is_ready():
            raise ViMaxNotConfigured(
                "ViMax long-form engine is inert. Missing: "
                + ", ".join(self.missing())
                + ". See docs/VIMAX_LONGFORM.md."
            )
        workdir.mkdir(parents=True, exist_ok=True)
        # Conceptual ViMax stages (the runtime performs these; documented here so
        # the mapping is explicit and reviewable):
        #   1. script        : brief -> multi-beat narrative script
        #   2. storyboard     : script -> shot list / framing / transitions
        #   3. scene_plan     : storyboard -> per-scene setting/characters/camera/duration
        #   4. assets         : scene_plan -> per-scene visuals (T2I/T2V)
        #   5. consistency    : enforce character/style across scenes (refs/seeds)
        #   6. assembly       : timeline -> single MP4 (ffmpeg/moviepy concat)
        result = self._invoke_vimax(brief, workdir)
        return self._to_rendered_video(brief, result)

    # --- single isolated integration point ----------------------------------
    def _invoke_vimax(self, brief: EpisodeBrief, workdir: Path) -> dict:
        """Invoke the actual ViMax runtime. TO BE IMPLEMENTED against the pinned
        ViMax release (CLI subprocess or local service). Must return at least
        {"video_path": <mp4>, optionally "thumbnail_path", "scene_count", ...}.

        Kept as the sole boundary so the rest of the project is ViMax-version
        agnostic. Voicebox/FFmpeg can be delegated to here for VO + final encode.
        """
        raise NotImplementedError(
            "Wire _invoke_vimax to the pinned ViMax runtime (VIMAX_CMD/VIMAX_HOME). "
            "Return {'video_path': <mp4>, ...}. See docs/VIMAX_LONGFORM.md."
        )

    def _to_rendered_video(self, brief: EpisodeBrief, result: dict) -> RenderedVideo:
        return RenderedVideo(
            video_path=result["video_path"],
            thumbnail_path=result.get("thumbnail_path"),
            title=result.get("title", brief.theme),
            description=result.get("description", ""),
            tags=result.get("tags", []),
            duration_seconds=result.get("duration_seconds", float(brief.target_seconds)),
            privacy_status=result.get("privacy_status", "private"),
            category_id=result.get("category_id", "22"),
            made_for_kids=brief.made_for_kids,
        )


if __name__ == "__main__":
    import json
    print(json.dumps(ViMaxEngine(os.getenv("VIDEO_MODE", "longform")).status(), indent=2))
