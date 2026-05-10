from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image

from .models import AnalysisSegment


DEFAULT_FFMPEG = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"


def resolve_ffmpeg(ffmpeg_bin: str | None = None) -> str:
    candidate = ffmpeg_bin or DEFAULT_FFMPEG
    if Path(candidate).exists():
        return candidate
    return "ffmpeg"


def sample_timestamps(segment: AnalysisSegment, max_frames: int = 5) -> list[float]:
    duration = max(segment.duration_sec, 0.1)
    if segment.media_type == "image":
        return [0.0]
    if duration <= 4:
        fractions = [0.15, 0.5, 0.85]
    elif duration <= 12:
        fractions = [0.08, 0.3, 0.5, 0.7, 0.92]
    elif duration <= 30:
        fractions = [0.08, 0.28, 0.5, 0.72, 0.92]
    else:
        fractions = [0.05, 0.25, 0.5, 0.75, 0.95]
    fractions = fractions[:max_frames]
    return [segment.start_sec + min(duration - 0.05, max(0.0, duration * fraction)) for fraction in fractions]


def _resize_image(path: Path, output_path: Path, max_width: int = 1280, max_height: int = 720) -> None:
    with Image.open(path) as image:
        image = image.convert("RGB")
        image.thumbnail((max_width, max_height))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path, "JPEG", quality=88)


def extract_segment_frames(
    segment: AnalysisSegment,
    output_dir: Path,
    ffmpeg_bin: str | None = None,
    max_frames: int = 5,
    max_width: int = 1280,
    max_height: int = 720,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    if segment.media_type == "image":
        output_path = output_dir / f"{segment.segment_id}_000.jpg"
        _resize_image(Path(segment.source_path), output_path, max_width=max_width, max_height=max_height)
        return [output_path]

    ffmpeg = resolve_ffmpeg(ffmpeg_bin)
    frames: list[Path] = []
    for index, timestamp in enumerate(sample_timestamps(segment, max_frames=max_frames)):
        output_path = output_dir / f"{segment.segment_id}_{index:03d}.jpg"
        vf = f"scale={max_width}:{max_height}:force_original_aspect_ratio=decrease"
        cmd = [
            ffmpeg,
            "-y",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            segment.source_path,
            "-frames:v",
            "1",
            "-vf",
            vf,
            "-q:v",
            "3",
            str(output_path),
        ]
        subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60)
        if output_path.exists():
            frames.append(output_path)
    return frames


def make_contact_sheet(frames: list[Path], output_path: Path, max_width: int = 1280) -> Path:
    images = [Image.open(frame).convert("RGB") for frame in frames if frame.exists()]
    if not images:
        raise ValueError("No frames available for contact sheet")
    thumb_width = max(1, max_width // min(len(images), 5))
    thumbs: list[Image.Image] = []
    for image in images:
        image.thumbnail((thumb_width, 720))
        thumbs.append(image.copy())
        image.close()
    width = sum(image.width for image in thumbs)
    height = max(image.height for image in thumbs)
    sheet = Image.new("RGB", (width, height), "black")
    x = 0
    for image in thumbs:
        y = (height - image.height) // 2
        sheet.paste(image, (x, y))
        x += image.width
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, "JPEG", quality=88)
    return output_path
