from __future__ import annotations

from collections import Counter

from .models import AnalysisSegment, ScoredSegment


ROLE_WEIGHTS = {
    "opening": 18,
    "arrival": 14,
    "location_context": 16,
    "walking": -10,
    "group_photo": 24,
    "portrait_moment": 18,
    "smiling_reaction": 22,
    "main_activity": 24,
    "animal_subject": 22,
    "environment_broll": 12,
    "venue_detail": 12,
    "interaction": 20,
    "transition": 2,
    "closing": 20,
    "dead_time": -100,
    "uncertain": 0,
}


def score_segment(segment: AnalysisSegment) -> ScoredSegment:
    score = 50.0
    reasons: list[str] = ["base 50"]
    role_weight = ROLE_WEIGHTS.get(segment.event_role, 0)
    if role_weight:
        score += role_weight
        reasons.append(f"role {segment.event_role} {role_weight:+.0f}")

    quality_delta = (segment.visual_quality - 0.5) * 30
    score += quality_delta
    reasons.append(f"visual quality {quality_delta:+.1f}")

    if segment.media_type == "image":
        score += 8
        reasons.append("memory photo +8")

    if segment.must_include:
        score += 100
        reasons.append("must_include +100")

    importances = {note.importance for note in segment.human_notes}
    if "high" in importances:
        score += 45
        reasons.append("human high +45")
    elif "medium" in importances:
        score += 18
        reasons.append("human medium +18")
    elif "low" in importances:
        score += 4
        reasons.append("human low +4")

    excluded = False
    exclusion_reason = ""
    if segment.avoid_use:
        excluded = True
        exclusion_reason = "avoid_use or do_not_use"
        score -= 500
        reasons.append("avoid_use -500")
    if "reject" in importances:
        excluded = True
        exclusion_reason = "reject importance"
        score -= 500
        reasons.append("reject -500")
    if segment.event_role == "dead_time" and not segment.must_include:
        excluded = True
        exclusion_reason = "dead_time"

    return ScoredSegment(segment=segment, score=round(score, 3), score_reasons=reasons, excluded=excluded, exclusion_reason=exclusion_reason)


def score_segments(segments: list[AnalysisSegment]) -> list[ScoredSegment]:
    scored = [score_segment(segment) for segment in segments]
    role_counts: Counter[str] = Counter()
    for item in scored:
        if item.excluded:
            continue
        role = item.segment.event_role
        if role == "walking" and role_counts[role] >= 1 and not item.segment.must_include:
            item.score -= 18
            item.score_reasons.append("repeated walking -18")
        if role != "uncertain":
            role_counts[role] += 1
    return scored


def select_candidates(scored_segments: list[ScoredSegment], max_candidates: int = 60) -> list[ScoredSegment]:
    included = [item for item in scored_segments if not item.excluded]
    must_include = [item for item in included if item.segment.must_include]
    regular = [item for item in included if not item.segment.must_include]
    regular.sort(key=lambda item: (-item.score, item.segment.sort_order))

    selected_by_id = {item.segment.segment_id: item for item in must_include}
    for item in regular:
        if len(selected_by_id) >= max_candidates:
            break
        selected_by_id.setdefault(item.segment.segment_id, item)

    return sorted(selected_by_id.values(), key=lambda item: item.segment.sort_order)
