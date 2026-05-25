#!/usr/bin/env python3
"""Experimental long-form (ViMax) entrypoint — SEPARATE from the Shorts cron.

This builds a long-form / kids-show episode via the ViMax adapter and, only when
explicitly told to, hands it off through the SAME proven upload queue the Shorts
pipeline uses. It is build-only by default and never scheduled by the Shorts
cron. Running it today is safe: the adapter is inert, so it reports what is
missing and exits 0 without rendering or uploading.

  python pipeline/longform_pipeline.py --theme "Bedtime with the Moon Cubs"
  LONGFORM_PUBLISH=1 python pipeline/longform_pipeline.py --theme "..." --publish

See docs/VIMAX_LONGFORM.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from video_modes import RenderedVideo, resolve_engine, resolve_mode  # noqa: E402
from vimax_adapter import EpisodeBrief, ViMaxNotConfigured  # noqa: E402

ROOT_DIR = Path(__file__).resolve().parents[1]
LONGFORM_RUNS = ROOT_DIR / "data" / "pipeline" / "longform_runs"

PUBLISH_ENDPOINT = os.getenv("PIPELINE_PUBLISH_ENDPOINT", "http://127.0.0.1:3456/api/youtube/publish")
QUEUE_ENDPOINT = os.getenv("PIPELINE_QUEUE_ENDPOINT", "http://127.0.0.1:3456/api/youtube/queue")


def _publish(video: RenderedVideo) -> dict:
    # Reuse the Shorts pipeline's proven, guarded upload helpers verbatim.
    from daily_pipeline_voicebox import queue_upload, wait_for_publish
    payload = video.to_publish_payload()
    result = queue_upload(payload, PUBLISH_ENDPOINT)
    job_id = ((result or {}).get("job") or {}).get("id")
    if not job_id:
        raise SystemExit("Publish API returned no job id; aborting before confirmation.")
    wait_for_publish(job_id, QUEUE_ENDPOINT, timeout_minutes=120, poll_seconds=20)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Experimental ViMax long-form builder (not the Shorts cron).")
    parser.add_argument("--theme", required=True, help="Episode theme / story prompt.")
    parser.add_argument("--mode", default=None, help="longform or kids_show (default: VIDEO_MODE or longform).")
    parser.add_argument("--target-seconds", type=int, default=360)
    parser.add_argument("--age-band", default="preschool")
    parser.add_argument("--publish", action="store_true", help="Hand off to the upload queue (also needs LONGFORM_PUBLISH=1).")
    args = parser.parse_args(argv)

    mode = resolve_mode(args.mode or os.getenv("VIDEO_MODE", "longform"))
    engine = resolve_engine(mode)

    # Inert-by-default safety: if ViMax isn't configured, report and exit cleanly.
    status = engine.status()
    if not status["ready"]:
        print("longform_pipeline: ViMax engine is inert — nothing built, nothing uploaded.")
        print(json.dumps(status, indent=2))
        print("Configure per docs/VIMAX_LONGFORM.md (VIMAX_ENABLED=1, VIMAX_CMD/VIMAX_HOME).")
        return 0

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    workdir = LONGFORM_RUNS / run_id
    brief = EpisodeBrief(
        theme=args.theme,
        target_seconds=args.target_seconds,
        age_band=args.age_band,
        made_for_kids=(mode == "kids_show" or args.age_band in {"preschool", "kids"}),
    )

    try:
        video = engine.build(brief, workdir)
    except ViMaxNotConfigured as exc:
        print(f"longform_pipeline: {exc}")
        return 0

    print(json.dumps({"runId": run_id, "mode": mode, "payload": video.to_publish_payload()}, indent=2))

    publish_opt_in = args.publish and os.getenv("LONGFORM_PUBLISH", "0").strip() == "1"
    if not publish_opt_in:
        print("Build-only: not publishing (need --publish AND LONGFORM_PUBLISH=1).")
        return 0

    result = _publish(video)
    print(json.dumps({"published": True, "queueResult": result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
