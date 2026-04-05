#!/usr/bin/env python3
"""
Render DaVinci Resolve timeline to 4K MP4 for YouTube.
Uses working method: get presets → load preset → set format → add job → render.
YouTube optimized: 30 Mbps H.265 HEVC codec.
Reads configuration from project_config.json.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path


def _title_to_filename(title):
	"""Convert an upload title to a safe filename (without extension)."""
	safe = re.sub(r'[^\w\s-]', '', title).strip()
	return re.sub(r'[\s]+', '_', safe)


def _add_resolve_module_path():
	"""Add Resolve's Python module path to sys.path."""
	paths_to_add = [
		Path("/opt/resolve/Developer/Scripting/Modules"),
		Path("/opt/resolve/libs/Fusion/lua"),
		Path("/opt/resolve/libs/python"),
		Path("/opt/resolve"),
	]
	for resolve_path in paths_to_add:
		if resolve_path.is_dir() and str(resolve_path) not in sys.path:
			sys.path.insert(0, str(resolve_path))


def _connect_resolve():
	"""Connect to DaVinci Resolve via scripting API."""
	_add_resolve_module_path()
	try:
		import DaVinciResolveScript as dvr
	except Exception as e:
		print(f"ERROR: Failed to import DaVinciResolveScript: {e}")
		return None
	
	try:
		resolve = dvr.scriptapp("Resolve")
		return resolve
	except Exception as e:
		print(f"ERROR: Could not connect to Resolve: {e}")
		return None


def render_timeline_youtube(output_path, timeline_index=1, config=None):
	"""Render Resolve timeline to YouTube 4K MP4 using working preset method."""
	
	# Load config if not provided
	if config is None:
		config_path = Path("project_config.json")
		if config_path.exists():
			with open(config_path, "r") as f:
				config = json.load(f)
		else:
			config = {}
	
	# Get render settings from config
	resolve_cfg = config.get("resolve", {})
	render_cfg = resolve_cfg.get("render_settings", {})
	
	# Default render settings
	format_name = render_cfg.get("format", "mp4")
	codec_name = render_cfg.get("codec", "H265_NVIDIA")
	video_quality = render_cfg.get("video_quality", 30000)
	encoding_profile = render_cfg.get("encoding_profile", "Main")
	preset_name = render_cfg.get("preset_name", "medium")
	output_base_dir = render_cfg.get("output_dir", "/home/mazsola/Videos")
	
	resolve = _connect_resolve()
	if not resolve:
		return False
	
	try:
		pm = resolve.GetProjectManager()
		project = pm.GetCurrentProject()
		if not project:
			print("ERROR: No current project in Resolve")
			return False
		
		print(f"Project: {project.GetName()}")
		
		timeline = project.GetTimelineByIndex(timeline_index)
		if not timeline:
			print(f"ERROR: Could not get timeline at index {timeline_index}")
			return False
		
		print(f"Timeline: {timeline.GetName()}")
		
		fps = timeline.GetSetting("timelineFrameRate")
		print(f"Timeline FPS: {fps}")
		
		# Use configured output directory (media storage location)
		output_path_obj = Path(output_base_dir) / Path(output_path).name
		output_path_obj.parent.mkdir(parents=True, exist_ok=True)
		output_path_str = str(output_path_obj)
		print(f"Output path: {output_path_str}\n")
		
		# Get available render presets (for reference)
		print("Getting available presets...")
		presets = project.GetRenderPresetList()
		print(f"Available presets: {presets}\n")
		
		# Don't load preset - just set all parameters directly
		print("Setting render parameters directly...")
		print(f"  Format: {format_name}")
		print(f"  Codec: {codec_name}")
		print(f"  Bitrate: {video_quality // 1000} Mbps")
		print("  Resolution: 3840x2160 (4K)\n")
		
		# Step 1: Set Location
		print("STEP 1: Set Location")
		
		# Parse output path
		output_dir = output_path_str.rsplit('/', 1)[0]
		output_file = output_path_str.rsplit('/', 1)[1]
		
		# Ensure directory exists and is accessible
		import os
		os.makedirs(output_dir, exist_ok=True)
		print(f"  Output dir: {output_dir}")
		print(f"  Dir exists: {os.path.isdir(output_dir)}")
		print(f"  Dir writable: {os.access(output_dir, os.W_OK)}\n")
		
		# First, explicitly set format and codec using correct names from GetRenderCodecs
		print("  Setting format and codec...")
		format_result = project.SetCurrentRenderFormatAndCodec(format_name, codec_name)
		print(f"  SetCurrentRenderFormatAndCodec('{format_name}', '{codec_name}') result: {format_result}")
		
		# Verify it was set
		current_format = project.GetCurrentRenderFormatAndCodec()
		print(f"  Current format: {current_format}")
		
		time.sleep(0.5)
		
		# Then set render settings
		# Resolve API requires TargetDir + CustomName (not TargetFile)
		output_filename = Path(output_path_str).stem  # filename without extension
		render_settings = {
			"TargetDir": output_dir,
			"CustomName": output_filename,
			"VideoQuality": video_quality,
			"EncodingProfile": encoding_profile,
			"PresetName": preset_name,
		}
		result = project.SetRenderSettings(render_settings)
		print(f"  SetRenderSettings result: {result}")
		
		print("✓ Location and format set")
		print(f"  ✓ Format: {format_name.upper()}")
		print(f"  ✓ Codec: {codec_name}")
		print(f"  ✓ VideoQuality: {video_quality} ({video_quality // 1000} Mbps)")
		print(f"  ✓ Preset: {preset_name}\n")
		time.sleep(0.5)
		
		# Step 2: Add to Render Queue
		print("STEP 2: Add to Render Queue")
		job = project.AddRenderJob()
		
		if not job:
			print("ERROR: Failed to add job")
			return False
		
		print(f"✓ Job added: {job}\n")
		time.sleep(0.5)
		
		# Step 3: Render All
		print("STEP 3: Render All")
		project.StartRendering(job)
		print("✓ Render All started\n")
		
		# Monitor progress
		print("Monitoring render progress...")
		max_wait = 7200  # 2 hours
		elapsed = 0
		last_pct = -1
		
		while elapsed < max_wait:
			try:
				status = project.GetRenderJobStatus(job)
				
				if isinstance(status, dict):
					pct = status.get("CompletionPercentage", 0)
					job_status = status.get("JobStatus", "Unknown")
					eta_ms = status.get("EstimatedTimeRemainingInMs", 0)
					
					if pct != last_pct:
						eta_sec = eta_ms / 1000
						print(f"  {pct:3d}% - {job_status} (ETA: {eta_sec:.0f}s)")
						last_pct = pct
					
					if job_status == "Completed":
						print("\n✅ Render completed!")
						time.sleep(0.5)
						
						if Path(output_path_str).exists():
							size_mb = Path(output_path_str).stat().st_size / (1024 * 1024)
							print(f"Output file: {size_mb:.1f} MB\n")
							return True
						else:
							print(f"WARNING: Output file not found at {output_path_str}")
							return False
					
					elif job_status == "Error":
						print("\n❌ Render failed")
						return False
			
			except Exception as e:
				print(f"  (status check: {type(e).__name__})")
			
			time.sleep(10)
			elapsed += 10
		
		print(f"\n❌ Render timeout after {elapsed}s")
		return False
	
	except Exception as e:
		print(f"ERROR: {e}")
		import traceback
		traceback.print_exc()
		return False


def main():
	parser = argparse.ArgumentParser(
		description="Render Resolve timeline to 4K MP4 for YouTube"
	)
	parser.add_argument(
		"--output",
		type=str,
		default="timeline_output_4k_youtube.mp4",
		help="Output MP4 file path (default: timeline_output_4k_youtube.mp4)",
	)
	parser.add_argument(
		"--timeline-index",
		type=int,
		default=1,
		help="Timeline index to render (1-based, default: 1)",
	)
	parser.add_argument(
		"--config",
		type=str,
		default="project_config.json",
		help="Project configuration file (default: project_config.json)",
	)
	
	args = parser.parse_args()
	
	# Load config
	config = {}
	config_path = Path(args.config)
	if config_path.exists():
		with open(config_path, "r") as f:
			config = json.load(f)
	
	# Derive output filename from upload title if --output was not explicitly set
	if args.output == "timeline_output_4k_youtube.mp4":
		youtube_cfg = config.get("youtube", {})
		upload_title = youtube_cfg.get("upload_title") or youtube_cfg.get("default_title", "")
		if upload_title:
			args.output = f"{_title_to_filename(upload_title)}.mp4"
	
	output_path = Path(args.output).resolve()
	output_path.parent.mkdir(parents=True, exist_ok=True)
	
	# Determine actual render path (respects output_dir from config)
	resolve_cfg = config.get("resolve", {})
	render_output_dir = resolve_cfg.get("render_settings", {}).get("output_dir", "")
	if render_output_dir:
		actual_render_path = Path(render_output_dir) / output_path.name
	else:
		actual_render_path = output_path

	success = render_timeline_youtube(str(output_path), args.timeline_index, config)
	
	if success:
		print(f"\n✅ YouTube render complete: {actual_render_path}")
		if actual_render_path.exists():
			size_mb = actual_render_path.stat().st_size / (1024 * 1024)
			print(f"File size: {size_mb:.1f} MB")
		
		# Prompt for YouTube upload
		print("\n" + "=" * 72)
		print("📤 YOUTUBE UPLOAD")
		print("=" * 72)
		print(f"   Video: {actual_render_path}")
		print(f"   Size:  {size_mb:.1f} MB")
		print("=" * 72)
		try:
			while True:
				response = input("\nUpload to YouTube? (y/N): ").strip().lower()
				if response in ('y', 'yes'):
					print("✓ Starting YouTube upload...")
					import subprocess
					script_dir = Path(__file__).resolve().parent
					config_path = Path(args.config).resolve()
					upload_cmd = [
						sys.executable,
						str(script_dir / "upload_youtube.py"),
						"--video", str(actual_render_path),
						"--config", str(config_path),
					]
					print(f"\n▶ Upload to YouTube")
					print("-" * 72)
					print(" ".join(upload_cmd))
					print("-" * 72)
					upload_result = subprocess.run(upload_cmd, cwd=script_dir)
					if upload_result.returncode == 0:
						print("✔ YouTube upload completed successfully")
					else:
						print(f"\n⚠️  Upload reported issues (exit code {upload_result.returncode})")
					break
				elif response in ('n', 'no', ''):
					print("✗ Upload skipped")
					break
				else:
					print("Please enter 'y' or 'n'")
		except (EOFError, KeyboardInterrupt):
			print("\n✗ Upload skipped")
	else:
		print("\n❌ YouTube render failed")
		sys.exit(1)


if __name__ == "__main__":
	main()
