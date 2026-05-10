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

MIN_RECAP_CLIPS = 14
TARGET_RECAP_CLIPS = 18
MAX_RECAP_CLIPS = 22
ROLE_DURATIONS = {
    "opening": 5.0,
    "arrival": 5.0,
    "location_context": 5.0,
    "walking": 3.5,
    "group_photo": 6.0,
    "portrait_moment": 4.5,
    "smiling_reaction": 4.0,
    "main_activity": 5.0,
    "animal_subject": 5.0,
    "environment_broll": 4.0,
    "venue_detail": 3.5,
    "interaction": 4.5,
    "transition": 3.0,
    "closing": 6.0,
    "uncertain": 4.0,
}
ROLE_SELECTION_LIMITS = {
    "arrival": 4,
    "walking": 2,
    "group_photo": 3,
    "uncertain": 14,
}


def _target_duration(material_duration: float) -> float:
    if material_duration <= 0:
        return 90.0
    return min(120.0, max(90.0, material_duration))


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


def _shot_duration(item: ScoredSegment, remaining: float) -> float:
    segment = item.segment
    if segment.media_type == "image":
        return min(max(segment.duration_sec, 3.0), 5.0)
    base = ROLE_DURATIONS.get(segment.event_role, 4.0)
    if segment.duration_sec <= 6:
        duration = segment.duration_sec
    else:
        duration = min(base, segment.duration_sec)
    if remaining < duration and remaining >= 2.5:
        duration = remaining
    return round(max(1.0, duration), 3)


def _source_range(item: ScoredSegment, shot_duration: float, use_tail: bool = False) -> tuple[float | None, float | None]:
    segment = item.segment
    if segment.media_type != "video":
        return None, None
    start = float(segment.start_sec)
    end = float(segment.end_sec if segment.end_sec is not None else segment.start_sec + segment.duration_sec)
    if use_tail and end - start > shot_duration:
        start = end - shot_duration
    return round(start, 3), round(min(end, start + shot_duration), 3)


def _pick_by_roles(candidates: list[ScoredSegment], roles: set[str], limit: int, used: set[str]) -> list[ScoredSegment]:
    items = [item for item in candidates if item.segment.event_role in roles and item.segment.segment_id not in used]
    items.sort(key=lambda item: (-item.score, item.segment.sort_order))
    selected = items[:limit]
    used.update(item.segment.segment_id for item in selected)
    return selected


def _build_recap_order(candidates: list[ScoredSegment], preserve_order: bool = False) -> list[ScoredSegment]:
    usable = [item for item in candidates if not item.excluded]
    if preserve_order:
        return usable
    chronological = sorted(usable, key=lambda item: item.segment.sort_order)
    used: set[str] = set()
    selected: list[ScoredSegment] = []

    selected.extend(_pick_by_roles(chronological, {"opening", "arrival", "location_context"}, 3, used))
    selected.extend(_pick_by_roles(chronological, {"group_photo", "portrait_moment", "smiling_reaction", "interaction"}, 5, used))
    selected.extend(_pick_by_roles(chronological, {"main_activity", "animal_subject", "venue_detail", "environment_broll"}, 5, used))

    # Chronological coverage, including uncertain-but-not-dead-time clips, fills the story gaps.
    remaining = [item for item in chronological if item.segment.segment_id not in used]
    if remaining:
        buckets = min(10, len(remaining))
        for bucket in range(buckets):
            start = round(bucket * len(remaining) / buckets)
            end = round((bucket + 1) * len(remaining) / buckets)
            bucket_items = remaining[start:end]
            if not bucket_items:
                continue
            best = max(bucket_items, key=lambda item: (item.score, item.segment.technical_signals.get("motion_score") or 0))
            if best.segment.segment_id not in used:
                selected.append(best)
                used.add(best.segment.segment_id)

    selected.extend(_pick_by_roles(chronological, {"closing", "group_photo"}, 2, used))

    if len(selected) < TARGET_RECAP_CLIPS:
        leftovers = [item for item in chronological if item.segment.segment_id not in used]
        leftovers.sort(key=lambda item: (-item.score, item.segment.sort_order))
        for item in leftovers:
            selected.append(item)
            used.add(item.segment.segment_id)
            if len(selected) >= TARGET_RECAP_CLIPS:
                break

    role_counts: dict[str, int] = {}
    limited: list[ScoredSegment] = []
    for item in selected:
        role = item.segment.event_role
        limit = ROLE_SELECTION_LIMITS.get(role)
        if limit is not None and role_counts.get(role, 0) >= limit:
            continue
        limited.append(item)
        role_counts[role] = role_counts.get(role, 0) + 1

    if len(limited) < TARGET_RECAP_CLIPS:
        leftovers = [item for item in chronological if item.segment.segment_id not in {selected_item.segment.segment_id for selected_item in limited}]
        leftovers.sort(key=lambda item: (item.segment.sort_order, -item.score))
        for item in leftovers:
            role = item.segment.event_role
            limit = ROLE_SELECTION_LIMITS.get(role)
            if limit is not None and role_counts.get(role, 0) >= limit:
                continue
            limited.append(item)
            role_counts[role] = role_counts.get(role, 0) + 1
            if len(limited) >= TARGET_RECAP_CLIPS:
                break

    selected = limited[:MAX_RECAP_CLIPS]
    return sorted(selected, key=lambda item: (item.segment.sort_order, item.segment.start_sec))


def build_timeline(
    candidates: list[ScoredSegment],
    project_title: str = "Event Memory Recap",
    target_duration_sec: float | None = None,
    preserve_order: bool = False,
) -> Timeline:
    total_candidate_duration = sum(item.segment.duration_sec for item in candidates if not item.excluded)
    target = target_duration_sec or _target_duration(total_candidate_duration)
    ordered = _build_recap_order(candidates, preserve_order=preserve_order)

    clips: list[TimelineClip] = []
    current_duration = 0.0
    for item in ordered:
        segment = item.segment
        remaining = target - current_duration
        if remaining <= 0 and len(clips) >= MIN_RECAP_CLIPS:
            break
        duration = _shot_duration(item, remaining)
        source_in, source_out = _source_range(item, duration, use_tail=segment.event_role in {"closing", "group_photo"} and len(clips) >= TARGET_RECAP_CLIPS - 3)
        include = segment.must_include or current_duration < target or len(clips) < MIN_RECAP_CLIPS
        if not include:
            continue
        clips.append(
            TimelineClip(
                clip_id=f"clip_{len(clips) + 1:03d}",
                source_path=segment.source_path,
                media_type=segment.media_type,
                source_in=source_in,
                source_out=source_out,
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
