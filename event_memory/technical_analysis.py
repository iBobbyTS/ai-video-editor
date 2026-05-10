from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from .frame_sampler import extract_segment_frames
from .models import AnalysisSegment


def estimate_motion_from_frames(frames: list[Path]) -> float:
    if len(frames) < 2:
        return 0.0
    values: list[float] = []
    previous = Image.open(frames[0]).convert("L").resize((160, 90))
    try:
        for frame in frames[1:]:
            current = Image.open(frame).convert("L").resize((160, 90))
            diff = ImageChops.difference(previous, current)
            stat = ImageStat.Stat(diff)
            values.append((stat.mean[0] or 0.0) / 255.0)
            previous.close()
            previous = current
    finally:
        previous.close()
    if not values:
        return 0.0
    return sum(values) / len(values)


def apply_technical_analysis(segment: AnalysisSegment, frames_dir: Path, ffmpeg_bin: str | None = None) -> AnalysisSegment:
    if segment.media_type != "video":
        segment.technical_signals["motion_score"] = 0.0
        return segment
    try:
        frames = extract_segment_frames(segment, frames_dir, ffmpeg_bin=ffmpeg_bin, max_frames=3, max_width=480, max_height=270)
        motion_score = estimate_motion_from_frames(frames)
    except Exception as exc:
        segment.technical_signals["technical_analysis_error"] = str(exc)
        return segment
    segment.technical_signals["motion_score"] = round(motion_score, 4)
    if segment.duration_sec >= 20 and motion_score < 0.01 and not segment.must_include:
        segment.labels.append("low_motion")
        if segment.event_role == "uncertain":
            segment.event_role = "dead_time"
        segment.recap_value = min(segment.recap_value, 0.2)
        segment.visual_quality = min(segment.visual_quality, 0.35)
        segment.notes.append("Low motion over a long segment; possible accidental or filler recording.")
    return segment
