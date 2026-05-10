from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .media_indexer import index_media
from .models import Timeline, to_jsonable
from .notes import load_human_notes, merge_notes_into_assets, merge_notes_into_segments
from .preview import render_preview, write_render_log, write_simple_fcpxml
from .scoring import score_segments, select_candidates
from .segmentation import create_analysis_segments
from .timeline import build_timeline, write_review_markdown
from .vision_backend import create_backend


@dataclass
class EventMemoryOptions:
    input_dir: Path
    output_dir: Path
    dry_run: bool = True
    yes: bool = False
    project_title: str = "Event Memory Recap"
    target_duration_sec: float | None = None
    still_duration_sec: float = 4.0
    window_sec: float = 25.0
    overlap_sec: float = 3.0
    sort_by: str = "filename"
    backend: str = "mock"
    render_preview_enabled: bool = False
    export_fcpxml: bool = True


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(to_jsonable(data), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_log(path: Path, messages: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(messages) + ("\n" if messages else ""), encoding="utf-8")


def run_event_memory_pipeline(options: EventMemoryOptions) -> Timeline:
    output_dir = Path(options.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = output_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    log_messages: list[str] = []
    assets, warnings = index_media(Path(options.input_dir), sort_by=options.sort_by)
    log_messages.extend(warnings)

    notes = load_human_notes(Path(options.input_dir))
    merge_notes_into_assets(assets, notes)
    _write_json(output_dir / "media_index.json", {"mode": "event_memory", "assets": assets, "warnings": warnings})

    segments = create_analysis_segments(
        assets,
        still_duration_sec=options.still_duration_sec,
        window_sec=options.window_sec,
        overlap_sec=options.overlap_sec,
    )
    merge_notes_into_segments(segments, notes)

    backend = create_backend(options.backend, dry_run=options.dry_run)
    analyzed_segments = [backend.analyze(segment) for segment in segments]
    _write_json(output_dir / "analysis_segments.json", {"mode": "event_memory", "segments": analyzed_segments})

    scored_segments = score_segments(analyzed_segments)
    _write_json(output_dir / "scored_segments.json", {"mode": "event_memory", "segments": scored_segments})

    candidates = select_candidates(scored_segments)
    _write_json(output_dir / "candidate_events.json", {"mode": "event_memory", "candidates": candidates})

    timeline = build_timeline(candidates, project_title=options.project_title, target_duration_sec=options.target_duration_sec)
    _write_json(output_dir / "timeline.json", timeline)
    write_review_markdown(timeline, output_dir / "review.md")

    if options.export_fcpxml:
        try:
            write_simple_fcpxml(timeline, output_dir / "event_memory.fcpxml")
        except Exception as exc:
            message = f"FCPXML export failed: {exc}"
            log_messages.append(message)

    if options.render_preview_enabled:
        render_messages: list[str] = []
        ok = False
        if timeline.clips:
            try:
                ok = render_preview(timeline, output_dir / "review_preview.mp4", logs_dir / "preview_parts")
            except Exception as exc:
                render_messages.append(f"Preview render failed: {exc}")
        else:
            render_messages.append("Preview render skipped: timeline has no clips")
        if ok:
            render_messages.append("Preview render completed: review_preview.mp4")
        else:
            render_messages.append("Preview render did not produce review_preview.mp4")
        write_render_log(logs_dir / "render_preview.json", render_messages)
        log_messages.extend(render_messages)

    _write_log(logs_dir / "event_memory.log", log_messages)
    return timeline
