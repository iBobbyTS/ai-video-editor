from __future__ import annotations

import html
import json
import subprocess
from pathlib import Path
from urllib.parse import quote

from .models import Timeline, TimelineClip


def _encoder_args(codec: str) -> list[str]:
    if codec == "hevc_videotoolbox":
        return ["-c:v", "hevc_videotoolbox", "-tag:v", "hvc1", "-b:v", "8M"]
    if codec == "h264_videotoolbox":
        return ["-c:v", "h264_videotoolbox", "-b:v", "8M"]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "20"]


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, capture_output=True, text=True, check=True)


def _image_filter(clip: TimelineClip, width: int, height: int, fps: int) -> str:
    frames = max(1, round(clip.timeline_duration * fps))
    preset = clip.image_motion_preset or "static"
    zoom_expr = "1"
    x_expr = "(iw-iw/zoom)/2"
    y_expr = "(ih-ih/zoom)/2"
    if preset == "zoom_in":
        zoom_expr = "min(1.04,1+0.04*on/{frames})"
    elif preset == "zoom_out":
        zoom_expr = "max(1,1.04-0.04*on/{frames})"
    elif preset == "pan_left":
        zoom_expr = "1.04"
        x_expr = "(iw-iw/zoom)*(1-on/{frames})"
    elif preset == "pan_right":
        zoom_expr = "1.04"
        x_expr = "(iw-iw/zoom)*on/{frames}"
    elif preset == "pan_up":
        zoom_expr = "1.04"
        y_expr = "(ih-ih/zoom)*(1-on/{frames})"
    elif preset == "pan_down":
        zoom_expr = "1.04"
        y_expr = "(ih-ih/zoom)*on/{frames}"
    zoom_expr = zoom_expr.format(frames=frames)
    x_expr = x_expr.format(frames=frames)
    y_expr = y_expr.format(frames=frames)
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s={width}x{height}:fps={fps},"
        "format=yuv420p"
    )


def render_preview(
    timeline: Timeline,
    output_path: Path,
    work_dir: Path,
    ffmpeg_bin: str = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    codec: str = "h264_videotoolbox",
) -> bool:
    work_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = ffmpeg_bin if Path(ffmpeg_bin).exists() else "ffmpeg"
    rendered_parts: list[Path] = []
    for index, clip in enumerate(timeline.clips, 1):
        part_path = work_dir / f"part_{index:04d}.mp4"
        if clip.media_type == "video":
            cmd = [
                ffmpeg,
                "-y",
                "-ss",
                str(clip.source_in or 0),
                "-t",
                str(clip.timeline_duration),
                "-i",
                clip.source_path,
                "-vf",
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p",
                "-an",
                "-r",
                str(fps),
                *_encoder_args(codec),
                str(part_path),
            ]
        else:
            cmd = [
                ffmpeg,
                "-y",
                "-loop",
                "1",
                "-t",
                str(clip.timeline_duration),
                "-i",
                clip.source_path,
                "-vf",
                _image_filter(clip, width, height, fps),
                "-an",
                *_encoder_args(codec),
                str(part_path),
            ]
        try:
            _run(cmd)
        except Exception:
            if codec != "libx264":
                return render_preview(timeline, output_path, work_dir, ffmpeg_bin=ffmpeg_bin, width=width, height=height, fps=fps, codec="libx264")
            return False
        rendered_parts.append(part_path)

    concat_file = work_dir / "concat.txt"
    concat_file.write_text("".join(f"file '{path.resolve().as_posix()}'\n" for path in rendered_parts), encoding="utf-8")
    try:
        _run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), *_encoder_args(codec), "-pix_fmt", "yuv420p", str(output_path)])
    except Exception:
        return False
    return True


def write_simple_fcpxml(timeline: Timeline, output_path: Path, fps: int = 30) -> None:
    resources: list[str] = []
    spine: list[str] = []
    for index, clip in enumerate(timeline.clips, 1):
        asset_id = f"r{index}"
        path = Path(clip.source_path).expanduser().resolve()
        uri = f"file://{quote(path.as_posix(), safe='/')}"
        duration_frames = max(1, round(clip.timeline_duration * fps))
        duration = f"{duration_frames}/{fps}s"
        resources.append(
            f'<asset id="{asset_id}" name="{html.escape(path.name)}" start="0s" duration="{duration}" hasVideo="1">'
            f'<media-rep kind="original-media" src="{html.escape(uri)}" />'
            f"</asset>"
        )
        offset_frames = round(sum(c.timeline_duration for c in timeline.clips[: index - 1]) * fps)
        offset = f"{offset_frames}/{fps}s"
        start_frames = round((clip.source_in or 0) * fps)
        start = f"{start_frames}/{fps}s"
        spine.append(
            f'<asset-clip name="{html.escape(path.name)}" ref="{asset_id}" offset="{offset}" start="{start}" duration="{duration}" />'
        )

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.14">
  <resources>
    <format id="r0" name="FFVideoFormat1080p30" frameDuration="1/{fps}s" width="1920" height="1080"/>
    {" ".join(resources)}
  </resources>
  <library>
    <event name="{html.escape(timeline.project_title)}">
      <project name="{html.escape(timeline.project_title)}">
        <sequence format="r0" duration="{round(timeline.total_duration_sec * fps)}/{fps}s">
          <spine>
            {" ".join(spine)}
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
"""
    output_path.write_text(xml, encoding="utf-8")


def write_render_log(path: Path, messages: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"messages": messages}, indent=2), encoding="utf-8")
