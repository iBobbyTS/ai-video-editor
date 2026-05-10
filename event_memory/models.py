from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any


VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic"}

EVENT_ROLES = {
    "opening",
    "arrival",
    "location_context",
    "walking",
    "group_photo",
    "portrait_moment",
    "smiling_reaction",
    "main_activity",
    "animal_subject",
    "environment_broll",
    "venue_detail",
    "interaction",
    "transition",
    "closing",
    "dead_time",
    "uncertain",
}

IMAGE_MOTION_PRESETS = (
    "zoom_in",
    "zoom_out",
    "pan_left",
    "pan_right",
    "pan_up",
    "pan_down",
    "static",
)


@dataclass
class HumanNote:
    file: str
    start: float | None = None
    end: float | None = None
    human_note: str = ""
    importance: str = ""
    must_include: bool = False
    avoid_use: bool = False
    event_role: str = ""
    preferred_use: str = ""


@dataclass
class MediaAsset:
    media_id: str
    source_path: str
    media_type: str
    duration_sec: float | None
    width: int | None
    height: int | None
    fps: float | None
    codec: str | None
    created_at: str | None
    sort_order: int
    warnings: list[str] = field(default_factory=list)
    human_notes: list[HumanNote] = field(default_factory=list)


@dataclass
class AnalysisSegment:
    segment_id: str
    media_id: str
    source_path: str
    file_name: str
    media_type: str
    start_sec: float
    end_sec: float | None
    duration_sec: float
    sort_order: int
    event_role: str = "uncertain"
    labels: list[str] = field(default_factory=list)
    visual_quality: float = 0.5
    duplicate_group: str | None = None
    image_motion_preset: str | None = None
    notes: list[str] = field(default_factory=list)
    human_notes: list[HumanNote] = field(default_factory=list)
    must_include: bool = False
    avoid_use: bool = False
    preferred_use: str = ""


@dataclass
class ScoredSegment:
    segment: AnalysisSegment
    score: float
    score_reasons: list[str] = field(default_factory=list)
    excluded: bool = False
    exclusion_reason: str = ""


@dataclass
class TimelineClip:
    clip_id: str
    source_path: str
    media_type: str
    source_in: float | None
    source_out: float | None
    timeline_duration: float
    event_role: str
    score: float
    notes: list[str] = field(default_factory=list)
    image_motion_preset: str | None = None
    segment_id: str = ""


@dataclass
class Timeline:
    project_title: str
    mode: str
    target_duration_sec: float
    total_duration_sec: float
    clips: list[TimelineClip]


def to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {k: to_jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, Path):
        return str(value)
    return value
