#!/usr/bin/env python3
"""Scaffold for analytics-driven topic/title optimization.

INERT BY DEFAULT. This module is NOT imported by the daily pipeline and is not
scheduled. It exists so the analytics loop has a contract to grow into once the
read-only YouTube scopes are granted. See docs/ANALYTICS.md.

Current credential scope is `youtube.upload` only, which cannot read analytics.
Running this module verifies the available scope and, if the analytics/readonly
scopes are missing, prints guidance and exits WITHOUT calling any API.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

ROOT_DIR = Path(__file__).resolve().parents[1]
AUTH_PATH = ROOT_DIR / "data" / "youtube-auth.json"
INSIGHTS_PATH = ROOT_DIR / "data" / "analytics" / "insights.json"

REQUIRED_SCOPES = (
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
)


def current_scopes() -> List[str]:
    if not AUTH_PATH.exists():
        return []
    try:
        data = json.loads(AUTH_PATH.read_text())
    except Exception:
        return []
    scope = data.get("scope") or (data.get("tokens") or {}).get("scope") or ""
    return [s for s in scope.split() if s]


def missing_scopes() -> List[str]:
    have = set(current_scopes())
    return [s for s in REQUIRED_SCOPES if s not in have]


def title_features(title: str) -> Dict[str, object]:
    """Tag a title with features to later regress against CTR / AVD."""
    words = title.split()
    return {
        "word_count": len(words),
        "has_question": "?" in title,
        "has_number": any(ch.isdigit() for ch in title),
        "has_emoji": any(ord(ch) > 0x2100 for ch in title),
        "has_shorts_tag": "#shorts" in title.lower(),
    }


def build_insights(metrics_by_video: Dict[str, Dict[str, float]],
                   summaries: List[Dict[str, object]]) -> Dict[str, object]:
    """Turn per-video metrics + run summaries into soft priors.

    Placeholder scoring: rank topics/title-features by CTR then average view %.
    Real implementation fills `metrics_by_video` from the YouTube Analytics API.
    """
    # Intentionally minimal until scopes exist; documents the output contract.
    return {
        "generated": True,
        "n_videos": len(metrics_by_video),
        "best_publish_hours": [],   # filled from publishAt buckets vs first-24h views
        "title_feature_lift": {},   # feature -> mean CTR lift
        "topic_lift": {},           # topic keyword -> mean AVD
    }


def main(argv: Optional[List[str]] = None) -> int:
    missing = missing_scopes()
    if missing:
        print("analytics_optimizer: inert — required read scopes are not granted.")
        print("  have:", current_scopes() or "(none)")
        print("  missing:", *missing, sep="\n    ")
        print("Re-auth additively (keep youtube.upload) to enable. See docs/ANALYTICS.md.")
        return 0

    # Scopes present: real pull would happen here (deferred — not yet required).
    INSIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    insights = build_insights(metrics_by_video={}, summaries=[])
    INSIGHTS_PATH.write_text(json.dumps(insights, indent=2) + "\n")
    print(f"analytics_optimizer: wrote {INSIGHTS_PATH.relative_to(ROOT_DIR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
