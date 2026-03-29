#!/usr/bin/env python3
"""
Export DaVinci Resolve Timeline (FCP XML)
References original video with speed changes - no re-encoding needed
"""

import json
import os
import sys
import subprocess
import shutil
import random
import functools
from pathlib import Path
from urllib.parse import quote
from xml.etree.ElementTree import Element, SubElement, ElementTree
from fractions import Fraction

DEFAULT_TIMELINE_WIDTH = 3840
DEFAULT_TIMELINE_HEIGHT = 2160
DEFAULT_WATERMARK_MARGIN = 80


def get_media_dimensions(media_path):
    try:
        result = subprocess.run([
            'ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height',
            '-of', 'csv=p=0:s=x', media_path
        ], capture_output=True, text=True, check=True, timeout=2)
        value = (result.stdout or '').strip()
        if 'x' in value:
            width_str, height_str = value.split('x', 1)
            return int(width_str), int(height_str)
    except Exception:
        return None
    return None


def normalize_transparency(value):
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value > 1:
        value = value / 100.0
    if value < 0:
        value = 0.0
    if value > 1:
        value = 1.0
    return value


def compute_watermark_position(position, image_size, margin=DEFAULT_WATERMARK_MARGIN,
                               timeline_width=DEFAULT_TIMELINE_WIDTH,
                               timeline_height=DEFAULT_TIMELINE_HEIGHT):
    if isinstance(position, dict) and 'x' in position and 'y' in position:
        return f"{position['x']} {position['y']}"
    if isinstance(position, (list, tuple)) and len(position) >= 2:
        return f"{position[0]} {position[1]}"

    image_width, image_height = image_size if image_size else (400, 400)
    half_w = timeline_width / 2
    half_h = timeline_height / 2
    offset_x = half_w - (image_width / 2) - margin
    offset_y = half_h - (image_height / 2) - margin

    pos = (position or '').lower().strip()
    if pos in ('top-left', 'topleft'):
        return f"{-offset_x} {offset_y}"
    if pos in ('top-right', 'topright'):
        return f"{offset_x} {offset_y}"
    if pos in ('bottom-left', 'bottomleft'):
        return f"{-offset_x} {-offset_y}"
    if pos in ('center', 'middle'):
        return "0 0"
    return f"{offset_x} {-offset_y}"

@functools.lru_cache(maxsize=4096)
def get_video_duration_frac(video_path, fps=24):
    result = subprocess.run([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-count_packets', '-show_entries', 'stream=nb_read_packets',
        '-of', 'csv=p=0', video_path
    ], capture_output=True, text=True, check=True)
    total_frames = int(result.stdout.strip().rstrip(','))
    return Fraction(total_frames, fps)


@functools.lru_cache(maxsize=4096)
def get_media_duration_frac(media_path, fps=24):
    """Prefer container duration; fall back to frame count."""
    try:
        result = subprocess.run([
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            media_path
        ], capture_output=True, text=True, check=True, timeout=2)
        value = (result.stdout or '').strip()
        if value:
            duration_sec = float(value)
            if duration_sec > 0:
                return Fraction(duration_sec).limit_denominator(10000)
    except Exception:
        pass
    return get_video_duration_frac(media_path, fps=fps)


def get_audio_info(audio_path):
    try:
        result = subprocess.run([
            'ffprobe', '-v', 'error', '-select_streams', 'a:0',
            '-show_entries', 'stream=duration,duration_ts,sample_rate,channels,bits_per_raw_sample,codec_name',
            '-show_entries', 'format=duration',
            '-of', 'json', audio_path
        ], capture_output=True, text=True, check=True, timeout=2)
        data = json.loads(result.stdout or '{}')
        streams = data.get('streams') or []
        channels = None
        duration = None
        duration_ts = None
        sample_rate = None
        bits = None
        codec = None
        if streams:
            stream = streams[0]
            channels = stream.get('channels')
            duration = stream.get('duration')
            duration_ts = stream.get('duration_ts')
            sample_rate = stream.get('sample_rate')
            bits = stream.get('bits_per_raw_sample')
            codec = stream.get('codec_name')
        if duration is None:
            duration = (data.get('format') or {}).get('duration')
        # Use sample-exact fraction (duration_ts / sample_rate) when available
        # to avoid rounding errors that can cause DaVinci Resolve audio issues
        duration_frac = None
        if duration_ts is not None and sample_rate is not None:
            try:
                duration_frac = Fraction(int(duration_ts), int(sample_rate))
            except (ValueError, ZeroDivisionError):
                pass
        if duration_frac is None:
            duration_sec = float(duration) if duration is not None else None
            if duration_sec and duration_sec > 0:
                duration_frac = Fraction(duration_sec).limit_denominator(100000)
        if channels is None:
            channels = 2
        bit_depth = int(bits) if bits else None
        if bit_depth is None and codec:
            if '16' in codec:
                bit_depth = 16
            elif '24' in codec:
                bit_depth = 24
            elif '32' in codec:
                bit_depth = 32
        return duration_frac, int(channels), bit_depth
    except Exception:
        return None, 2, None


def normalize_audio_folder(music_dir):
    """Pre-check WAV files in a music folder and convert non-24-bit files in-place."""
    music_dir = Path(music_dir)
    if not music_dir.exists() or not music_dir.is_dir():
        return
    wav_files = sorted(music_dir.glob('*.wav'))
    if not wav_files:
        return
    converted = 0
    for wav_path in wav_files:
        _, _, bit_depth = get_audio_info(str(wav_path))
        if bit_depth is not None and bit_depth != 24:
            print(f"  🔧 Converting {wav_path.name} from {bit_depth}-bit to 24-bit...")
            tmp_path = wav_path.with_suffix('.tmp.wav')
            try:
                subprocess.run([
                    'ffmpeg', '-y', '-i', str(wav_path),
                    '-acodec', 'pcm_s24le', '-ar', '44100',
                    str(tmp_path)
                ], capture_output=True, check=True, timeout=120)
                shutil.move(str(tmp_path), str(wav_path))
                converted += 1
                print(f"    ✓ {wav_path.name} converted to 24-bit")
            except Exception as e:
                print(f"    ⚠️  Failed to convert {wav_path.name}: {e}")
                if tmp_path.exists():
                    tmp_path.unlink()
    if converted:
        print(f"  ✅ Normalized {converted} audio file(s) to 24-bit")
    else:
        print(f"  ✓ All {len(wav_files)} audio files are 24-bit")

    # Strip BWF time_reference metadata (causes silence offset in DaVinci Resolve)
    bwf_fixed = 0
    for wav_path in wav_files:
        try:
            result = subprocess.run([
                'ffprobe', '-v', 'quiet', '-show_entries',
                'format_tags=time_reference', '-of', 'csv=p=0',
                str(wav_path)
            ], capture_output=True, text=True, timeout=10)
            time_ref = result.stdout.strip()
            if time_ref and time_ref != '0':
                print(f"  🔧 Stripping BWF time_reference={time_ref} from {wav_path.name}...")
                orig_path = wav_path.with_suffix('.wav.ORIG')
                if not orig_path.exists():
                    shutil.copy2(str(wav_path), str(orig_path))
                tmp_path = wav_path.with_suffix('.bwf_tmp.wav')
                subprocess.run([
                    'ffmpeg', '-y', '-i', str(wav_path),
                    '-map_metadata', '-1', '-fflags', '+bitexact',
                    '-c:a', 'copy', str(tmp_path)
                ], capture_output=True, check=True, timeout=120)
                shutil.move(str(tmp_path), str(wav_path))
                bwf_fixed += 1
                print(f"    ✓ {wav_path.name} BWF metadata stripped")
        except Exception as e:
            print(f"    ⚠️  Failed to check/fix BWF for {wav_path.name}: {e}")
            tmp_path = wav_path.with_suffix('.bwf_tmp.wav')
            if tmp_path.exists():
                tmp_path.unlink()
    if bwf_fixed:
        print(f"  ✅ Fixed {bwf_fixed} audio file(s) with BWF time_reference offset")
    else:
        print(f"  ✓ All {len(wav_files)} audio files are BWF-clean")


def get_video_rotation_degrees(video_path):
    """Return rotation in degrees from video metadata (0, 90, 180, 270)."""
    try:
        result = subprocess.run([
            'ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-read_intervals', '0%+0.1',
            '-show_entries', 'stream_tags=rotate:side_data_list',
            '-of', 'json', video_path
        ], capture_output=True, text=True, check=True, timeout=2)
        data = json.loads(result.stdout or '{}')
        streams = data.get('streams') or []
        if not streams:
            return 0

        stream = streams[0]
        rotate_tag = None
        tags = stream.get('tags') or {}
        if 'rotate' in tags:
            rotate_tag = tags.get('rotate')

        side_data = stream.get('side_data_list') or []
        rotate_side = None
        for item in side_data:
            if 'rotation' in item:
                rotate_side = item.get('rotation')
                break

        rotate_value = rotate_tag if rotate_tag is not None else rotate_side
        if rotate_value is None:
            return 0

        rotation = int(round(float(rotate_value)))
        rotation = rotation % 360
        if rotation in (0, 90, 180, 270):
            return rotation
        return 0
    except Exception:
        return 0


def load_project_config(config_path):
    if not config_path:
        return {}
    path = Path(config_path)
    if not path.exists():
        return {}
    try:
        with open(path, 'r') as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        return {}
    return {}


def load_analyses(analysis_path, video_dir=None, video_file=None):
    analysis_path = Path(analysis_path)
    analyses = []
    
    if analysis_path.is_dir():
        analysis_files = sorted(analysis_path.glob('scene_analysis_*.json'), key=lambda p: p.name.lower())
    else:
        analysis_files = [analysis_path]
    
    for analysis_file in analysis_files:
        with open(analysis_file, 'r') as f:
            data = json.load(f)
        video_name = data.get('video')
        if not video_name and video_file:
            video_name = os.path.basename(video_file)
        if not video_name:
            continue
        
        if video_file and not analysis_path.is_dir():
            video_path = Path(video_file)
        elif video_dir:
            video_path = Path(video_dir) / video_name
        else:
            video_path = analysis_file.parent / video_name
        
        analyses.append({
            'data': data,
            'analysis_file': analysis_file,
            'video_name': video_name,
            'video_path': video_path
        })
    
    analyses.sort(key=lambda x: x['video_name'].lower())
    return analyses


def hash_distance(a, b):
    if not a or not b:
        return None
    try:
        return bin(int(a, 16) ^ int(b, 16)).count('1')
    except ValueError:
        return None


def dedupe_clip_infos(clip_infos, threshold):
    """Remove near-duplicate scenes across videos using scene_hash."""
    selected = []
    for info in clip_infos:
        scene_hash = info['scene'].get('scene_hash')
        if not scene_hash:
            selected.append(info)
            continue
        
        duplicate_idx = None
        for idx, kept in enumerate(selected):
            dist = hash_distance(scene_hash, kept['scene'].get('scene_hash'))
            if dist is not None and dist <= threshold:
                duplicate_idx = idx
                break
        
        if duplicate_idx is None:
            selected.append(info)
        else:
            current_score = info['scene'].get('quality_score', 0)
            kept_score = selected[duplicate_idx]['scene'].get('quality_score', 0)
            if current_score > kept_score:
                selected[duplicate_idx] = info
    return selected


def to_file_uri(path_str, clips_dir=None):
    """
    Convert path to a properly percent-encoded file URI for FCPXML.
    Uses absolute file:// URIs for all media files so DaVinci can locate them.
    """
    path = Path(path_str).expanduser().resolve()
    encoded = quote(path.as_posix(), safe='/')
    return f"file://{encoded}"


def find_rendered_clip(rendered_dir, video_stem, scene_num, classification, speed, extensions=None):
    extensions = extensions or ['.mkv']
    extensions = [ext if ext.startswith('.') else f".{ext}" for ext in extensions]
    prefix = f"{video_stem}_" if video_stem else ""
    candidates = []
    for ext in extensions:
        candidates.extend([
            f"{prefix}scene_{scene_num:02d}_{classification}_{speed:.2f}x{ext}",
            f"{prefix}scene_{scene_num:02d}_{classification}_{speed:.1f}x{ext}",
            f"{prefix}scene_{scene_num:02d}_{classification}_{speed:g}x{ext}",
        ])
    for name in candidates:
        path = rendered_dir / name
        if path.exists():
            return path

    matches = []
    for ext in extensions:
        pattern = f"{prefix}scene_{scene_num:02d}_{classification}_*x{ext}"
        matches.extend(rendered_dir.glob(pattern))
    matches = sorted(matches)
    if len(matches) == 1:
        return matches[0]

    def parse_speed(path):
        try:
            speed_str = path.stem.split('_')[-1].replace('x', '')
            return float(speed_str)
        except (ValueError, IndexError):
            return None

    for match in matches:
        match_speed = parse_speed(match)
        if match_speed is not None and abs(match_speed - speed) <= 0.01:
            return match

    return None


def create_fcpxml_timeline(analysis_path, video_dir, output_file, clip_base_dir='ai_clips', dedupe=False, hash_threshold=6, use_rendered=True, resolve_format=True, exclude_boring=False, config=None):
    """Create FCP XML timeline for DaVinci Resolve"""
    config = config or {}
    timeline_config = config.get('timeline', config)
    export_cfg = config.get('export', {})
    clip_ext = export_cfg.get('clip_format', 'mkv')
    clip_ext = f".{str(clip_ext).lower().lstrip('.')}"
    clip_exts = [clip_ext, '.mkv', '.mov', '.mp4']
    clip_exts = [ext for i, ext in enumerate(clip_exts) if ext not in clip_exts[:i]]
    
    # Read exclude_boring from config if not explicitly set via command-line
    # Command-line argument (exclude_boring parameter) takes precedence over config
    if not exclude_boring and timeline_config.get('exclude_boring', False):
        exclude_boring = True
    
    analyses = load_analyses(analysis_path, video_dir=video_dir, video_file=video_dir if video_dir and Path(video_dir).is_file() else None)
    if not analyses:
        raise ValueError("No analysis files found.")
    
    fps = 24
    clip_infos = []
    video_rotations = {}
    for analysis in analyses:
        scenes = analysis['data']['scenes']
        video_path = analysis['video_path']
        video_name = analysis['video_name']
        video_stem = Path(video_name).stem
        rendered_dir = Path(clip_base_dir) / video_stem

        if video_name not in video_rotations:
            video_rotations[video_name] = get_video_rotation_degrees(str(video_path))
        
        video_duration_frac = get_video_duration_frac(str(video_path), fps=fps)
        
        for i, scene in enumerate(scenes, 1):
            speed = scene['speed']
            duration_sec = scene['duration']
            output_duration_sec = duration_sec / speed
            output_duration_frames = round(output_duration_sec * fps)
            output_duration_frac = Fraction(output_duration_frames, fps)
            
            classification = scene.get('classification', 'unknown')
            rendered_path = find_rendered_clip(rendered_dir, video_stem, i, classification, speed, extensions=clip_exts)
            use_rendered_clip = use_rendered and rendered_path is not None
            rendered_duration_frac = None
            if use_rendered_clip:
                try:
                    rendered_duration_frac = get_media_duration_frac(str(rendered_path), fps=fps)
                except Exception:
                    rendered_duration_frac = None
            if rendered_duration_frac and rendered_duration_frac < output_duration_frac:
                effective_output_duration = rendered_duration_frac
            else:
                effective_output_duration = output_duration_frac
            if use_rendered_clip:
                effective_src_duration = rendered_duration_frac or output_duration_frac
            else:
                effective_src_duration = video_duration_frac
            
            clip_infos.append({
                'scene': scene,
                'video_name': video_name,
                'video_path': str(video_path),
                'output_duration_frac': effective_output_duration,
                'use_rendered': use_rendered_clip,
                'src_path': str(rendered_path) if use_rendered_clip else str(video_path),
                'src_name': rendered_path.name if use_rendered_clip else video_name,
                'src_duration_frac': effective_src_duration,
                'rotation': video_rotations.get(video_name, 0)
            })

    def build_static_clip_info(clip_path, label, clips_dir, preferred_dir=None, copy_to_clips_dir=True):
        clip_path = Path(clip_path).expanduser().resolve()
        if not clip_path.exists():
            print(f"⚠️  Missing {label} clip: {clip_path}")
            return None
        video_name = clip_path.name
        preferred_path = clip_path
        if clips_dir and copy_to_clips_dir:
            clips_dir_path = Path(clips_dir).expanduser().resolve()
            target_dir = Path(preferred_dir).expanduser().resolve() if preferred_dir else clips_dir_path
            candidate = target_dir / video_name
            if not candidate.exists() or candidate.is_symlink():
                try:
                    candidate.parent.mkdir(parents=True, exist_ok=True)
                    if candidate.is_symlink():
                        candidate.unlink()
                    shutil.copy2(clip_path, candidate)
                except Exception as exc:
                    print(f"⚠️  Could not copy {label} clip to {candidate}: {exc}")
            if candidate.exists():
                preferred_path = candidate
        rotation = get_video_rotation_degrees(str(preferred_path))
        video_rotations[video_name] = rotation
        duration_frac = get_video_duration_frac(str(preferred_path), fps=fps)
        duration_sec = float(duration_frac)
        
        # For static images (photos), use default 5-second duration
        if duration_sec <= 0 and label in ('closing_photo',):
            duration_sec = 5.0
            duration_frac = Fraction(5, 1)
        
        scene = {
            'start_time': 0.0,
            'end_time': duration_sec,
            'duration': duration_sec,
            'speed': 1.0,
            'classification': label
        }
        return {
            'scene': scene,
            'video_name': video_name,
            'video_path': str(preferred_path),
            'output_duration_frac': duration_frac,
            'use_rendered': True,
            'src_path': str(preferred_path),
            'src_name': video_name,
            'src_duration_frac': duration_frac,
            'rotation': rotation
        }
    
    if dedupe:
        before = len(clip_infos)
        clip_infos = dedupe_clip_infos(clip_infos, hash_threshold)
        after = len(clip_infos)
        if after < before:
            print(f"🔁 Deduped scenes: {before} → {after} (threshold={hash_threshold})")

    # Filter scenes based on classification
    # Option 1: exclude_boring (legacy support - excludes only boring)
    # Option 2: include_classifications (explicit list of classifications to include)
    include_classifications = timeline_config.get('include_classifications')
    
    if include_classifications:
        # Use explicit inclusion list
        before = len(clip_infos)
        clip_infos = [info for info in clip_infos if info['scene'].get('classification') in include_classifications]
        after = len(clip_infos)
        if after < before:
            excluded_count = before - after
            included = ', '.join(include_classifications)
            print(f"🎬 Filtered scenes by classification: {before} → {after} (included: {included})")
    elif exclude_boring:
        # Legacy behavior: exclude only boring
        before = len(clip_infos)
        clip_infos = [info for info in clip_infos if info['scene'].get('classification') != 'boring']
        after = len(clip_infos)
        if after < before:
            print(f"🚫 Excluded boring scenes: {before} → {after}")

    # ---- Smart selection: fit content into max_duration_minutes budget ----
    max_duration_minutes = timeline_config.get('max_duration_minutes')
    smart_select_enabled = timeline_config.get('smart_select', False)

    if max_duration_minutes and smart_select_enabled and clip_infos:
        max_duration_sec = float(max_duration_minutes) * 60.0
        weights = timeline_config.get('smart_select_weights', {})
        w_rating = float(weights.get('llm_rating', 1.0))
        w_semantic = float(weights.get('semantic_interest', 3.0))
        w_dup = float(weights.get('duplicate_penalty', 0.3))
        w_rep = float(weights.get('repetitive_penalty', 0.3))
        min_per_video_sec = float(weights.get('min_per_video_seconds', 10))

        # Estimate overhead from intro/outro/teaser/closing (not yet added)
        overhead_sec = 0.0
        if timeline_config.get('intro_clip'):
            overhead_sec += 8.0  # rough intro duration
        if timeline_config.get('outro_clip'):
            overhead_sec += 8.0  # rough outro duration
        teaser_dur = float(timeline_config.get('teaser_max_duration', 65.0))
        if timeline_config.get('teaser_enabled', True):
            overhead_sec += teaser_dur
        # Closing photos/teasers estimate
        paths_cfg_ss = config.get('paths', {})
        if paths_cfg_ss.get('photos'):
            photos_path_ss = Path(paths_cfg_ss['photos']).expanduser().resolve()
            if photos_path_ss.exists():
                photo_count = sum(1 for f in photos_path_ss.iterdir()
                                 if f.is_file() and f.suffix.lower() in {'.jpg','.jpeg','.png','.bmp','.tiff'})
                closing_photo_dur = float(timeline_config.get('closing_photos', {}).get('duration_seconds', 3))
                overhead_sec += photo_count * closing_photo_dur
        if paths_cfg_ss.get('teaser_videos'):
            tv_path_ss = Path(paths_cfg_ss['teaser_videos']).expanduser().resolve()
            if tv_path_ss.exists():
                for f in tv_path_ss.iterdir():
                    if f.is_file() and f.suffix.lower() in {'.mov','.mp4','.mkv','.avi','.m4v'}:
                        overhead_sec += 18.0  # rough per teaser-video

        content_budget = max(60.0, max_duration_sec - overhead_sec)
        current_total = sum(float(info['output_duration_frac']) for info in clip_infos)

        if current_total > content_budget:
            # Score each clip
            def _compute_quality_score(info):
                scene = info['scene']
                rating = float(scene.get('llm_rating', 5))
                sem = float(scene.get('semantic_interest', 0.1))
                dup = float(scene.get('duplicate_ratio', 0))
                rep = float(scene.get('repetitive_ratio', 0))
                cls = scene.get('classification', 'moderate')
                score = (rating * w_rating) * (1.0 - dup * w_dup - rep * w_rep) + sem * w_semantic
                if cls == 'interesting':
                    score += 2.0  # bonus for interesting
                return score

            for info in clip_infos:
                info['_quality_score'] = _compute_quality_score(info)

            # Group clips by video
            from collections import defaultdict
            videos_clips = defaultdict(list)
            for info in clip_infos:
                videos_clips[info['video_name']].append(info)

            # Phase 1: guarantee minimum per video (highest-scored first)
            guaranteed = []
            remaining_pool = []
            for vname, vclips in videos_clips.items():
                vclips_sorted = sorted(vclips, key=lambda x: x['_quality_score'], reverse=True)
                video_dur = 0.0
                for clip in vclips_sorted:
                    clip_dur = float(clip['output_duration_frac'])
                    if video_dur < min_per_video_sec:
                        guaranteed.append(clip)
                        video_dur += clip_dur
                    else:
                        remaining_pool.append(clip)

            guaranteed_dur = sum(float(c['output_duration_frac']) for c in guaranteed)

            # Phase 2: fill remaining budget with highest-scored clips
            remaining_pool.sort(key=lambda x: x['_quality_score'], reverse=True)
            budget_left = content_budget - guaranteed_dur
            extra = []
            for clip in remaining_pool:
                clip_dur = float(clip['output_duration_frac'])
                if budget_left >= clip_dur:
                    extra.append(clip)
                    budget_left -= clip_dur

            # Merge and restore original ordering
            selected_set = set(id(c) for c in guaranteed) | set(id(c) for c in extra)
            clip_infos = [info for info in clip_infos if id(info) in selected_set]
            new_total = sum(float(info['output_duration_frac']) for info in clip_infos)

            # Find cutoff score
            scores = sorted([c['_quality_score'] for c in clip_infos])
            cutoff = scores[0] if scores else 0

            print(f"\n🧠 Smart selection: {current_total/60:.1f} min → {new_total/60:.1f} min "
                  f"(budget {content_budget/60:.1f} min, overhead ~{overhead_sec/60:.1f} min)")
            print(f"   {len(clip_infos)} clips kept from {len(guaranteed) + len(remaining_pool)}, "
                  f"cutoff score {cutoff:.2f}, "
                  f"{len(videos_clips)} videos represented (min {min_per_video_sec}s each)")

            # Clean up temp scores
            for info in clip_infos:
                info.pop('_quality_score', None)

    intro_path = timeline_config.get('intro_clip')
    outro_path = timeline_config.get('outro_clip')
    copy_intro_outro = timeline_config.get('copy_intro_outro_to_clips_dir', True)
    intro_used_path = None
    outro_used_path = None
    primary_render_dir = None
    if clip_base_dir and analyses:
        first_video_stem = Path(analyses[0]['video_name']).stem
        candidate_dir = Path(clip_base_dir) / first_video_stem
        primary_render_dir = candidate_dir if candidate_dir.exists() else Path(clip_base_dir)
    
    # Collect teaser clips (showcases + interesting scenes)
    teaser_clips = []
    teaser_enabled = timeline_config.get('teaser_enabled', True)
    teaser_max_duration = timeline_config.get('teaser_max_duration', 45.0)  # 30-50 seconds
    
    if teaser_enabled and clip_base_dir:
        # First, collect showcases organized by video index (for beginning/mid/end selection)
        video_showcases = []  # List of (video_index, showcase_data)
        
        for video_index, analysis in enumerate(analyses):
            data = analysis['data']
            video_name = analysis['video_name']
            video_stem = Path(video_name).stem
            render_dir = Path(clip_base_dir) / video_stem
            
            # Collect showcase moments
            showcases = data.get('showcases', [])
            
            # Calculate video quality score based on interesting scenes
            total_scenes = len(data.get('scenes', []))
            interesting_scenes = sum(1 for s in data.get('scenes', []) if s.get('classification') == 'interesting')
            video_quality_ratio = interesting_scenes / max(total_scenes, 1)
            video_quality_bonus = video_quality_ratio * 3  # 0-3 bonus
            
            # Only include showcases from videos with some interesting content
            # Skip videos with 0 interesting scenes unless they have very few total scenes
            if interesting_scenes == 0 and total_scenes > 3:
                continue  # Skip boring videos entirely
            
            for idx, showcase in enumerate(showcases):
                timestamp = showcase['timestamp']
                # Showcase clips are named: {stem}_showcase_{num}_{timestamp}s_1.00x.{ext}
                clip_path = None
                if render_dir.exists():
                    for ext in clip_exts:
                        showcase_pattern = f"{video_stem}_showcase_*_{timestamp}s_1.00x{ext}"
                        matches = list(render_dir.glob(showcase_pattern))
                        if matches:
                            clip_path = matches[0]
                            break
                if clip_path:
                    # Rate showcases: earlier showcases get higher rating (first is best)
                    # Base rating 10, minus position penalty, plus video quality bonus
                    showcase_rating = 10.0 - (idx * 1.0) + video_quality_bonus
                    
                    # Only include high-quality showcases (rating >= 9.0) or first showcase from good videos
                    if showcase_rating < 9.0 and video_quality_ratio < 0.15:
                        continue  # Skip low-rated showcases from mediocre videos
                    
                    video_showcases.append((video_index, {
                        'path': clip_path,
                        'type': 'showcase',
                        'rating': showcase_rating,
                        'video': video_name
                    }))
            
            # Collect interesting scenes
            scenes = data.get('scenes', [])
            for scene in scenes:
                if scene.get('classification') == 'interesting':
                    scene_num = scene['scene_num']
                    speed = scene['speed']
                    rendered_path = find_rendered_clip(render_dir, video_stem, scene_num, 'interesting', speed, extensions=clip_exts)
                    if rendered_path and rendered_path.exists():
                        llm_rating = scene.get('llm_rating', 8)
                        video_showcases.append((video_index, {
                            'path': rendered_path,
                            'type': 'interesting',
                            'rating': llm_rating,
                            'video': video_name
                        }))
        
        # Add teaser-videos to the pool BEFORE selecting
        paths_cfg = config.get('paths', {})
        teaser_videos_dir = paths_cfg.get('teaser_videos')
        
        if teaser_videos_dir:
            teaser_videos_path = Path(teaser_videos_dir).expanduser().resolve()
            if teaser_videos_path.exists() and teaser_videos_path.is_dir():
                video_extensions = {'.mov', '.mp4', '.mkv', '.avi', '.m4v'}
                teaser_video_files = sorted([
                    f for f in teaser_videos_path.iterdir()
                    if f.is_file() and f.suffix.lower() in video_extensions
                ], key=lambda p: p.name.lower())
                
                for teaser_video_file in teaser_video_files:
                    # Add to video_showcases with high rating and video_index 0 (beginning)
                    clip_duration_frac = get_video_duration_frac(str(teaser_video_file), fps=fps)
                    video_showcases.append((0, {
                        'path': teaser_video_file,
                        'type': 'teaser_video',
                        'rating': 11.0,  # HIGHER than showcase rating (10) to sort first
                        'video': teaser_video_file.name
                    }))
        
        # Divide videos into 3 sections: beginning, middle, end
        total_videos = len(analyses)
        section_size = max(1, total_videos // 3)
        
        beginning_indices = set(range(0, section_size))
        middle_indices = set(range(section_size, 2 * section_size))
        end_indices = set(range(2 * section_size, total_videos))
        
        # Organize showcases by section
        section_showcases = {
            'beginning': [],
            'middle': [],
            'end': []
        }
        
        for video_index, showcase_data in video_showcases:
            if video_index in beginning_indices:
                section_showcases['beginning'].append(showcase_data)
            elif video_index in middle_indices:
                section_showcases['middle'].append(showcase_data)
            else:
                section_showcases['end'].append(showcase_data)
        
        # Sort each section by rating, with optional shuffle jitter for variety
        teaser_shuffle_seed = timeline_config.get('teaser_shuffle_seed')
        teaser_shuffle_jitter = timeline_config.get('teaser_shuffle_jitter', 0.4)
        try:
            teaser_shuffle_jitter = float(teaser_shuffle_jitter)
        except (TypeError, ValueError):
            teaser_shuffle_jitter = 0.4
        rng = random.Random(teaser_shuffle_seed) if teaser_shuffle_seed is not None else random.Random()

        def _sort_key(item):
            if item.get('type') == 'teaser_video':
                return item['rating']
            return item['rating'] + rng.uniform(-teaser_shuffle_jitter, teaser_shuffle_jitter)

        for section in section_showcases:
            section_showcases[section].sort(key=_sort_key, reverse=True)
        
        # Select clips from each section proportionally
        selected_teasers = []
        total_duration = 0.0
        used_showcase_videos = set()
        
        # Allocate duration budget: 40% beginning, 30% middle, 30% end
        budget_beginning = teaser_max_duration * 0.4
        budget_middle = teaser_max_duration * 0.3
        budget_end = teaser_max_duration * 0.3
        
        # Select from beginning
        for clip in section_showcases['beginning']:
            if total_duration >= budget_beginning:
                break
            clip_video = clip.get('video')
            if clip.get('type') != 'teaser_video' and clip_video in used_showcase_videos:
                continue
            clip_duration_frac = get_media_duration_frac(str(clip['path']), fps=fps)
            clip_duration_sec = float(clip_duration_frac)
            
            if total_duration + clip_duration_sec <= budget_beginning:
                selected_teasers.append(clip)
                if clip.get('type') != 'teaser_video' and clip_video:
                    used_showcase_videos.add(clip_video)
                total_duration += clip_duration_sec
        
        # Select from middle
        for clip in section_showcases['middle']:
            if total_duration >= budget_beginning + budget_middle:
                break
            clip_video = clip.get('video')
            if clip.get('type') != 'teaser_video' and clip_video in used_showcase_videos:
                continue
            clip_duration_frac = get_media_duration_frac(str(clip['path']), fps=fps)
            clip_duration_sec = float(clip_duration_frac)
            
            if total_duration + clip_duration_sec <= budget_beginning + budget_middle:
                selected_teasers.append(clip)
                if clip.get('type') != 'teaser_video' and clip_video:
                    used_showcase_videos.add(clip_video)
                total_duration += clip_duration_sec
        
        # Select from end
        for clip in section_showcases['end']:
            if total_duration >= teaser_max_duration:
                break
            clip_video = clip.get('video')
            if clip.get('type') != 'teaser_video' and clip_video in used_showcase_videos:
                continue
            clip_duration_frac = get_media_duration_frac(str(clip['path']), fps=fps)
            clip_duration_sec = float(clip_duration_frac)
            
            if total_duration + clip_duration_sec <= teaser_max_duration:
                selected_teasers.append(clip)
                if clip.get('type') != 'teaser_video' and clip_video:
                    used_showcase_videos.add(clip_video)
                total_duration += clip_duration_sec
        
        if selected_teasers:
            teaser_videos_count = sum(1 for c in selected_teasers if c.get('type') == 'teaser_video')
            showcases_count = len(selected_teasers) - teaser_videos_count
            print(f"\n🎬 Building teaser: {len(selected_teasers)} clips ({total_duration:.1f}s) - {showcases_count} showcases + {teaser_videos_count} teaser-videos (highest-ranked first)")
    
    # Insert teaser clips first (before intro)
    teaser_insert_pos = 0
    if teaser_enabled and selected_teasers:
        for teaser in selected_teasers:
            teaser_type = teaser.get('type', 'showcase')
            teaser_path = teaser['path']
            
            if teaser_type == 'teaser_video':
                # For teaser videos, process them like static clips but with teaser classification
                rotation = video_rotations.get(teaser['video'], 0)
                video_rotations[teaser_path.name] = rotation
                duration_frac = get_media_duration_frac(str(teaser_path), fps=fps)
                teaser_info = {
                    'scene': {
                        'start_time': 0.0,
                        'end_time': float(duration_frac),
                        'duration': float(duration_frac),
                        'speed': 1.0,
                        'classification': 'teaser'
                    },
                    'video_name': teaser_path.name,
                    'video_path': str(teaser_path),
                    'output_duration_frac': duration_frac,
                    'use_rendered': True,
                    'src_path': str(teaser_path),
                    'src_name': teaser_path.name,
                    'src_duration_frac': duration_frac,
                    'rotation': rotation
                }
                clip_infos.insert(teaser_insert_pos, teaser_info)
                teaser_insert_pos += 1
            else:
                # For showcase/interesting clips, use direct timeline info
                original_video_name = teaser['video']
                rotation = video_rotations.get(original_video_name, 0)
                video_rotations[teaser_path.name] = rotation
                duration_frac = get_media_duration_frac(str(teaser_path), fps=fps)
                teaser_info = {
                    'scene': {
                        'start_time': 0.0,
                        'end_time': float(duration_frac),
                        'duration': float(duration_frac),
                        'speed': 1.0,
                        'classification': 'teaser'
                    },
                    'video_name': teaser_path.name,
                    'video_path': str(teaser_path),
                    'output_duration_frac': duration_frac,
                    'use_rendered': True,
                    'src_path': str(teaser_path),
                    'src_name': teaser_path.name,
                    'src_duration_frac': duration_frac,
                    'rotation': rotation
                }
                clip_infos.insert(teaser_insert_pos, teaser_info)
                teaser_insert_pos += 1
    
    # Insert intro after teaser
    if intro_path:
        intro_info = build_static_clip_info(
            intro_path,
            'intro',
            clip_base_dir,
            preferred_dir=primary_render_dir,
            copy_to_clips_dir=copy_intro_outro
        )
        if intro_info:
            intro_used_path = intro_info['src_path']
            clip_infos.insert(teaser_insert_pos, intro_info)
    
    if outro_path:
        # Before adding outro, scan for photos and teaser-videos
        paths_cfg = config.get('paths', {})
        photos_dir = paths_cfg.get('photos')
        teaser_videos_dir = paths_cfg.get('teaser_videos')
        
        closing_section_clips = []
        
        # Scan teaser-videos directory FIRST (before photos)
        if teaser_videos_dir:
            teaser_videos_path = Path(teaser_videos_dir).expanduser().resolve()
            if teaser_videos_path.exists() and teaser_videos_path.is_dir():
                # Look for video files
                video_extensions = {'.mov', '.mp4', '.mkv', '.avi', '.m4v'}
                teaser_video_files = sorted([
                    f for f in teaser_videos_path.iterdir()
                    if f.is_file() and f.suffix.lower() in video_extensions
                ], key=lambda p: p.name.lower())
                
                for teaser_video_file in teaser_video_files:
                    teaser_info = build_static_clip_info(
                        teaser_video_file,
                        'closing_teaser',
                        clip_base_dir,
                        preferred_dir=primary_render_dir,
                        copy_to_clips_dir=copy_intro_outro
                    )
                    if teaser_info:
                        closing_section_clips.append(teaser_info)
        
        # Scan photos directory (after teaser-videos)
        if photos_dir:
            photos_path = Path(photos_dir).expanduser().resolve()
            if photos_path.exists() and photos_path.is_dir():
                # Look for image files (jpg, jpeg, png, etc.)
                photo_extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif'}
                photo_files = sorted([
                    f for f in photos_path.iterdir()
                    if f.is_file() and f.suffix.lower() in photo_extensions
                ], key=lambda p: p.name.lower())
                
                for photo_file in photo_files:
                    photo_info = build_static_clip_info(
                        photo_file,
                        'closing_photo',
                        clip_base_dir,
                        preferred_dir=primary_render_dir,
                        copy_to_clips_dir=copy_intro_outro
                    )
                    if photo_info:
                        # Set default duration for photos from config
                        closing_photo_config = timeline_config.get('closing_photos', {})
                        photo_duration = closing_photo_config.get('duration_seconds', 3)
                        photo_info['output_duration_frac'] = Fraction(photo_duration, 1)
                        closing_section_clips.append(photo_info)
        
        # Add all closing section clips before outro
        if closing_section_clips:
            print(f"🎬 Adding closing section: {len(closing_section_clips)} photos/teaser-videos")
            clip_infos.extend(closing_section_clips)
        
        # Now add the outro
        outro_info = build_static_clip_info(
            outro_path,
            'outro',
            clip_base_dir,
            preferred_dir=primary_render_dir,
            copy_to_clips_dir=copy_intro_outro
        )
        if outro_info:
            outro_used_path = outro_info['src_path']
            clip_infos.append(outro_info)
    else:
        # No outro, just add closing section if any
        paths_cfg = config.get('paths', {})
        photos_dir = paths_cfg.get('photos')
        teaser_videos_dir = paths_cfg.get('teaser_videos')
        
        closing_section_clips = []
        
        # Scan teaser-videos directory FIRST (before photos)
        if teaser_videos_dir:
            teaser_videos_path = Path(teaser_videos_dir).expanduser().resolve()
            if teaser_videos_path.exists() and teaser_videos_path.is_dir():
                video_extensions = {'.mov', '.mp4', '.mkv', '.avi', '.m4v'}
                teaser_video_files = sorted([
                    f for f in teaser_videos_path.iterdir()
                    if f.is_file() and f.suffix.lower() in video_extensions
                ], key=lambda p: p.name.lower())
                
                for teaser_video_file in teaser_video_files:
                    teaser_info = build_static_clip_info(
                        teaser_video_file,
                        'closing_teaser',
                        clip_base_dir,
                        preferred_dir=primary_render_dir,
                        copy_to_clips_dir=copy_intro_outro
                    )
                    if teaser_info:
                        closing_section_clips.append(teaser_info)
        
        # Scan photos directory (after teaser-videos)
        if photos_dir:
            photos_path = Path(photos_dir).expanduser().resolve()
            if photos_path.exists() and photos_path.is_dir():
                photo_extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif'}
                photo_files = sorted([
                    f for f in photos_path.iterdir()
                    if f.is_file() and f.suffix.lower() in photo_extensions
                ], key=lambda p: p.name.lower())
                
                for photo_file in photo_files:
                    photo_info = build_static_clip_info(
                        photo_file,
                        'closing_photo',
                        clip_base_dir,
                        preferred_dir=primary_render_dir,
                        copy_to_clips_dir=copy_intro_outro
                    )
                    if photo_info:
                        # Set default duration for photos from config
                        closing_photo_config = timeline_config.get('closing_photos', {})
                        photo_duration = closing_photo_config.get('duration_seconds', 3)
                        photo_info['output_duration_frac'] = Fraction(photo_duration, 1)
                        closing_section_clips.append(photo_info)
        
        if closing_section_clips:
            print(f"🎬 Adding closing section: {len(closing_section_clips)} photos/teaser-videos")
            clip_infos.extend(closing_section_clips)

    
    use_rendered_any = any(info['use_rendered'] for info in clip_infos)
    use_rendered_all = all(info['use_rendered'] for info in clip_infos)

    # Calculate timeline duration
    timeline_duration_sec = sum(float(info['output_duration_frac']) for info in clip_infos)
    timeline_duration_frac = Fraction(timeline_duration_sec).limit_denominator(10000)

    watermark_config = timeline_config.get('watermark') or config.get('watermark')
    audio_config = config.get('audio') if isinstance(config.get('audio'), dict) else {}
    background_music_config = (
        timeline_config.get('background_music')
        or audio_config.get('background_music')
        or config.get('background_music')
    )
    teaser_music_config = (
        timeline_config.get('teaser_music')
        or audio_config.get('teaser_music')
        or config.get('teaser_music')
    )
    audio_role = timeline_config.get('audio_role', 'dialogue')
    watermark_asset_id = None
    watermark_position = None
    watermark_opacity = None
    watermark_path = None
    if watermark_config and isinstance(watermark_config, dict):
        watermark_path = watermark_config.get('path')
        transparency = normalize_transparency(watermark_config.get('transparency'))
        if transparency is None:
            transparency = 0.3  # Default 30% transparent = 70% opaque
        watermark_opacity = 1.0 - transparency  # Convert transparency to opacity
        watermark_position = watermark_config.get('position', 'bottom-right')
    
    # FCP XML structure (version 1.13 - DaVinci Resolve format)
    fcpxml = Element('fcpxml', version='1.13')
    
    # Resources section
    resources = SubElement(fcpxml, 'resources')
    
    # Format r0: Timeline format - 4K 24fps
    SubElement(resources, 'format', {
        'name': 'FFVideoFormat3840x2160p2398',
        'height': '2160',
        'id': 'r0',
        'frameDuration': '1001/24000s',
        'width': '3840'
    })
    
    # Format r2: Source video format
    SubElement(resources, 'format', {
        'name': 'FFVideoFormatRateUndefined',
        'height': '2160',
        'id': 'r2',
        'frameDuration': '1/24s',
        'width': '3840'
    })
    
    # Cross Dissolve effect (r1)
    SubElement(resources, 'effect', {
        'name': 'Cross Dissolve',
        'id': 'r1',
        'uid': 'FxPlug:4731E73A-8DAC-4113-9A30-AE85B1761265'
    })
    
    asset_counter = 3
    media_by_video = {}

    if resolve_format:
        # Create media resources per source video (Resolve-style)
        for video_name in sorted({info['video_name'] for info in clip_infos if not info['use_rendered']}):
            video_path = next(info['video_path'] for info in clip_infos if info['video_name'] == video_name)
            duration_frac = next(info['src_duration_frac'] for info in clip_infos if info['video_name'] == video_name)

            video_asset_id = f'r{asset_counter}'
            asset_counter += 1
            audio_asset_id = f'r{asset_counter}'
            asset_counter += 1
            media_id = f'r{asset_counter}'
            asset_counter += 1

            asset_video = SubElement(resources, 'asset', {
                'name': video_name,
                'start': '0/1s',
                'hasAudio': '0',
                'id': video_asset_id,
                'uid': to_file_uri(video_path, clip_base_dir),
                'duration': f"{duration_frac.numerator}/{duration_frac.denominator}s",
                'hasVideo': '1',
                'audioSources': '0',
                'format': 'r2',
                'audioChannels': '0'
            })
            SubElement(asset_video, 'media-rep', {
                'src': to_file_uri(video_path, clip_base_dir),
                'kind': 'original-media'
            })

            asset_audio = SubElement(resources, 'asset', {
                'name': video_name,
                'start': '0/1s',
                'hasAudio': '1',
                'id': audio_asset_id,
                'uid': to_file_uri(video_path, clip_base_dir),
                'duration': f"{duration_frac.numerator}/{duration_frac.denominator}s",
                'hasVideo': '0',
                'audioSources': '1',
                'audioChannels': '1'
            })
            SubElement(asset_audio, 'media-rep', {
                'src': to_file_uri(video_path, clip_base_dir),
                'kind': 'original-media'
            })

            media = SubElement(resources, 'media', {
                'id': media_id,
                'name': video_name
            })
            media_seq = SubElement(media, 'sequence', {
                'tcStart': '0/1s',
                'tcFormat': 'NDF',
                'format': 'r2',
                'duration': f"{duration_frac.numerator}/{duration_frac.denominator}s"
            })
            media_spine = SubElement(media_seq, 'spine')
            media_clip = SubElement(media_spine, 'clip', {
                'enabled': '1',
                'tcFormat': 'NDF',
                'start': '0/1s',
                'format': 'r2',
                'offset': '0/1s',
                'name': video_name,
                'duration': f"{duration_frac.numerator}/{duration_frac.denominator}s"
            })
            media_transform = {
                'position': '0 0',
                'anchor': '0 0',
                'scale': '1 1'
            }
            rotation = video_rotations.get(video_name, 0)
            if rotation:
                media_transform['rotation'] = str(rotation)
                zoom = timeline_config.get('rotation_zoom', 1.78)
                media_transform['scale'] = f"{zoom} {zoom}"
            SubElement(media_clip, 'adjust-transform', media_transform)
            SubElement(media_clip, 'video', {
                'ref': video_asset_id,
                'start': '0/1s',
                'offset': '0/1s',
                'duration': f"{duration_frac.numerator}/{duration_frac.denominator}s"
            })
            media_by_video[video_name] = {
                'media_id': media_id,
                'video_asset_id': video_asset_id,
                'audio_asset_id': audio_asset_id
            }

    # Create separate asset for rendered clips (or legacy mode)
    legacy_asset_map = {}
    for clip_info in clip_infos:
        if resolve_format and not clip_info['use_rendered'] and not clip_info.get('force_asset_clip'):
            continue
        asset_id = f'r{asset_counter}'
        asset_counter += 1
        # Use r0 (proper 23.98fps) for video clips, r2 for static images
        is_image = clip_info['src_name'].lower().endswith(('.jpg', '.jpeg', '.png', '.tiff', '.gif'))
        asset_format = 'r2' if is_image else 'r0'
        asset = SubElement(resources, 'asset', {
            'name': clip_info['src_name'],
            'start': '0/1s',
            'hasAudio': '1',
            'id': asset_id,
            'uid': to_file_uri(clip_info['src_path'], clip_base_dir),
            'duration': f"{clip_info['src_duration_frac'].numerator}/{clip_info['src_duration_frac'].denominator}s",
            'hasVideo': '1',
            'audioSources': '1',
            'format': asset_format,
            'audioChannels': '2'
        })
        SubElement(asset, 'media-rep', {
            'src': to_file_uri(clip_info['src_path'], clip_base_dir),
            'kind': 'original-media'
        })
        legacy_asset_map[id(clip_info)] = asset_id

    if watermark_path:
        watermark_path = str(Path(watermark_path).expanduser().resolve())
        if Path(watermark_path).exists():
            watermark_asset_id = f'r{asset_counter}'
            asset_counter += 1
            watermark_asset = SubElement(resources, 'asset', {
                'name': Path(watermark_path).name,
                'start': '0/1s',
                'hasAudio': '0',
                'id': watermark_asset_id,
                'uid': to_file_uri(watermark_path),
                'duration': f"{timeline_duration_frac.numerator}/{timeline_duration_frac.denominator}s",
                'hasVideo': '1',
                'audioSources': '0',
                'format': 'r2',
                'audioChannels': '0'
            })
            SubElement(watermark_asset, 'media-rep', {
                'src': to_file_uri(watermark_path),
                'kind': 'original-media'
            })
        else:
            print(f"⚠️  Missing watermark image: {watermark_path}")

    music_assets = []
    if background_music_config and isinstance(background_music_config, dict):
        music_folder = background_music_config.get('folder')
        if music_folder:
            music_dir = Path(music_folder).expanduser().resolve()
            if music_dir.exists() and music_dir.is_dir():
                print("\n🔍 Pre-checking background music files...")
                normalize_audio_folder(music_dir)
                music_files = sorted(music_dir.glob('*.wav'))
                if not music_files:
                    print(f"⚠️  No WAV files found in music folder: {music_dir}")
                else:
                    for music_path in music_files:
                        duration_frac, channels, _ = get_audio_info(str(music_path))
                        if not duration_frac:
                            print(f"⚠️  Skipping audio (unknown duration): {music_path}")
                            continue
                        asset_id = f'r{asset_counter}'
                        asset_counter += 1
                        asset = SubElement(resources, 'asset', {
                            'name': music_path.name,
                            'start': '0/1s',
                            'hasAudio': '1',
                            'id': asset_id,
                            'uid': to_file_uri(str(music_path)),
                            'duration': f"{duration_frac.numerator}/{duration_frac.denominator}s",
                            'hasVideo': '0',
                            'audioSources': '1',
                            'audioChannels': str(channels)
                        })
                        SubElement(asset, 'media-rep', {
                            'src': to_file_uri(str(music_path)),
                            'kind': 'original-media'
                        })
                        music_assets.append({
                            'asset_id': asset_id,
                            'path': str(music_path),
                            'duration': duration_frac
                        })
            else:
                print(f"⚠️  Music folder not found: {music_dir}")
    
    # Load teaser music assets
    teaser_music_assets = []
    if teaser_music_config and isinstance(teaser_music_config, dict):
        teaser_music_folder = teaser_music_config.get('folder')
        if teaser_music_folder:
            teaser_music_dir = Path(teaser_music_folder).expanduser().resolve()
            if teaser_music_dir.exists() and teaser_music_dir.is_dir():
                print("\n🔍 Pre-checking teaser music files...")
                normalize_audio_folder(teaser_music_dir)
                teaser_music_files = sorted(teaser_music_dir.glob('*.wav'))
                if not teaser_music_files:
                    print(f"⚠️  No WAV files found in teaser music folder: {teaser_music_dir}")
                else:
                    for teaser_music_path in teaser_music_files:
                        duration_frac, channels, _ = get_audio_info(str(teaser_music_path))
                        if not duration_frac:
                            print(f"⚠️  Skipping teaser audio (unknown duration): {teaser_music_path}")
                            continue
                        asset_id = f'r{asset_counter}'
                        asset_counter += 1
                        asset = SubElement(resources, 'asset', {
                            'name': teaser_music_path.name,
                            'start': '0/1s',
                            'hasAudio': '1',
                            'id': asset_id,
                            'uid': to_file_uri(str(teaser_music_path)),
                            'duration': f"{duration_frac.numerator}/{duration_frac.denominator}s",
                            'hasVideo': '0',
                            'audioSources': '1',
                            'audioChannels': str(channels)
                        })
                        SubElement(asset, 'media-rep', {
                            'src': to_file_uri(str(teaser_music_path)),
                            'kind': 'original-media'
                        })
                        teaser_music_assets.append({
                            'asset_id': asset_id,
                            'path': str(teaser_music_path),
                            'duration': duration_frac
                        })
            else:
                print(f"⚠️  Teaser music folder not found: {teaser_music_dir}")
    
    # Library structure
    library = SubElement(fcpxml, 'library')
    event = SubElement(library, 'event', name='Timeline 1 (Resolve)')
    project = SubElement(event, 'project', name='Timeline 1 (Resolve)')
    
    sequence = SubElement(project, 'sequence', {
        'tcStart': '0/1s',
        'duration': f'{timeline_duration_frac.numerator}/{timeline_duration_frac.denominator}s',
        'format': 'r0',
        'tcFormat': 'NDF'
    })
    
    spine = SubElement(sequence, 'spine')

    watermark_lane = None
    main_video_lane = None
    try:
        watermark_lane = int(
            watermark_config.get('lane', timeline_config.get('watermark_lane', 2))
            if watermark_config else timeline_config.get('watermark_lane', 2)
        )
    except (TypeError, ValueError):
        watermark_lane = 1
    try:
        main_video_lane = int(timeline_config.get('main_video_lane', 1))
    except (TypeError, ValueError):
        main_video_lane = 2

    # Add clips to timeline
    print(f'🎬 Creating DaVinci Resolve Timeline...')
    video_names = sorted({info['video_name'] for info in clip_infos})
    if len(video_names) == 1:
        print(f'   Original video: {video_names[0]}')
    else:
        print(f'   Videos: {len(video_names)} ({", ".join(video_names)})')
    print(f'   Timeline: {len(clip_infos)} clips')
    print()
    
    timeline_pos_sec = Fraction(0, 1)
    _FRAC_LIMIT = 1_000_000  # keep fraction denominators manageable
    
    transition_seconds = timeline_config.get('transition_duration', None)
    transition_duration = None
    if transition_seconds is None:
        transition_duration = Fraction(1001, 1000)
    else:
        try:
            transition_seconds = float(transition_seconds)
        except (TypeError, ValueError):
            transition_seconds = None
        if transition_seconds and transition_seconds > 0:
            transition_duration = Fraction(transition_seconds).limit_denominator(10000)
    transition_half = transition_duration / 2 if transition_duration else None
    clip_timeline_ranges = []
    timeline_cursor = Fraction(0, 1)
    for idx, info in enumerate(clip_infos):
        clip_start = timeline_cursor
        clip_end = timeline_cursor + info['output_duration_frac']
        clip_timeline_ranges.append((clip_start, clip_end))
        if transition_half and idx < len(clip_infos) - 1:
            timeline_cursor = (clip_end - transition_half).limit_denominator(_FRAC_LIMIT)
        else:
            timeline_cursor = clip_end.limit_denominator(_FRAC_LIMIT)
    snippet_volume_db = timeline_config.get(
        'snippet_audio_volume_db',
        audio_config.get('snippet_audio_volume_db', None)
    )
    try:
        snippet_volume_db = float(snippet_volume_db) if snippet_volume_db is not None else None
    except (TypeError, ValueError):
        snippet_volume_db = None
    
    for i, clip_info in enumerate(clip_infos, 1):
        scene = clip_info['scene']
        start_sec = scene['start_time']
        end_sec = scene['end_time']
        duration_sec = scene['duration']
        speed = scene['speed']
        classification = scene.get('classification', 'unknown')
        output_duration_frac = clip_info['output_duration_frac']
        output_duration_sec = float(output_duration_frac)
        
        # Round to frame boundaries (24fps) to avoid fractional frames
        fps = 24
        duration_frames = round(duration_sec * fps)
        duration_frac = Fraction(duration_frames, fps)
        
        clip_start_frac = Fraction(0, 1) if clip_info['use_rendered'] else Fraction(start_sec).limit_denominator(10000)
        clip_name = clip_info['src_name'] if clip_info['use_rendered'] else clip_info['video_name']

        clip_offset = timeline_pos_sec
        audio_start_frac = clip_start_frac
        audio_duration_frac = output_duration_frac
        if resolve_format and not clip_info['use_rendered'] and not clip_info.get('force_asset_clip'):
            media_ref = media_by_video[clip_info['video_name']]['media_id']
            audio_ref = media_by_video[clip_info['video_name']]['audio_asset_id']
            clip = SubElement(spine, 'ref-clip', {
                'name': clip_name,
                'ref': media_ref,
                'start': f'{clip_start_frac.numerator}/{clip_start_frac.denominator}s',
                'offset': f'{clip_offset.numerator}/{clip_offset.denominator}s',
                'duration': f'{output_duration_frac.numerator}/{output_duration_frac.denominator}s',
                'lane': str(main_video_lane),
                'audioStart': f'{audio_start_frac.numerator}/{audio_start_frac.denominator}s',
                'audioDuration': f'{audio_duration_frac.numerator}/{audio_duration_frac.denominator}s'
            })
            SubElement(clip, 'conform-rate', {'srcFrameRate': '24'})
            if abs(speed - 1.0) > 0.01:
                frame_epsilon = Fraction(1, fps)
                handle_sec = 45
                handle_frac = Fraction(round(handle_sec * fps), fps)
                available_source = clip_info['src_duration_frac'] - clip_start_frac
                max_source = available_source - handle_frac
                effective_duration_frac = duration_frac
                if max_source > frame_epsilon and duration_frac >= max_source:
                    effective_duration_frac = max_source
                elif available_source > frame_epsilon and duration_frac >= available_source:
                    effective_duration_frac = available_source - frame_epsilon

                timemap = SubElement(clip, 'timeMap', {'frameSampling': 'floor'})
                # Start point
                SubElement(timemap, 'timept', {
                    'time': '0/1s', 'interp': 'linear', 'value': '0/1s'
                })

                # End point
                SubElement(timemap, 'timept', {
                    'time': f'{output_duration_frac.numerator}/{output_duration_frac.denominator}s',
                    'interp': 'linear',
                    'value': f'{effective_duration_frac.numerator}/{effective_duration_frac.denominator}s'
                })
            SubElement(clip, 'adjust-conform', {'type': 'fit'})
        else:
            asset_ref = legacy_asset_map[id(clip_info)]
            # Use r0 (proper 23.98fps) for video clips, r2 for static images
            is_image = clip_info['src_name'].lower().endswith(('.jpg', '.jpeg', '.png', '.tiff', '.gif'))
            clip_format = 'r2' if is_image else 'r0'
            clip_attrs = {
                'name': clip_name,
                'ref': asset_ref,
                'start': f'{clip_start_frac.numerator}/{clip_start_frac.denominator}s',
                'offset': f'{clip_offset.numerator}/{clip_offset.denominator}s',
                'duration': f'{output_duration_frac.numerator}/{output_duration_frac.denominator}s',
                'format': clip_format,
                'enabled': '1',
                'tcFormat': 'NDF',
                'lane': str(main_video_lane),
                'audioStart': f'{audio_start_frac.numerator}/{audio_start_frac.denominator}s',
                'audioDuration': f'{audio_duration_frac.numerator}/{audio_duration_frac.denominator}s'
            }
            clip = SubElement(spine, 'asset-clip', clip_attrs)
            
            # IMPORTANT: For rendered/pre-processed clips, DO NOT add conform-rate or adjust-transform
            # These elements force DaVinci to wrap clips as compounds instead of native media,
            # preventing LUT application and proper codec recognition.
            # Rendered clips already have rotation/transforms baked in during extraction.
            if not clip_info['use_rendered']:
                # Only add conform-rate for source clips that need frame rate adjustment
                SubElement(clip, 'conform-rate', {'srcFrameRate': '24'})
                
                # Only add timeMap for speed adjustments on source clips
                if abs(speed - 1.0) > 0.01:
                    timemap = SubElement(clip, 'timeMap', {'frameSampling': 'floor'})
                    SubElement(timemap, 'timept', {
                        'time': '0/1s',
                        'interp': 'linear',
                        'value': '0/1s'
                    })
                    SubElement(timemap, 'timept', {
                        'time': f'{output_duration_frac.numerator}/{output_duration_frac.denominator}s',
                        'interp': 'linear',
                        'value': f'{duration_frac.numerator}/{duration_frac.denominator}s'
                    })
        
        # Add adjust-volume to clips that need audio adjustment
        if snippet_volume_db is not None:
            clip_class = scene.get('classification')
            # Mute all clips except intro/outro (includes opening teaser/showcase clips before Start-Intro)
            if clip_class not in ('intro', 'outro'):
                SubElement(clip, 'adjust-volume', {
                    'amount': f"{snippet_volume_db}dB"
                })

        # Add adjust-transform for clips that need rotation/zoom adjustment
        if True:
            clip_transform = {
                'position': '0 0',
                'scale': '1 1',
                'anchor': '0 0'
            }
            if clip_info.get('rotation'):
                clip_transform['rotation'] = str(clip_info['rotation'])
                zoom = timeline_config.get('rotation_zoom', 1.78)
                clip_transform['scale'] = f"{zoom} {zoom}"
            # Add zoom for photos
            if classification == 'closing_photo':
                closing_photo_config = timeline_config.get('closing_photos', {})
                photo_zoom = closing_photo_config.get('zoom', 1.350)
                clip_transform['scale'] = f"{photo_zoom} {photo_zoom}"
            SubElement(clip, 'adjust-transform', clip_transform)

        
        print(f'   Clip {i:02d}: {clip_info["video_name"]} {start_sec:7.1f}s-{end_sec:7.1f}s ({duration_sec:6.1f}s) @ {speed:.1f}x ({speed*100:.0f}%) → {output_duration_sec:6.1f}s [{classification}]')
        
        # Move timeline position forward
        timeline_pos_sec = (timeline_pos_sec + output_duration_frac).limit_denominator(_FRAC_LIMIT)
        
        # Add transition to next clip (except for last clip) with overlap
        if transition_duration and i < len(clip_infos):
            transition_offset = timeline_pos_sec - transition_half
            transition = SubElement(spine, 'transition', {
                'name': 'Transition',
                'offset': f'{transition_offset.numerator}/{transition_offset.denominator}s',
                'duration': f'{transition_duration.numerator}/{transition_duration.denominator}s'
            })
            SubElement(transition, 'filter-video', {'name': 'Transition', 'ref': 'r1'})
            SubElement(transition, 'filter-audio', {'name': 'Transition', 'ref': 'r1'})
            timeline_pos_sec = (timeline_pos_sec - transition_half).limit_denominator(_FRAC_LIMIT)

    if watermark_asset_id:
        watermark_size = get_media_dimensions(watermark_path)
        watermark_margin = watermark_config.get('margin', timeline_config.get('watermark_margin', DEFAULT_WATERMARK_MARGIN))
        watermark_width = watermark_config.get('timeline_width', timeline_config.get('timeline_width', DEFAULT_TIMELINE_WIDTH))
        watermark_height = watermark_config.get('timeline_height', timeline_config.get('timeline_height', DEFAULT_TIMELINE_HEIGHT))
        watermark_scale = watermark_config.get('scale', timeline_config.get('watermark_scale', 1.0))
        position_scale_with_zoom = watermark_config.get(
            'position_scale_with_zoom',
            timeline_config.get('watermark_position_scale_with_zoom', True)
        )
        position_multiplier = watermark_config.get(
            'position_multiplier',
            timeline_config.get('watermark_position_multiplier', 1.0)
        )
        if position_scale_with_zoom and isinstance(watermark_position, dict):
            if 'x' in watermark_position and 'y' in watermark_position:
                try:
                    watermark_position = {
                        'x': float(watermark_position['x']) * watermark_scale,
                        'y': float(watermark_position['y']) * watermark_scale
                    }
                except (TypeError, ValueError):
                    pass
        if isinstance(watermark_position, dict) and 'x' in watermark_position and 'y' in watermark_position:
            try:
                watermark_position = {
                    'x': float(watermark_position['x']) * position_multiplier,
                    'y': float(watermark_position['y']) * position_multiplier
                }
            except (TypeError, ValueError):
                pass
        if watermark_size:
            scaled_size = (int(watermark_size[0] * watermark_scale), int(watermark_size[1] * watermark_scale))
        else:
            scaled_size = None
        watermark_position_str = compute_watermark_position(
            watermark_position,
            scaled_size,
            margin=watermark_margin,
            timeline_width=watermark_width,
            timeline_height=watermark_height
        )
        watermark_clip = SubElement(spine, 'asset-clip', {
            'name': Path(watermark_path).name,
            'ref': watermark_asset_id,
            'start': '0/1s',
            'offset': '0/1s',
            'duration': f"{timeline_pos_sec.numerator}/{timeline_pos_sec.denominator}s",
            'format': 'r2',
            'enabled': '1',
            'tcFormat': 'NDF',
            'lane': str(watermark_lane)
        })
        SubElement(watermark_clip, 'adjust-transform', {
            'position': watermark_position_str,
            'scale': f"{watermark_scale} {watermark_scale}",
            'anchor': '0 0'
        })
        SubElement(watermark_clip, 'adjust-blend', {
            'amount': f"{watermark_opacity:.3f}",
            'opacity': f"{watermark_opacity:.3f}"
        })

    if music_assets:
        try:
            music_lane = int(background_music_config.get('audio_lane', 2))
        except (TypeError, ValueError):
            music_lane = 2
        fade_seconds = background_music_config.get('fade_duration', 1.0)
        fade_in_seconds = background_music_config.get('fade_in_duration', fade_seconds)
        fade_out_seconds = background_music_config.get('fade_out_duration', fade_seconds)
        fade_silence_db = background_music_config.get('fade_silence_db', -96.0)

        def _to_duration(value):
            try:
                value = float(value)
            except (TypeError, ValueError):
                return None
            if value <= 0:
                return None
            return Fraction(value).limit_denominator(10000)

        fade_duration = _to_duration(fade_seconds)
        fade_in_duration = _to_duration(fade_in_seconds)
        fade_out_duration = _to_duration(fade_out_seconds)
        seed = background_music_config.get('random_seed')
        rng = random.Random(seed) if seed is not None else random.Random()
        shuffled_music = music_assets[:]
        rng.shuffle(shuffled_music)
        def _fmt_time(value):
            if value is None:
                return None
            return f"{value.numerator}/{value.denominator}s"
        if shuffled_music:
            music_start = Fraction(0, 1)
            music_end = timeline_duration_frac
            intro_end = None
            outro_start = None
            closing_section_start = None
            closing_section_end = None
            
            for info, (clip_start, clip_end) in zip(clip_infos, clip_timeline_ranges):
                classification = info['scene'].get('classification')
                if classification == 'intro' and intro_end is None:
                    intro_end = clip_end
                if classification in ('closing_photo', 'closing_teaser') and closing_section_start is None:
                    closing_section_start = clip_start
                if classification in ('closing_photo', 'closing_teaser'):
                    closing_section_end = clip_end
                if classification == 'outro' and outro_start is None:
                    outro_start = clip_start
            
            if intro_end is not None:
                music_start = intro_end
            if closing_section_start is not None:
                # Background music ends when closing section starts
                music_end = closing_section_start
            elif outro_start is not None:
                music_end = outro_start
            if music_end < music_start:
                music_end = music_start

            music_pos = music_start
            music_index = 0
            while music_pos < music_end:
                track = shuffled_music[music_index % len(shuffled_music)]
                remaining = music_end - music_pos
                clip_duration = track['duration'] if track['duration'] <= remaining else remaining
                if clip_duration <= 0:
                    break
                # Simplify fractions to avoid precision issues with large numbers
                clip_start = music_pos.limit_denominator(10000)
                clip_duration = clip_duration.limit_denominator(10000)
                if clip_duration <= 0:
                    break  # limit_denominator can round tiny remainders to 0
                attributes = {
                    'name': Path(track['path']).name,
                    'ref': track['asset_id'],
                    'start': '0/1s',
                    'offset': f"{clip_start.numerator}/{clip_start.denominator}s",
                    'duration': f"{clip_duration.numerator}/{clip_duration.denominator}s",
                    'enabled': '1',
                    'tcFormat': 'NDF',
                    'lane': str(music_lane),
                    'audioStart': '0/1s',
                    'audioDuration': f"{clip_duration.numerator}/{clip_duration.denominator}s"
                }
                if fade_in_duration:
                    fade_in = fade_in_duration if fade_in_duration <= clip_duration else clip_duration
                    attributes['audioFadeIn'] = _fmt_time(fade_in)
                if fade_out_duration:
                    fade_out = fade_out_duration if fade_out_duration <= clip_duration else clip_duration
                    attributes['audioFadeOut'] = _fmt_time(fade_out)
                music_clip = SubElement(spine, 'asset-clip', attributes)
                SubElement(music_clip, 'adjust-volume', {
                    'amount': '0dB'
                })

                if fade_in_duration or fade_out_duration:
                    fade_in = fade_in_duration if fade_in_duration else None
                    fade_out = fade_out_duration if fade_out_duration else None
                    if fade_in and fade_in > clip_duration:
                        fade_in = clip_duration
                    if fade_out and fade_out > clip_duration:
                        fade_out = clip_duration
                    fade_out_start = clip_duration - fade_out if fade_out else None
                    if fade_out_start is not None and fade_out_start < Fraction(0, 1):
                        fade_out_start = Fraction(0, 1)

                    try:
                        fade_silence_value = f"{float(fade_silence_db)}dB"
                    except (TypeError, ValueError):
                        fade_silence_value = "-96.0dB"
                    fade_full_value = "0dB"

                    audio_automation = SubElement(music_clip, 'audio-automation', {
                        'lane': 'volume'
                    })
                    if fade_in:
                        SubElement(audio_automation, 'keyframe', {
                            'time': '0/1s',
                            'value': fade_silence_value
                        })
                        SubElement(audio_automation, 'keyframe', {
                            'time': _fmt_time(fade_in),
                            'value': fade_full_value
                        })
                    else:
                        SubElement(audio_automation, 'keyframe', {
                            'time': '0/1s',
                            'value': fade_full_value
                        })

                    if fade_out and fade_out_start is not None:
                        SubElement(audio_automation, 'keyframe', {
                            'time': _fmt_time(fade_out_start),
                            'value': fade_full_value
                        })
                        SubElement(audio_automation, 'keyframe', {
                            'time': _fmt_time(clip_duration),
                            'value': fade_silence_value
                        })
                    else:
                        SubElement(audio_automation, 'keyframe', {
                            'time': _fmt_time(clip_duration),
                            'value': fade_full_value
                        })
                # Use simplified duration to update position for consistency
                music_pos = (music_pos + clip_duration).limit_denominator(10000)
                music_index += 1
    
    # Add teaser music for closing section (photos/teaser-videos)
    if teaser_music_assets and closing_section_start is not None and closing_section_end is not None:
        try:
            teaser_music_lane = int(teaser_music_config.get('audio_lane', 1))
        except (TypeError, ValueError):
            teaser_music_lane = 1
        fade_seconds = teaser_music_config.get('fade_duration', 1.0)
        fade_in_seconds = teaser_music_config.get('fade_in_duration', fade_seconds)
        fade_out_seconds = teaser_music_config.get('fade_out_duration', fade_seconds)
        fade_silence_db = teaser_music_config.get('fade_silence_db', -96.0)

        def _to_duration_closing(value):
            try:
                value = float(value)
            except (TypeError, ValueError):
                return None
            if value <= 0:
                return None
            return Fraction(value).limit_denominator(10000)

        fade_duration = _to_duration_closing(fade_seconds)
        fade_in_duration = _to_duration_closing(fade_in_seconds)
        fade_out_duration = _to_duration_closing(fade_out_seconds)
        
        seed = teaser_music_config.get('random_seed')
        rng = random.Random(seed) if seed is not None else random.Random()
        shuffled_teaser_music = teaser_music_assets[:]
        rng.shuffle(shuffled_teaser_music)
        
        def _fmt_time_closing(value):
            if value is None:
                return None
            return f"{value.numerator}/{value.denominator}s"
        
        if shuffled_teaser_music:
            music_pos = closing_section_start
            music_index = 0
            
            while music_pos < closing_section_end:
                track = shuffled_teaser_music[music_index % len(shuffled_teaser_music)]
                remaining = closing_section_end - music_pos
                clip_duration = track['duration'] if track['duration'] <= remaining else remaining
                if clip_duration <= 0:
                    break
                
                clip_start = music_pos.limit_denominator(10000)
                clip_duration = clip_duration.limit_denominator(10000)
                if clip_duration <= 0:
                    break  # limit_denominator can round tiny remainders to 0
                
                attributes = {
                    'name': Path(track['path']).name,
                    'ref': track['asset_id'],
                    'start': '0/1s',
                    'offset': f"{clip_start.numerator}/{clip_start.denominator}s",
                    'duration': f"{clip_duration.numerator}/{clip_duration.denominator}s",
                    'enabled': '1',
                    'tcFormat': 'NDF',
                    'lane': str(teaser_music_lane),
                    'audioStart': '0/1s',
                    'audioDuration': f"{clip_duration.numerator}/{clip_duration.denominator}s"
                }
                
                if fade_in_duration:
                    fade_in = fade_in_duration if fade_in_duration <= clip_duration else clip_duration
                    attributes['audioFadeIn'] = _fmt_time_closing(fade_in)
                if fade_out_duration:
                    fade_out = fade_out_duration if fade_out_duration <= clip_duration else clip_duration
                    attributes['audioFadeOut'] = _fmt_time_closing(fade_out)
                
                teaser_music_clip = SubElement(spine, 'asset-clip', attributes)
                SubElement(teaser_music_clip, 'adjust-volume', {
                    'amount': '0dB'
                })
                
                if fade_in_duration or fade_out_duration:
                    fade_in = fade_in_duration if fade_in_duration else None
                    fade_out = fade_out_duration if fade_out_duration else None
                    if fade_in and fade_in > clip_duration:
                        fade_in = clip_duration
                    if fade_out and fade_out > clip_duration:
                        fade_out = clip_duration
                    fade_out_start = clip_duration - fade_out if fade_out else None
                    if fade_out_start is not None and fade_out_start < Fraction(0, 1):
                        fade_out_start = Fraction(0, 1)

                    try:
                        fade_silence_value = f"{float(fade_silence_db)}dB"
                    except (TypeError, ValueError):
                        fade_silence_value = "-96.0dB"
                    fade_full_value = "0dB"

                    audio_automation = SubElement(teaser_music_clip, 'audio-automation', {
                        'lane': 'volume'
                    })
                    if fade_in:
                        SubElement(audio_automation, 'keyframe', {
                            'time': '0/1s',
                            'value': fade_silence_value
                        })
                        SubElement(audio_automation, 'keyframe', {
                            'time': _fmt_time_closing(fade_in),
                            'value': fade_full_value
                        })
                    else:
                        SubElement(audio_automation, 'keyframe', {
                            'time': '0/1s',
                            'value': fade_full_value
                        })

                    if fade_out and fade_out_start is not None:
                        SubElement(audio_automation, 'keyframe', {
                            'time': _fmt_time_closing(fade_out_start),
                            'value': fade_full_value
                        })
                        SubElement(audio_automation, 'keyframe', {
                            'time': _fmt_time_closing(clip_duration),
                            'value': fade_silence_value
                        })
                    else:
                        SubElement(audio_automation, 'keyframe', {
                            'time': _fmt_time_closing(clip_duration),
                            'value': fade_full_value
                        })
                
                music_pos = (music_pos + clip_duration).limit_denominator(10000)
                music_index += 1
    
    # Add teaser music to A1 track
    if teaser_music_assets and teaser_enabled and selected_teasers:
        try:
            teaser_music_lane = int(teaser_music_config.get('audio_lane', 1))
        except (TypeError, ValueError):
            teaser_music_lane = 1
        fade_seconds = teaser_music_config.get('fade_duration', 1.0)
        fade_in_seconds = teaser_music_config.get('fade_in_duration', fade_seconds)
        fade_out_seconds = teaser_music_config.get('fade_out_duration', fade_seconds)
        fade_silence_db = teaser_music_config.get('fade_silence_db', -96.0)

        def _to_duration(value):
            try:
                value = float(value)
            except (TypeError, ValueError):
                return None
            if value <= 0:
                return None
            return Fraction(value).limit_denominator(10000)

        teaser_fade_duration = _to_duration(fade_seconds)
        teaser_fade_in_duration = _to_duration(fade_in_seconds)
        teaser_fade_out_duration = _to_duration(fade_out_seconds)
        
        seed = teaser_music_config.get('random_seed')
        teaser_rng = random.Random(seed) if seed is not None else random.Random()
        teaser_music_track = teaser_rng.choice(teaser_music_assets)
        
        def _fmt_time(value):
            if value is None:
                return None
            return f"{value.numerator}/{value.denominator}s"
        
        # Calculate teaser section duration (only opening teaser clips BEFORE intro)
        teaser_start = Fraction(0, 1)
        teaser_end = Fraction(0, 1)
        intro_start = None
        
        # Find where intro starts
        for info, (clip_start, clip_end) in zip(clip_infos, clip_timeline_ranges):
            classification = info['scene'].get('classification')
            if classification == 'intro':
                intro_start = clip_start
                break
        
        # Calculate teaser duration only for clips BEFORE intro
        for info, (clip_start, clip_end) in zip(clip_infos, clip_timeline_ranges):
            classification = info['scene'].get('classification')
            # Only include opening teaser clips (before intro)
            if classification == 'teaser':
                if intro_start is None or clip_start < intro_start:
                    if teaser_end < clip_end:
                        teaser_end = clip_end
        
        if teaser_end > teaser_start:
            teaser_duration = teaser_end - teaser_start
            track_duration = teaser_music_track['duration']
            clip_duration = track_duration if track_duration <= teaser_duration else teaser_duration
            
            if clip_duration > 0:
                attributes = {
                    'name': Path(teaser_music_track['path']).name,
                    'ref': teaser_music_track['asset_id'],
                    'start': '0/1s',
                    'offset': f"{teaser_start.numerator}/{teaser_start.denominator}s",
                    'duration': f"{clip_duration.numerator}/{clip_duration.denominator}s",
                    'enabled': '1',
                    'tcFormat': 'NDF',
                    'lane': str(teaser_music_lane),
                    'audioStart': '0/1s',
                    'audioDuration': f"{clip_duration.numerator}/{clip_duration.denominator}s"
                }
                if teaser_fade_in_duration:
                    fade_in = teaser_fade_in_duration if teaser_fade_in_duration <= clip_duration else clip_duration
                    attributes['audioFadeIn'] = _fmt_time(fade_in)
                if teaser_fade_out_duration:
                    fade_out = teaser_fade_out_duration if teaser_fade_out_duration <= clip_duration else clip_duration
                    attributes['audioFadeOut'] = _fmt_time(fade_out)
                teaser_music_clip = SubElement(spine, 'asset-clip', attributes)
                SubElement(teaser_music_clip, 'adjust-volume', {
                    'amount': '0dB'
                })
    
    # Add final gap
    gap_dur = Fraction(1001, 2000)
    gap = SubElement(spine, 'gap', {
        'name': 'Gap',
        'start': '3600/1s',
        'offset': f'{timeline_pos_sec.numerator}/{timeline_pos_sec.denominator}s',
        'duration': f'{gap_dur.numerator}/{gap_dur.denominator}s'
    })

    # Fix sequence duration to match actual timeline content
    final_duration = (timeline_pos_sec + gap_dur).limit_denominator(_FRAC_LIMIT)
    sequence.set('duration', f'{final_duration.numerator}/{final_duration.denominator}s')
    
    # Write XML file with proper formatting
    tree = ElementTree(fcpxml)
    
    # Custom indent function for proper formatting
    def indent_xml(elem, level=0):
        i = "\n" + level * "    "
        if len(elem):
            if not elem.text or not elem.text.strip():
                elem.text = i + "    "
            if not elem.tail or not elem.tail.strip():
                elem.tail = i
            for child in elem:
                indent_xml(child, level+1)
            if not child.tail or not child.tail.strip():
                child.tail = i
        else:
            if level and (not elem.tail or not elem.tail.strip()):
                elem.tail = i
    
    indent_xml(fcpxml)
    
    # Write with DOCTYPE
    with open(output_file, 'wb') as f:
        f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(b'<!DOCTYPE fcpxml>\n')
        tree.write(f, encoding='utf-8', xml_declaration=False)
    
    total_input = sum(info['scene']['duration'] for info in clip_infos)
    total_output = sum(info['scene']['duration'] / info['scene']['speed'] for info in clip_infos)
    compression = (1 - total_output / total_input) * 100
    
    print()
    print('='*70)
    print('📊 Timeline Export Complete')
    print('='*70)
    print(f'  Format:          FCP XML 1.13 (DaVinci Resolve)')
    print(f'  Original:        {total_input/60:.1f} min')
    print(f'  Timeline:        {total_output/60:.1f} min')
    print(f'  Compression:     {compression:.0f}%')
    print(f'  Clips:           {len(clip_infos)}')
    print('='*70)
    print()
    print('⚠️  IMPORTANT - Import Steps for DaVinci Resolve:')
    print()
    print('   1. Open DaVinci Resolve')
    intro_exists = bool(intro_used_path and Path(intro_used_path).expanduser().resolve().exists())
    outro_exists = bool(outro_used_path and Path(outro_used_path).expanduser().resolve().exists())

    if use_rendered_all:
        print('   2. Import rendered clips to Media Pool FIRST')
        print('      (File → Import Media → Select all files in ai_clips/*/ including .mov intro/outro)')
    elif use_rendered_any:
        print('   2. Import source videos AND rendered clips to Media Pool FIRST')
        print('      (File → Import Media → Select videos and ai_clips/*/*.mkv)')
    else:
        print('   2. Import source videos to Media Pool FIRST')
        print('      (File → Import Media → Select all input videos)')
    if intro_exists or outro_exists:
        extra = []
        if intro_exists:
            extra.append(str(Path(intro_used_path).expanduser().resolve()))
        if outro_exists:
            extra.append(str(Path(outro_used_path).expanduser().resolve()))
        print('      Also import intro/outro clips:')
        for path in extra:
            print(f'      - {path}')
    print('   3. Then import timeline:')
    print('      File → Import → Timeline → Import AAF/EDL/XML')
    print(f'      Select: {output_file}')
    print()
    print('   ⚠️  Media MUST be in Media Pool before importing XML!')
    print()
    if use_rendered_any:
        print('✅ Timeline uses pre-rendered clips to avoid timeMap issues.')
    else:
        print('✅ No re-encoding needed! Timeline uses original video with speed changes.')
    print()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Export DaVinci Resolve FCPXML timeline')
    parser.add_argument('--analysis', default='scene_analysis_advanced3.json', help='Analysis JSON file or directory')
    parser.add_argument('--video-dir', default='.', help='Directory containing source videos')
    parser.add_argument('--output', default='timeline_davinci_resolve.fcpxml', help='Output FCPXML file')
    parser.add_argument('--clips-dir', default='ai_clips', help='Base directory for rendered clips')
    parser.add_argument('--use-rendered', dest='use_rendered', action='store_true', default=True,
                        help='Use pre-rendered clips when available (default)')
    parser.add_argument('--use-original', dest='use_rendered', action='store_false',
                        help='Use original videos instead of rendered clips')
    parser.add_argument('--legacy-asset-format', action='store_true', help='Use legacy asset-clip format instead of Resolve ref-clip format')
    parser.add_argument('--exclude-boring', action='store_true', help='Exclude boring scenes from the timeline')
    parser.add_argument('--dedupe', action='store_true', help='Remove near-duplicate scenes across videos')
    parser.add_argument('--hash-threshold', type=int, default=6, help='Hamming distance threshold for dedupe')
    parser.add_argument('--config', default='project_config.json', help='Project config JSON file')
    args = parser.parse_args()
    
    analysis_path = args.analysis
    if not os.path.exists(analysis_path):
        print(f'❌ Analysis path not found: {analysis_path}')
        sys.exit(1)
    
    # Load config and get paths
    config = load_project_config(args.config)
    paths_cfg = config.get('paths', {})
    video_dir = args.video_dir or paths_cfg.get('video_dir') or paths_cfg.get('video') or paths_cfg.get('input_dir') or '.'
    clips_dir = args.clips_dir or paths_cfg.get('clips_dir') or './ai_clips'
    
    create_fcpxml_timeline(
        analysis_path,
        video_dir,
        args.output,
        clip_base_dir=clips_dir,
        dedupe=args.dedupe,
        hash_threshold=args.hash_threshold,
        use_rendered=args.use_rendered,
        resolve_format=not args.legacy_asset_format,
        exclude_boring=args.exclude_boring,
        config=config
    )
