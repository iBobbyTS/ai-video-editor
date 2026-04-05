#!/usr/bin/env python3
"""
Audio silence and motion analysis for unboxing-style videos.

Detects silent + static segments that are likely boring (dead air).
Used by the unboxing pipeline mode to identify scenes to cut without
altering speed or muting narration audio.
"""

import json
import subprocess
import sys
from pathlib import Path


def detect_silence_ranges(video_path, threshold_db=-35, min_duration=3.0):
    """Detect silent segments in a video's audio track using ffmpeg silencedetect.

    Returns list of dicts: [{'start': float, 'end': float, 'duration': float}, ...]
    """
    cmd = [
        'ffmpeg', '-i', str(video_path),
        '-af', f'silencedetect=noise={threshold_db}dB:d={min_duration}',
        '-f', 'null', '-'
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    stderr = result.stderr

    ranges = []
    current_start = None
    for line in stderr.splitlines():
        if 'silence_start:' in line:
            try:
                current_start = float(line.split('silence_start:')[1].strip().split()[0])
            except (ValueError, IndexError):
                current_start = None
        elif 'silence_end:' in line and current_start is not None:
            try:
                parts = line.split('silence_end:')[1].strip().split()
                end = float(parts[0])
                dur_part = line.split('silence_duration:')
                dur = float(dur_part[1].strip()) if len(dur_part) > 1 else end - current_start
                ranges.append({
                    'start': current_start,
                    'end': end,
                    'duration': dur,
                })
            except (ValueError, IndexError):
                pass
            current_start = None
    return ranges


def detect_static_ranges(video_path, threshold=0.02, min_duration=3.0, sample_interval=1):
    """Detect visually static segments using frame-difference analysis.

    Uses ffmpeg's 'mestimate' + 'metadata' filter to compute per-frame
    motion scores, then merges consecutive low-motion frames into ranges.

    Returns list of dicts: [{'start': float, 'end': float, 'duration': float}, ...]
    """
    # Use scene-change detection score as a simpler proxy for motion
    cmd = [
        'ffprobe',
        '-f', 'lavfi',
        '-i', f'movie={str(video_path)},select=not(mod(n\\,{sample_interval})),signalstats[out0]',
        '-show_entries', 'frame_tags=lavfi.signalstats.HUEAVG',
        '-of', 'csv=p=0',
        '-v', 'quiet',
    ]
    # Fallback: use freezedetect which is simpler and more reliable
    cmd = [
        'ffmpeg', '-i', str(video_path),
        '-vf', f'freezedetect=n={threshold}:d={min_duration}',
        '-f', 'null', '-'
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    stderr = result.stderr

    ranges = []
    current_start = None
    for line in stderr.splitlines():
        if 'freeze_start:' in line:
            try:
                current_start = float(line.split('freeze_start:')[1].strip().split()[0])
            except (ValueError, IndexError):
                current_start = None
        elif 'freeze_end:' in line and current_start is not None:
            try:
                parts = line.split('freeze_end:')[1].strip().split()
                end = float(parts[0])
                dur_part = line.split('freeze_duration:')
                dur = float(dur_part[1].strip()) if len(dur_part) > 1 else end - current_start
                ranges.append({
                    'start': current_start,
                    'end': end,
                    'duration': dur,
                })
            except (ValueError, IndexError):
                pass
            current_start = None
    return ranges


def find_boring_segments(silence_ranges, static_ranges, require_both=True):
    """Find segments that are boring (silent AND/OR static).

    If require_both=True (default), only segments where silence AND static
    overlap are considered boring. Otherwise, either condition is enough.

    Returns list of dicts with merged boring ranges.
    """
    if not require_both:
        # Union of both lists
        all_ranges = silence_ranges + static_ranges
        if not all_ranges:
            return []
        all_ranges.sort(key=lambda r: r['start'])
        merged = [all_ranges[0].copy()]
        for r in all_ranges[1:]:
            if r['start'] <= merged[-1]['end']:
                merged[-1]['end'] = max(merged[-1]['end'], r['end'])
                merged[-1]['duration'] = merged[-1]['end'] - merged[-1]['start']
            else:
                merged.append(r.copy())
        return merged

    # Intersection: find overlapping ranges
    boring = []
    for s in silence_ranges:
        for m in static_ranges:
            # Compute overlap
            overlap_start = max(s['start'], m['start'])
            overlap_end = min(s['end'], m['end'])
            if overlap_end > overlap_start:
                boring.append({
                    'start': overlap_start,
                    'end': overlap_end,
                    'duration': overlap_end - overlap_start,
                })
    # Merge overlapping boring ranges
    if not boring:
        return []
    boring.sort(key=lambda r: r['start'])
    merged = [boring[0].copy()]
    for r in boring[1:]:
        if r['start'] <= merged[-1]['end']:
            merged[-1]['end'] = max(merged[-1]['end'], r['end'])
            merged[-1]['duration'] = merged[-1]['end'] - merged[-1]['start']
        else:
            merged.append(r.copy())
    return merged


def analyze_audio_and_motion(video_path, config=None):
    """Full audio+motion analysis for unboxing mode.

    Args:
        video_path: Path to the video file.
        config: dict with unboxing config (from project_config.json 'unboxing' section).

    Returns dict with silence_ranges, static_ranges, boring_segments, and video_duration.
    """
    config = config or {}
    threshold_db = config.get('silence_threshold_db', -35)
    silence_min = config.get('silence_min_duration', 3.0)
    motion_threshold = config.get('motion_threshold', 0.02)
    require_both = config.get('boring_requires_both', True)

    video_path = Path(video_path)
    print(f"\n🔇 Analyzing audio silence (threshold={threshold_db}dB, min={silence_min}s)...")
    silence = detect_silence_ranges(video_path, threshold_db=threshold_db, min_duration=silence_min)
    print(f"   Found {len(silence)} silent segments")
    for s in silence:
        print(f"     {s['start']:.1f}s - {s['end']:.1f}s ({s['duration']:.1f}s)")

    print(f"\n📷 Analyzing video motion (freeze threshold={motion_threshold}, min={silence_min}s)...")
    static = detect_static_ranges(video_path, threshold=motion_threshold, min_duration=silence_min)
    print(f"   Found {len(static)} static/frozen segments")
    for s in static:
        print(f"     {s['start']:.1f}s - {s['end']:.1f}s ({s['duration']:.1f}s)")

    boring = find_boring_segments(silence, static, require_both=require_both)
    print(f"\n🚫 Boring segments ({'silence AND static' if require_both else 'silence OR static'}): {len(boring)}")
    for b in boring:
        print(f"     {b['start']:.1f}s - {b['end']:.1f}s ({b['duration']:.1f}s)")

    # Get video duration
    probe_cmd = [
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', str(video_path)
    ]
    dur_result = subprocess.run(probe_cmd, capture_output=True, text=True)
    try:
        video_duration = float(dur_result.stdout.strip())
    except ValueError:
        video_duration = 0.0

    total_boring = sum(b['duration'] for b in boring)
    print(f"\n📊 Total boring: {total_boring:.1f}s / {video_duration:.1f}s ({total_boring/video_duration*100:.1f}%)" if video_duration else "")

    return {
        'silence_ranges': silence,
        'static_ranges': static,
        'boring_segments': boring,
        'video_duration': video_duration,
    }


def _find_voice_boundaries(scene_start, scene_end, silence_ranges, pre_roll=2.0, post_roll=1.0):
    """Find where voice actually starts and ends within a scene.

    Builds a list of "voice active" intervals by inverting silence ranges
    within the scene boundaries. Returns (trimmed_start, trimmed_end) with
    pre/post roll applied, or (scene_start, scene_end) if no trimming needed.
    """
    # Collect silence intervals that overlap this scene
    silences_in_scene = []
    for s in silence_ranges:
        s_start = max(scene_start, s['start'])
        s_end = min(scene_end, s['end'])
        if s_end > s_start:
            silences_in_scene.append((s_start, s_end))
    silences_in_scene.sort()

    if not silences_in_scene:
        return scene_start, scene_end  # No silence → no trimming

    # Find first voice start: end of leading silence (if scene starts with silence)
    first_voice_start = scene_start
    if silences_in_scene[0][0] <= scene_start + 0.5:
        # Scene starts with silence — voice begins after it
        first_voice_start = silences_in_scene[0][1]
        # Check if consecutive silences merge at the beginning
        for i in range(1, len(silences_in_scene)):
            if silences_in_scene[i][0] - first_voice_start < 1.0:
                first_voice_start = silences_in_scene[i][1]
            else:
                break

    # Find last voice end: start of trailing silence (if scene ends with silence)
    last_voice_end = scene_end
    if silences_in_scene[-1][1] >= scene_end - 0.5:
        # Scene ends with silence — voice ends before it
        last_voice_end = silences_in_scene[-1][0]
        # Check if consecutive silences merge at the end
        for i in range(len(silences_in_scene) - 2, -1, -1):
            if last_voice_end - silences_in_scene[i][1] < 1.0:
                last_voice_end = silences_in_scene[i][0]
            else:
                break

    # Apply pre/post roll (but don't exceed original scene boundaries)
    trimmed_start = max(scene_start, first_voice_start - pre_roll)
    trimmed_end = min(scene_end, last_voice_end + post_roll)

    # Safety: ensure we have at least 3s of content
    if trimmed_end - trimmed_start < 3.0:
        return scene_start, scene_end

    return trimmed_start, trimmed_end


def mark_scenes_from_audio_analysis(scenes, audio_result, boring_overlap_threshold=0.5):
    """Mark scenes as boring based on audio/motion analysis overlap.

    For unboxing mode: scenes where boring segments cover > threshold of
    the scene duration get classified as 'boring'. All other scenes keep
    their original classification but speed is forced to 1.0.

    Also trims scene boundaries to voice activity — removes leading/trailing
    silence (shaky camera, dead air) and starts from where the voice begins,
    with a small pre-roll buffer.

    Args:
        scenes: list of scene dicts (from analysis JSON).
        audio_result: output from analyze_audio_and_motion().
        boring_overlap_threshold: fraction of scene that must be boring (0.0-1.0).

    Returns modified scenes list.
    """
    boring_segments = audio_result.get('boring_segments', [])
    silence_ranges = audio_result.get('silence_ranges', [])

    for scene in scenes:
        start = scene['start_time']
        end = scene['end_time']
        scene_dur = scene['duration']

        # Calculate how much of this scene overlaps with boring segments
        boring_overlap = 0.0
        for b in boring_segments:
            overlap_start = max(start, b['start'])
            overlap_end = min(end, b['end'])
            if overlap_end > overlap_start:
                boring_overlap += overlap_end - overlap_start

        # Calculate how much of this scene is silent (no voice)
        silence_overlap = 0.0
        for s in silence_ranges:
            overlap_start = max(start, s['start'])
            overlap_end = min(end, s['end'])
            if overlap_end > overlap_start:
                silence_overlap += overlap_end - overlap_start

        boring_ratio = boring_overlap / scene_dur if scene_dur > 0 else 0.0
        silence_ratio = silence_overlap / scene_dur if scene_dur > 0 else 0.0
        has_voice = silence_ratio < 0.5  # Voice present if less than 50% silent

        scene['audio_boring_ratio'] = round(boring_ratio, 3)
        scene['silence_ratio'] = round(silence_ratio, 3)
        scene['has_voice'] = has_voice

        if boring_ratio >= boring_overlap_threshold:
            scene['classification'] = 'boring'
            scene['speed'] = 1.0
            scene['audio_boring'] = True
        else:
            # Voice-over protection: rescue boring scenes that have active voice
            if scene.get('classification') == 'boring' and has_voice:
                scene['classification'] = 'moderate'
                scene['audio_rescued'] = True
                print(f"   🎙️ Scene {scene.get('scene_num', '?'):02d}: rescued from boring → moderate (voice detected, silence={silence_ratio:.0%})")
            scene['speed'] = 1.0
            scene['audio_boring'] = False

        # Trim scene boundaries to voice activity (remove leading/trailing silence)
        if has_voice and silence_ranges:
            orig_start = scene['start_time']
            orig_end = scene['end_time']
            trimmed_start, trimmed_end = _find_voice_boundaries(
                orig_start, orig_end, silence_ranges, pre_roll=2.0, post_roll=1.0
            )
            if trimmed_start != orig_start or trimmed_end != orig_end:
                scene['original_start_time'] = orig_start
                scene['original_end_time'] = orig_end
                scene['original_duration'] = scene['duration']
                scene['start_time'] = round(trimmed_start, 2)
                scene['end_time'] = round(trimmed_end, 2)
                scene['duration'] = round(trimmed_end - trimmed_start, 2)
                scene['audio_trimmed'] = True
                saved = round(scene['original_duration'] - scene['duration'], 1)
                print(f"   ✂️  Scene {scene.get('scene_num', '?'):02d}: trimmed {orig_start:.1f}s-{orig_end:.1f}s → {trimmed_start:.1f}s-{trimmed_end:.1f}s (saved {saved}s silence)")

    return scenes


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description="Analyze audio silence and video motion")
    parser.add_argument('video', help="Path to video file")
    parser.add_argument('--config', default='project_config.json', help="Project config JSON file")
    parser.add_argument('--output', help="Output JSON file for results")
    args = parser.parse_args()

    config = {}
    config_path = Path(args.config)
    if config_path.exists():
        with open(config_path) as f:
            config = json.load(f).get('unboxing', {})

    result = analyze_audio_and_motion(args.video, config)

    if args.output:
        out_path = Path(args.output)
        with open(out_path, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"\n💾 Saved to {out_path}")
