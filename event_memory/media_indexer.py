from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

from .models import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, MediaAsset


def _iso_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _parse_fps(value: str | None) -> float | None:
    if not value:
        return None
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        try:
            denominator_float = float(denominator)
            if denominator_float == 0:
                return None
            return float(numerator) / denominator_float
        except ValueError:
            return None
    try:
        return float(value)
    except ValueError:
        return None


def probe_video(path: Path) -> dict[str, Any]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,avg_frame_rate,duration,codec_name:format=duration:format_tags=creation_time",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=15)
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams") or []
    if not streams:
        raise ValueError("no video stream found")
    stream = streams[0]
    format_data = data.get("format") or {}
    duration_value = stream.get("duration") or format_data.get("duration")
    created_at = (format_data.get("tags") or {}).get("creation_time")
    return {
        "duration_sec": float(duration_value) if duration_value else None,
        "width": int(stream["width"]) if stream.get("width") is not None else None,
        "height": int(stream["height"]) if stream.get("height") is not None else None,
        "fps": _parse_fps(stream.get("avg_frame_rate")) or _parse_fps(stream.get("r_frame_rate")),
        "codec": stream.get("codec_name"),
        "created_at": created_at,
    }


def probe_image(path: Path) -> dict[str, Any]:
    with Image.open(path) as image:
        width, height = image.size
    return {
        "duration_sec": None,
        "width": width,
        "height": height,
        "fps": None,
        "codec": None,
        "created_at": None,
    }


def _make_media_id(index: int, path: Path) -> str:
    return f"media_{index:04d}_{path.stem.lower().replace(' ', '_')}"


def index_media(input_dir: Path, sort_by: str = "filename") -> tuple[list[MediaAsset], list[str]]:
    input_dir = Path(input_dir)
    warnings: list[str] = []
    if not input_dir.exists() or not input_dir.is_dir():
        raise FileNotFoundError(f"Input folder does not exist: {input_dir}")

    supported_paths: list[Path] = []
    for path in sorted(input_dir.iterdir(), key=lambda p: p.name.lower()):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in VIDEO_EXTENSIONS or suffix in IMAGE_EXTENSIONS:
            supported_paths.append(path)
        elif path.name.lower() != "human_notes.csv":
            warnings.append(f"Skipped unsupported file: {path.name}")

    if sort_by == "created_at":
        supported_paths.sort(key=lambda p: (p.stat().st_mtime, p.name.lower()))
    else:
        supported_paths.sort(key=lambda p: p.name.lower())

    assets: list[MediaAsset] = []
    for index, path in enumerate(supported_paths):
        suffix = path.suffix.lower()
        media_type = "video" if suffix in VIDEO_EXTENSIONS else "image"
        asset_warnings: list[str] = []
        metadata: dict[str, Any]
        try:
            metadata = probe_video(path) if media_type == "video" else probe_image(path)
        except Exception as exc:
            metadata = {
                "duration_sec": None,
                "width": None,
                "height": None,
                "fps": None,
                "codec": None,
                "created_at": None,
            }
            asset_warnings.append(f"Metadata probe failed: {exc}")
            warnings.append(f"{path.name}: metadata probe failed ({exc})")

        created_at = metadata.get("created_at") or _iso_mtime(path)
        assets.append(
            MediaAsset(
                media_id=_make_media_id(index + 1, path),
                source_path=str(path.resolve()),
                media_type=media_type,
                duration_sec=metadata.get("duration_sec"),
                width=metadata.get("width"),
                height=metadata.get("height"),
                fps=metadata.get("fps"),
                codec=metadata.get("codec"),
                created_at=created_at,
                sort_order=index,
                warnings=asset_warnings,
            )
        )

    return assets, warnings
