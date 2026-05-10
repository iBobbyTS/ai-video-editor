from __future__ import annotations

from pathlib import Path

from .models import IMAGE_MOTION_PRESETS, AnalysisSegment, MediaAsset


def assign_image_motion_preset(asset: MediaAsset, index: int) -> str:
    width = asset.width or 0
    height = asset.height or 0
    if width > height * 1.25:
        choices = ("pan_left", "pan_right", "zoom_in", "static")
    elif height > width * 1.25:
        choices = ("pan_up", "pan_down", "zoom_in", "static")
    else:
        choices = IMAGE_MOTION_PRESETS
    return choices[index % len(choices)]


def _make_segment_id(asset: MediaAsset, index: int) -> str:
    return f"{asset.media_id}_seg_{index:03d}"


def create_video_segments(
    asset: MediaAsset,
    short_threshold_sec: float = 12.0,
    long_threshold_sec: float = 45.0,
    window_sec: float = 25.0,
    overlap_sec: float = 3.0,
) -> list[AnalysisSegment]:
    duration = float(asset.duration_sec or 0)
    if duration <= 0:
        duration = short_threshold_sec

    ranges: list[tuple[float, float]]
    if duration <= short_threshold_sec:
        ranges = [(0.0, duration)]
    elif duration > long_threshold_sec:
        ranges = []
        start = 0.0
        step = max(1.0, window_sec - overlap_sec)
        while start < duration:
            end = min(duration, start + window_sec)
            ranges.append((start, end))
            if end >= duration:
                break
            start += step
    else:
        ranges = [(0.0, duration)]

    segments: list[AnalysisSegment] = []
    for index, (start, end) in enumerate(ranges, 1):
        segments.append(
            AnalysisSegment(
                segment_id=_make_segment_id(asset, index),
                media_id=asset.media_id,
                source_path=asset.source_path,
                file_name=Path(asset.source_path).name,
                media_type="video",
                start_sec=round(start, 3),
                end_sec=round(end, 3),
                duration_sec=round(max(0.1, end - start), 3),
                sort_order=asset.sort_order * 1000 + index,
            )
        )
    return segments


def create_image_segment(asset: MediaAsset, index: int, still_duration_sec: float = 4.0) -> AnalysisSegment:
    return AnalysisSegment(
        segment_id=_make_segment_id(asset, 1),
        media_id=asset.media_id,
        source_path=asset.source_path,
        file_name=Path(asset.source_path).name,
        media_type="image",
        start_sec=0.0,
        end_sec=None,
        duration_sec=still_duration_sec,
        sort_order=asset.sort_order * 1000 + 1,
        event_role="uncertain",
        image_motion_preset=assign_image_motion_preset(asset, index),
    )


def create_analysis_segments(
    assets: list[MediaAsset],
    still_duration_sec: float = 4.0,
    window_sec: float = 25.0,
    overlap_sec: float = 3.0,
) -> list[AnalysisSegment]:
    segments: list[AnalysisSegment] = []
    image_index = 0
    for asset in sorted(assets, key=lambda item: item.sort_order):
        if asset.media_type == "image":
            segments.append(create_image_segment(asset, image_index, still_duration_sec=still_duration_sec))
            image_index += 1
        elif asset.media_type == "video":
            segments.extend(create_video_segments(asset, window_sec=window_sec, overlap_sec=overlap_sec))
    return sorted(segments, key=lambda item: item.sort_order)
