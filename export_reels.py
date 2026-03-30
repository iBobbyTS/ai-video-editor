#!/usr/bin/env python3
"""
Export DaVinci Resolve Timeline for YouTube Shorts / Reels (9:16 vertical).
Loads video clips from assets/videos-reels/ in sequential order,
trims total duration to under 59 seconds, and picks one music track
from assets/music-teaser/.
"""

import json
import os
import random
import subprocess
import sys
from fractions import Fraction
from pathlib import Path
from urllib.parse import quote
from xml.etree.ElementTree import Element, SubElement, ElementTree, indent


def to_file_uri(path_str):
    """Convert path to a properly percent-encoded file URI for FCPXML."""
    path = Path(path_str).expanduser().resolve()
    encoded = quote(path.as_posix(), safe='/')
    return f"file://{encoded}"


def get_media_duration_frac(media_path, fps=24):
    """Get media duration as a fraction."""
    try:
        result = subprocess.run([
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            media_path
        ], capture_output=True, text=True, check=True, timeout=10)
        value = (result.stdout or '').strip()
        if value:
            duration_sec = float(value)
            if duration_sec > 0:
                return Fraction(duration_sec).limit_denominator(10000)
    except Exception:
        pass
    # Fallback: count packets
    try:
        result = subprocess.run([
            'ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-count_packets', '-show_entries', 'stream=nb_read_packets',
            '-of', 'csv=p=0', media_path
        ], capture_output=True, text=True, check=True, timeout=30)
        total_frames = int(result.stdout.strip().rstrip(','))
        return Fraction(total_frames, fps)
    except Exception:
        return None


def get_audio_info(audio_path):
    """Get audio duration, channels, and bit depth."""
    try:
        result = subprocess.run([
            'ffprobe', '-v', 'error', '-select_streams', 'a:0',
            '-show_entries', 'stream=duration,duration_ts,sample_rate,channels,bits_per_raw_sample,codec_name',
            '-show_entries', 'format=duration',
            '-of', 'json', audio_path
        ], capture_output=True, text=True, check=True, timeout=10)
        data = json.loads(result.stdout or '{}')
        streams = data.get('streams') or []
        channels = 2
        duration_frac = None
        if streams:
            stream = streams[0]
            channels = stream.get('channels', 2)
            duration_ts = stream.get('duration_ts')
            sample_rate = stream.get('sample_rate')
            duration = stream.get('duration')
            if duration_ts is not None and sample_rate is not None:
                try:
                    duration_frac = Fraction(int(duration_ts), int(sample_rate))
                except (ValueError, ZeroDivisionError):
                    pass
            if duration_frac is None and duration:
                duration_frac = Fraction(float(duration)).limit_denominator(100000)
        if duration_frac is None:
            fmt_duration = (data.get('format') or {}).get('duration')
            if fmt_duration:
                duration_frac = Fraction(float(fmt_duration)).limit_denominator(100000)
        return duration_frac, int(channels)
    except Exception:
        return None, 2


def load_project_config(config_path):
    """Load project configuration."""
    if not config_path:
        return {}
    path = Path(config_path)
    if not path.exists():
        return {}
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def create_reels_fcpxml(config=None, output_file=None):
    """Create FCP XML timeline for Reels/Shorts (9:16 vertical, <59s)."""
    config = config or {}
    reels_cfg = config.get('reels', {})
    base_dir = Path(__file__).resolve().parent

    # Resolve paths
    videos_folder = Path(reels_cfg.get('videos_folder', './assets/videos-reels'))
    if not videos_folder.is_absolute():
        videos_folder = (base_dir / videos_folder).resolve()

    music_folder = Path(reels_cfg.get('music_folder', './assets/music-teaser'))
    if not music_folder.is_absolute():
        music_folder = (base_dir / music_folder).resolve()

    max_duration = reels_cfg.get('max_duration_seconds', 59)
    timeline_width = reels_cfg.get('timeline_width', 1080)
    timeline_height = reels_cfg.get('timeline_height', 1920)

    if output_file is None:
        output_file = reels_cfg.get('timeline_file', './timeline_reels.fcpxml')
    output_path = Path(output_file)
    if not output_path.is_absolute():
        output_path = (base_dir / output_path).resolve()

    # Collect video clips sorted by name (sequential order)
    if not videos_folder.exists():
        print(f"❌ Videos-reels folder not found: {videos_folder}")
        sys.exit(1)

    video_extensions = {'.mov', '.mp4', '.mkv', '.avi'}
    video_files = sorted(
        [p for p in videos_folder.iterdir()
         if p.is_file() and p.suffix.lower() in video_extensions],
        key=lambda p: p.name.lower()
    )

    if not video_files:
        print(f"❌ No video files found in {videos_folder}")
        sys.exit(1)

    print(f"📱 Reels/Shorts Timeline Export (9:16 vertical)")
    print(f"   Videos folder: {videos_folder}")
    print(f"   Music folder:  {music_folder}")
    print(f"   Max duration:  {max_duration}s")
    print(f"   Resolution:    {timeline_width}x{timeline_height}")
    print()

    # Probe each video and accumulate up to max_duration
    fps = 24
    clips = []
    total_duration = Fraction(0)
    max_duration_frac = Fraction(max_duration)

    for vf in video_files:
        dur = get_media_duration_frac(str(vf), fps=fps)
        if dur is None:
            print(f"   ⚠️  Skipping (can't probe): {vf.name}")
            continue

        remaining = max_duration_frac - total_duration
        if remaining <= 0:
            break

        # Trim this clip if it would exceed the limit
        clip_dur = min(dur, remaining)
        clips.append({
            'path': str(vf),
            'name': vf.name,
            'full_duration': dur,
            'use_duration': clip_dur,
            'trimmed': clip_dur < dur,
        })
        total_duration += clip_dur
        status = " (trimmed)" if clip_dur < dur else ""
        print(f"   ✓ {vf.name}: {float(clip_dur):.1f}s{status}")

    if not clips:
        print("❌ No usable clips found")
        sys.exit(1)

    print(f"\n   Total video duration: {float(total_duration):.1f}s / {max_duration}s max")

    # Pick one music track from teaser music folder
    music_track = None
    if music_folder.exists():
        music_files = sorted([
            p for p in music_folder.iterdir()
            if p.is_file() and p.suffix.lower() == '.wav'
            and not p.name.endswith('.ORIG') and not p.name.endswith('.BACKUP')
        ])
        if music_files:
            # Pick a random track
            rng = random.Random()
            music_track = rng.choice(music_files)
            music_duration, music_channels = get_audio_info(str(music_track))
            print(f"   🎵 Music: {music_track.name} ({float(music_duration):.1f}s)")
        else:
            print("   ⚠️  No WAV music files found in music folder")
    else:
        print(f"   ⚠️  Music folder not found: {music_folder}")

    # Build FCP XML (version 1.13 for DaVinci Resolve)
    print(f"\n🎬 Creating Reels Timeline...")
    fcpxml = Element('fcpxml', version='1.13')
    resources = SubElement(fcpxml, 'resources')

    # Format r0: Timeline format - 1080x1920 vertical 24fps
    SubElement(resources, 'format', {
        'name': f'FFVideoFormat{timeline_width}x{timeline_height}p24',
        'height': str(timeline_height),
        'id': 'r0',
        'frameDuration': '1/24s',
        'width': str(timeline_width),
    })

    # Format r2: Source video format (may differ from timeline)
    SubElement(resources, 'format', {
        'name': 'FFVideoFormatRateUndefined',
        'height': str(timeline_height),
        'id': 'r2',
        'frameDuration': '1/24s',
        'width': str(timeline_width),
    })

    # Cross Dissolve effect
    SubElement(resources, 'effect', {
        'name': 'Cross Dissolve',
        'id': 'r1',
        'uid': 'FxPlug:4731E73A-8DAC-4113-9A30-AE85B1761265',
    })

    asset_counter = 3
    asset_map = {}  # clip index -> asset_id

    # Create assets for each video clip
    for idx, clip in enumerate(clips):
        asset_id = f'r{asset_counter}'
        asset_counter += 1
        asset = SubElement(resources, 'asset', {
            'name': clip['name'],
            'start': '0/1s',
            'hasAudio': '1',
            'id': asset_id,
            'uid': to_file_uri(clip['path']),
            'duration': f"{clip['full_duration'].numerator}/{clip['full_duration'].denominator}s",
            'hasVideo': '1',
            'audioSources': '1',
            'format': 'r0',
            'audioChannels': '2',
        })
        SubElement(asset, 'media-rep', {
            'src': to_file_uri(clip['path']),
            'kind': 'original-media',
        })
        asset_map[idx] = asset_id

    # Create asset for music track
    music_asset_id = None
    if music_track and music_duration:
        music_asset_id = f'r{asset_counter}'
        asset_counter += 1
        asset = SubElement(resources, 'asset', {
            'name': music_track.name,
            'start': '0/1s',
            'hasAudio': '1',
            'id': music_asset_id,
            'uid': to_file_uri(str(music_track)),
            'duration': f"{music_duration.numerator}/{music_duration.denominator}s",
            'hasVideo': '0',
            'audioSources': '1',
            'audioChannels': str(music_channels),
        })
        SubElement(asset, 'media-rep', {
            'src': to_file_uri(str(music_track)),
            'kind': 'original-media',
        })

    # Library / Event / Project / Sequence
    library = SubElement(fcpxml, 'library')
    event = SubElement(library, 'event', name='Reels Timeline (Resolve)')
    project = SubElement(event, 'project', name='Reels Timeline (Resolve)')

    timeline_duration_frac = total_duration

    sequence = SubElement(project, 'sequence', {
        'tcStart': '0/1s',
        'duration': f'{timeline_duration_frac.numerator}/{timeline_duration_frac.denominator}s',
        'format': 'r0',
        'tcFormat': 'NDF',
    })

    spine = SubElement(sequence, 'spine')

    # Transition settings
    transition_duration = Fraction(1001, 1000)  # ~1 second
    transition_half = transition_duration / 2

    # Place clips on the spine
    timeline_pos = Fraction(0)
    _FRAC_LIMIT = 1_000_000

    for idx, clip in enumerate(clips):
        asset_id = asset_map[idx]
        clip_dur = clip['use_duration']
        clip_offset = timeline_pos

        clip_el = SubElement(spine, 'asset-clip', {
            'name': clip['name'],
            'ref': asset_id,
            'start': '0/1s',
            'offset': f'{clip_offset.numerator}/{clip_offset.denominator}s',
            'duration': f'{clip_dur.numerator}/{clip_dur.denominator}s',
            'format': 'r0',
            'enabled': '1',
            'tcFormat': 'NDF',
            'audioStart': '0/1s',
            'audioDuration': f'{clip_dur.numerator}/{clip_dur.denominator}s',
        })

        # Mute original clip audio (we use separate music track)
        SubElement(clip_el, 'adjust-volume', {'amount': '-96dB'})

        # Fit transform
        SubElement(clip_el, 'adjust-transform', {
            'position': '0 0',
            'scale': '1 1',
            'anchor': '0 0',
        })

        timeline_pos = (timeline_pos + clip_dur).limit_denominator(_FRAC_LIMIT)

        # Add cross-dissolve transition (except after last clip)
        if transition_duration and idx < len(clips) - 1:
            t_offset = timeline_pos - transition_half
            transition = SubElement(spine, 'transition', {
                'name': 'Transition',
                'offset': f'{t_offset.numerator}/{t_offset.denominator}s',
                'duration': f'{transition_duration.numerator}/{transition_duration.denominator}s',
            })
            SubElement(transition, 'filter-video', {'name': 'Transition', 'ref': 'r1'})
            SubElement(transition, 'filter-audio', {'name': 'Transition', 'ref': 'r1'})
            timeline_pos = (timeline_pos - transition_half).limit_denominator(_FRAC_LIMIT)

    # Add music track on audio lane 2
    if music_asset_id and music_duration:
        # Use the shorter of music duration or timeline duration
        music_use_dur = min(music_duration, timeline_duration_frac)
        music_clip = SubElement(spine, 'asset-clip', {
            'name': music_track.name,
            'ref': music_asset_id,
            'start': '0/1s',
            'offset': '0/1s',
            'duration': f'{music_use_dur.numerator}/{music_use_dur.denominator}s',
            'enabled': '1',
            'tcFormat': 'NDF',
            'lane': '2',
            'audioStart': '0/1s',
            'audioDuration': f'{music_use_dur.numerator}/{music_use_dur.denominator}s',
        })

        # Fade in/out
        fade_dur = Fraction(1)  # 1 second fade
        fade_silence_db = -60.0

        if music_use_dur > fade_dur * 2:
            filter_audio = SubElement(music_clip, 'filter-audio', {
                'name': 'Volume',
            })
            param = SubElement(filter_audio, 'param', {
                'name': 'gain',
            })
            # Fade in
            SubElement(param, 'keyframe', {
                'time': '0/1s',
                'value': str(fade_silence_db),
                'interp': 'linear',
            })
            SubElement(param, 'keyframe', {
                'time': f'{fade_dur.numerator}/{fade_dur.denominator}s',
                'value': '0',
                'interp': 'linear',
            })
            # Fade out
            fade_out_start = music_use_dur - fade_dur
            SubElement(param, 'keyframe', {
                'time': f'{fade_out_start.numerator}/{fade_out_start.denominator}s',
                'value': '0',
                'interp': 'linear',
            })
            SubElement(param, 'keyframe', {
                'time': f'{music_use_dur.numerator}/{music_use_dur.denominator}s',
                'value': str(fade_silence_db),
                'interp': 'linear',
            })

    # Write XML
    tree = ElementTree(fcpxml)
    indent(tree, space='  ')
    tree.write(str(output_path), xml_declaration=True, encoding='utf-8')

    print(f"\n{'=' * 70}")
    print(f"📊 Reels Timeline Export Complete")
    print(f"{'=' * 70}")
    print(f"  Format:     FCP XML 1.13 (DaVinci Resolve)")
    print(f"  Resolution: {timeline_width}x{timeline_height} (9:16 vertical)")
    print(f"  Duration:   {float(total_duration):.1f}s / {max_duration}s max")
    print(f"  Clips:      {len(clips)}")
    if music_track:
        print(f"  Music:      {music_track.name}")
    print(f"  Output:     {output_path}")
    print(f"{'=' * 70}")
    print()
    print("⚠️  IMPORTANT - Import Steps for DaVinci Resolve:")
    print("   1. Open DaVinci Resolve")
    print("   2. Import media to Media Pool FIRST")
    print(f"      (all files from {videos_folder})")
    if music_track:
        print(f"      Also import: {music_track}")
    print("   3. Then import timeline:")
    print("      File → Import → Timeline → Import AAF/EDL/XML")
    print(f"      Select: {output_path}")
    print("   ⚠️  Media MUST be in Media Pool before importing XML!")
    print()

    return str(output_path)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Export Reels/Shorts FCPXML timeline (9:16 vertical)')
    parser.add_argument('--config', default='project_config.json', help='Project config JSON file')
    parser.add_argument('--output', default=None, help='Output FCPXML file path')
    args = parser.parse_args()

    config = load_project_config(args.config)
    create_reels_fcpxml(config=config, output_file=args.output)
