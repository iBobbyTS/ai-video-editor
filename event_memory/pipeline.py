from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .env import load_dotenv
from .media_indexer import index_media
from .models import Timeline, to_jsonable
from .notes import load_human_notes, merge_notes_into_assets, merge_notes_into_segments
from .planner import create_timeline_planner
from .preview import render_preview, write_render_log, write_simple_fcpxml
from .scoring import score_segments, select_candidates
from .segmentation import create_analysis_segments
from .technical_analysis import apply_technical_analysis
from .timeline import build_timeline, write_review_markdown
from .vision_backend import create_backend


@dataclass
class EventMemoryOptions:
    input_dir: Path
    output_dir: Path
    dry_run: bool = True
    yes: bool = False
    project_title: str = "Event Memory Recap"
    target_duration_sec: float | None = 90.0
    still_duration_sec: float = 4.0
    window_sec: float = 25.0
    overlap_sec: float = 3.0
    sort_by: str = "filename"
    backend: str = "mock"
    timeline_planner: str = "heuristic"
    ffmpeg_bin: str = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
    mlx_model_path: str = "/Users/ibobby/.lmstudio/models/mlx-community/Qwen3.6-35B-A3B-mxfp4"
    lmstudio_base_url: str = "http://192.168.31.76:1234/v1"
    lmstudio_model: str = "qwen3.6-35b-a3b"
    lmstudio_api_key: str = "lm-studio"
    openai_base_url: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-5.5"
    max_qwen_segments: int | None = None
    render_preview_enabled: bool = False
    export_fcpxml: bool = True


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(to_jsonable(data), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_log(path: Path, messages: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(messages) + ("\n" if messages else ""), encoding="utf-8")


def _primary_analysis_ids(segments, budget: int | None) -> set[str]:
    if budget is None or budget >= len(segments):
        return {segment.segment_id for segment in segments}
    if budget <= 0:
        return {segment.segment_id for segment in segments if segment.must_include}
    ordered = sorted(segments, key=lambda segment: segment.sort_order)
    selected: set[str] = {segment.segment_id for segment in ordered if segment.must_include}
    for segment in ordered[: min(3, len(ordered))]:
        selected.add(segment.segment_id)
    for segment in ordered[-min(2, len(ordered)) :]:
        selected.add(segment.segment_id)
    remaining_slots = max(0, budget - len(selected))
    if remaining_slots:
        buckets = min(remaining_slots, len(ordered))
        for bucket in range(buckets):
            index = round((bucket + 0.5) * len(ordered) / buckets) - 1
            index = max(0, min(len(ordered) - 1, index))
            selected.add(ordered[index].segment_id)
            if len(selected) >= budget:
                break
    return selected


def run_event_memory_pipeline(options: EventMemoryOptions) -> Timeline:
    output_dir = Path(options.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = output_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = logs_dir / "frames"

    env_values = load_dotenv(Path.cwd() / ".env")
    openai_base_url = options.openai_base_url or env_values.get("OPENAI_BASE_URL", "")
    openai_api_key = options.openai_api_key or env_values.get("OPENAI_API_KEY", "")

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
    segments = [apply_technical_analysis(segment, frames_dir / "technical", ffmpeg_bin=options.ffmpeg_bin) for segment in segments]

    backend = create_backend(
        options.backend,
        dry_run=options.dry_run,
        ffmpeg_bin=options.ffmpeg_bin,
        frames_dir=frames_dir,
        mlx_model_path=options.mlx_model_path,
        lmstudio_base_url=options.lmstudio_base_url,
        lmstudio_model=options.lmstudio_model,
        lmstudio_api_key=options.lmstudio_api_key,
    )
    analyzed_segments = []
    fallback_backend = create_backend("mock", dry_run=True)
    qwen_budget = options.max_qwen_segments
    primary_ids = _primary_analysis_ids(segments, qwen_budget)
    analyzed_with_primary = 0
    for segment in segments:
        use_primary = segment.segment_id in primary_ids
        if use_primary:
            analyzed_segments.append(backend.analyze(segment))
            analyzed_with_primary += 1
        else:
            segment.notes.append("Primary vision analysis skipped by max_qwen_segments budget; using heuristic fallback.")
            analyzed_segments.append(fallback_backend.analyze(segment))
    _write_json(output_dir / "analysis_segments.json", {"mode": "event_memory", "segments": analyzed_segments})

    scored_segments = score_segments(analyzed_segments)
    _write_json(output_dir / "scored_segments.json", {"mode": "event_memory", "segments": scored_segments})

    candidates = select_candidates(scored_segments)
    _write_json(output_dir / "candidate_events.json", {"mode": "event_memory", "candidates": candidates})

    planned_candidates = candidates
    planner_used_model = False
    planner_notes: list[str] = []
    try:
        planner = create_timeline_planner(
            options.timeline_planner,
            openai_base_url=openai_base_url,
            openai_api_key=openai_api_key,
            model=options.openai_model,
            log_path=logs_dir / "gpt55_planner_response.txt",
        )
        plan = planner.plan(candidates, float(options.target_duration_sec or 90.0))
        planned_candidates = plan.candidates
        planner_used_model = plan.used_model
        planner_notes = plan.notes
        _write_json(output_dir / "timeline_plan.json", {"used_model": plan.used_model, "notes": planner_notes, "segment_ids": [item.segment.segment_id for item in planned_candidates]})
    except Exception as exc:
        message = f"Timeline planner failed; using heuristic order: {exc}"
        log_messages.append(message)
        _write_json(output_dir / "timeline_plan.json", {"used_model": False, "notes": [message], "segment_ids": [item.segment.segment_id for item in planned_candidates]})

    timeline = build_timeline(
        planned_candidates,
        project_title=options.project_title,
        target_duration_sec=options.target_duration_sec,
        preserve_order=planner_used_model,
    )
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
                ok = render_preview(timeline, output_dir / "review_preview.mp4", logs_dir / "preview_parts", ffmpeg_bin=options.ffmpeg_bin)
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
