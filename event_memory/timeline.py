from __future__ import annotations

from pathlib import Path

from .models import ScoredSegment, Timeline, TimelineClip


STRUCTURE_ORDER = [
    {"opening", "arrival", "location_context"},
    {"group_photo", "portrait_moment", "smiling_reaction", "interaction"},
    {"main_activity", "animal_subject", "venue_detail", "environment_broll"},
    {"transition", "walking", "uncertain"},
    {"closing"},
]

PREFERRED_ROLE_BY_USE = {
    "opening": "opening",
    "context": "location_context",
    "main_story": "main_activity",
    "montage": "transition",
    "transition": "transition",
    "closing": "closing",
}


def _target_duration(material_duration: float) -> float:
    if material_duration <= 0:
        return 60.0
    return min(120.0, max(60.0, material_duration))


def _ordered_candidates(candidates: list[ScoredSegment]) -> list[ScoredSegment]:
    selected: list[ScoredSegment] = []
    used: set[str] = set()
    for role_group in STRUCTURE_ORDER:
        group_items = [
            item
            for item in candidates
            if item.segment.segment_id not in used
            and (
                item.segment.event_role in role_group
                or PREFERRED_ROLE_BY_USE.get(item.segment.preferred_use) in role_group
            )
        ]
        group_items.sort(key=lambda item: item.segment.sort_order)
        for item in group_items:
            selected.append(item)
            used.add(item.segment.segment_id)

    remaining = [item for item in candidates if item.segment.segment_id not in used]
    remaining.sort(key=lambda item: item.segment.sort_order)
    selected.extend(remaining)
    return selected


def build_timeline(
    candidates: list[ScoredSegment],
    project_title: str = "Event Memory Recap",
    target_duration_sec: float | None = None,
) -> Timeline:
    total_candidate_duration = sum(item.segment.duration_sec for item in candidates if not item.excluded)
    target = target_duration_sec or _target_duration(total_candidate_duration)
    ordered = _ordered_candidates([item for item in candidates if not item.excluded])

    clips: list[TimelineClip] = []
    current_duration = 0.0
    for item in ordered:
        segment = item.segment
        duration = segment.duration_sec
        include = segment.must_include or current_duration < target or total_candidate_duration <= target
        if not include:
            continue
        clips.append(
            TimelineClip(
                clip_id=f"clip_{len(clips) + 1:03d}",
                source_path=segment.source_path,
                media_type=segment.media_type,
                source_in=segment.start_sec if segment.media_type == "video" else None,
                source_out=segment.end_sec if segment.media_type == "video" else None,
                timeline_duration=duration,
                event_role=segment.event_role,
                score=item.score,
                notes=segment.notes,
                image_motion_preset=segment.image_motion_preset,
                segment_id=segment.segment_id,
            )
        )
        current_duration += duration

    return Timeline(
        project_title=project_title,
        mode="event_memory",
        target_duration_sec=round(target, 3),
        total_duration_sec=round(sum(clip.timeline_duration for clip in clips), 3),
        clips=clips,
    )


def write_review_markdown(timeline: Timeline, output_path: Path) -> None:
    lines = [
        f"# {timeline.project_title}",
        "",
        f"- Mode: `{timeline.mode}`",
        f"- Target duration: {timeline.target_duration_sec:.1f}s",
        f"- Timeline duration: {timeline.total_duration_sec:.1f}s",
        f"- Clips: {len(timeline.clips)}",
        "",
        "## Clips",
        "",
        "| # | Type | Role | Duration | Score | Source | Notes |",
        "|---:|---|---|---:|---:|---|---|",
    ]
    for index, clip in enumerate(timeline.clips, 1):
        source_name = Path(clip.source_path).name
        notes = "; ".join(clip.notes).replace("|", "/")
        if clip.image_motion_preset:
            notes = f"{notes}; motion={clip.image_motion_preset}".strip("; ")
        lines.append(
            f"| {index} | {clip.media_type} | {clip.event_role} | {clip.timeline_duration:.1f}s | {clip.score:.1f} | {source_name} | {notes} |"
        )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
