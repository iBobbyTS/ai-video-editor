from __future__ import annotations

import json
from pathlib import Path

from .models import AnalysisSegment, ScoredSegment
from .preview import render_preview, write_simple_fcpxml
from .timeline import build_timeline, write_review_markdown


def _segment_from_dict(data: dict) -> AnalysisSegment:
    allowed = set(AnalysisSegment.__dataclass_fields__.keys())
    return AnalysisSegment(**{key: value for key, value in data.items() if key in allowed})


def _scored_from_dict(data: dict) -> ScoredSegment:
    segment_data = data.get("segment", data)
    return ScoredSegment(
        segment=_segment_from_dict(segment_data),
        score=float(data.get("score", 0)),
        score_reasons=list(data.get("score_reasons", [])),
        excluded=bool(data.get("excluded", False)),
        exclusion_reason=str(data.get("exclusion_reason", "")),
    )


def rebuild_from_candidates(
    output_dir: Path,
    project_title: str,
    target_duration_sec: float = 90.0,
    render: bool = True,
    ffmpeg_bin: str = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
) -> None:
    candidate_path = output_dir / "candidate_events.json"
    data = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidates = [_scored_from_dict(item) for item in data.get("candidates", [])]
    timeline = build_timeline(candidates, project_title=project_title, target_duration_sec=target_duration_sec)
    (output_dir / "timeline.json").write_text(json.dumps(timeline, default=lambda value: value.__dict__, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_review_markdown(timeline, output_dir / "review.md")
    write_simple_fcpxml(timeline, output_dir / "event_memory.fcpxml")
    if render:
        ok = render_preview(timeline, output_dir / "review_preview.mp4", output_dir / "logs" / "preview_parts", ffmpeg_bin=ffmpeg_bin)
        (output_dir / "logs" / "render_preview.json").write_text(json.dumps({"messages": [f"Preview render {'completed' if ok else 'failed'}"]}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Rebuild event_memory timeline/FCPXML/preview from candidate_events.json")
    parser.add_argument("output_dir")
    parser.add_argument("--title", default="Event Memory Recap")
    parser.add_argument("--target-duration", type=float, default=90.0)
    parser.add_argument("--no-render", action="store_true")
    parser.add_argument("--ffmpeg-bin", default="/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
    args = parser.parse_args()
    rebuild_from_candidates(
        Path(args.output_dir),
        project_title=args.title,
        target_duration_sec=args.target_duration,
        render=not args.no_render,
        ffmpeg_bin=args.ffmpeg_bin,
    )
