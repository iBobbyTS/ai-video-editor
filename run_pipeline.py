#!/usr/bin/env python3
"""
End-to-end workflow: analyze -> extract clips -> export Resolve timeline.
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path


def run_stage(label, cmd, cwd):
	print(f"\n▶ {label}")
	print("-" * 72)
	print(" ".join(cmd))
	print("-" * 72)
	subprocess.run(cmd, cwd=cwd, check=True)
	print(f"✔ {label} completed")


def list_videos(input_dir):
	return sorted(
		[p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in {".mov", ".mp4", ".mkv"}],
		key=lambda p: p.name.lower(),
	)


def analysis_output_path(video_path, output_dir):
	return output_dir / f"scene_analysis_{video_path.stem}.json"


def analysis_failed_marker(video_path, output_dir):
	return output_dir / f"scene_analysis_{video_path.stem}.failed"


def analysis_exists(video_path, output_dir):
	primary = analysis_output_path(video_path, output_dir)
	if primary.exists():
		return True
	pattern = f"scene_analysis_{video_path.stem}*.json"
	return any(output_dir.glob(pattern))


def analysis_mode_matches(video_path, output_dir, mode):
	"""Check if the existing analysis JSON was created with the same mode."""
	primary = analysis_output_path(video_path, output_dir)
	if not primary.exists():
		return True  # no JSON → nothing to mismatch
	try:
		with open(primary) as f:
			data = json.load(f)
		existing_mode = data.get('mode', 'build')
		return existing_mode == mode
	except (json.JSONDecodeError, OSError):
		return True


def expected_clip_exists(clips_dir, video_stem, scene_num, classification):
	pattern = f"{video_stem}_scene_{scene_num:02d}_{classification}_*x.mkv"
	return any((clips_dir / video_stem).glob(pattern))


def clips_complete(analysis_file, clips_dir):
	import json
	with open(analysis_file, "r") as f:
		analysis = json.load(f)

	video_name = analysis.get("video")
	if not video_name:
		return False

	video_stem = Path(video_name).stem
	output_dir = clips_dir / video_stem
	if not output_dir.exists():
		return False

	for scene in analysis.get("scenes", []):
		classification = scene.get("classification", "unknown")
		if classification == "skip":
			continue
		if not expected_clip_exists(clips_dir, video_stem, scene["scene_num"], classification):
			return False

	return True


def load_project_config(config_path):
	if not config_path:
		return {}
	path = Path(config_path)
	if not path.exists():
		return {}
	try:
		with open(path, "r") as f:
			data = json.load(f)
		if isinstance(data, dict):
			return data
	except Exception:
		return {}
	return {}


def _add_resolve_module_path():
	for path in [
		"/opt/resolve/Developer/Scripting/Modules",
		"/opt/resolve/Developer/Scripting/Modules/Resolve",
		"/opt/resolve/Developer/Scripting/Modules/DaVinciResolveScript",
		os.path.expanduser("~/DaVinciResolve/Developer/Scripting/Modules"),
	]:
		if os.path.isdir(path) and path not in sys.path:
			sys.path.append(path)


def _connect_resolve(retries=30, delay_seconds=2):
	_add_resolve_module_path()
	try:
		import DaVinciResolveScript as dvr  # type: ignore
	except Exception:
		return None
	for _ in range(retries):
		resolve = dvr.scriptapp("Resolve")
		if resolve:
			return resolve
		time.sleep(delay_seconds)
	return None


def _launch_resolve(launch_cmd, launch_env):
	cmd = launch_cmd
	if isinstance(cmd, str):
		cmd = shlex.split(cmd)
	if not cmd:
		return None
	env = os.environ.copy()
	if isinstance(launch_env, dict):
		for key, value in launch_env.items():
			env[str(key)] = str(value)
	return subprocess.Popen(cmd, env=env, cwd=str(Path(__file__).resolve().parent))


def _import_timeline(resolve, timeline_path, timeline_name=None):
	pm = resolve.GetProjectManager()
	project = pm.GetCurrentProject()
	if not project:
		return False
	media_pool = project.GetMediaPool()
	if not media_pool:
		return False
	options = None
	if timeline_name:
		options = {"timelineName": timeline_name}
	try:
		result = media_pool.ImportTimelineFromFile(str(timeline_path), options) if options else media_pool.ImportTimelineFromFile(str(timeline_path))
		return bool(result)
	except Exception:
		pass
	try:
		result = project.ImportTimelineFromFile(str(timeline_path), options) if options else project.ImportTimelineFromFile(str(timeline_path))
		return bool(result)
	except Exception:
		return False


def _create_or_load_project(resolve, base_name, create_new=True):
	pm = resolve.GetProjectManager()
	if not pm:
		return None
	if not base_name:
		base_name = "AI Pipeline"
	if create_new:
		timestamp = time.strftime("%Y%m%d_%H%M%S")
		project_name = f"{base_name}_{timestamp}"
		project = pm.CreateProject(project_name)
		if project:
			return project
		# Fallback to loading the base name if create failed
		project = pm.LoadProject(base_name)
		if project:
			return project
		return None
	# If not creating new, try load first, else create
	project = pm.LoadProject(base_name)
	if project:
		return project
	return pm.CreateProject(base_name)


def main():
	parser = argparse.ArgumentParser(description="End-to-end AI video pipeline")
	parser.add_argument("--config", default="project_config.json", help="Project config JSON file")
	parser.add_argument("--input-dir", default=None, help="Folder with input videos")
	parser.add_argument("--video", default=None, help="Single video file to analyze")
	parser.add_argument("--output-dir", default=None, help="Folder for analysis JSON outputs")
	parser.add_argument("--clips-dir", default=None, help="Base output directory for clips")
	parser.add_argument("--timeline", default=None, help="Output FCPXML file")
	parser.add_argument("--sample-interval", type=int, default=None, help="Frame sample interval (seconds)")
	parser.add_argument("--dedupe", action=argparse.BooleanOptionalAction, default=None, help="Deduplicate similar scenes across videos")
	parser.add_argument("--hash-threshold", type=int, default=None, help="Hamming distance threshold for dedupe")
	parser.add_argument("--use-rendered", action=argparse.BooleanOptionalAction, default=None, help="Use pre-rendered clips when available")
	parser.add_argument("--skip-analysis", action="store_true", help="Skip analysis stage")
	parser.add_argument("--skip-extract", action="store_true", help="Skip clip extraction stage")
	parser.add_argument("--skip-export", action="store_true", help="Skip timeline export stage")
	parser.add_argument("--force-analysis", action="store_true", help="Re-run analysis even if JSON exists")
	parser.add_argument("--reels-only", action="store_true", help="Skip main pipeline, run only Reels/Shorts stages")
	parser.add_argument("--yes", "-y", action="store_true", help="Auto-confirm all interactive prompts (render, upload, etc.)")
	parser.add_argument("--mode", default=None, choices=['build', 'unboxing', 'reels'], help="Pipeline mode: build (default), unboxing (keep audio/speed), reels")
	args = parser.parse_args()

	base_dir = Path(__file__).resolve().parent
	# Use venv Python if running from system Python
	venv_python = base_dir / ".venv" / "bin" / "python"
	python = str(venv_python) if venv_python.exists() else sys.executable

	config = load_project_config(args.config)
	paths_cfg = config.get("paths", {})
	analysis_cfg = config.get("analysis", {})
	pipeline_cfg = config.get("pipeline", {})

	# video_dir is where videos AND analysis files are located
	video_dir_cfg = paths_cfg.get("video_dir") or paths_cfg.get("input_dir") or "."
	input_dir = Path(args.input_dir or video_dir_cfg).resolve()
	output_dir = Path(args.output_dir or video_dir_cfg).resolve()
	clips_dir = Path(args.clips_dir or paths_cfg.get("clips_dir") or "ai_clips").resolve()
	timeline_path = Path(args.timeline or paths_cfg.get("timeline") or "timeline_davinci_resolve.fcpxml").resolve()

	video_arg = args.video or paths_cfg.get("video")
	if video_arg:
		video_path = Path(video_arg).resolve()
		video_dir = video_path.parent
	else:
		video_path = None
		video_dir = input_dir

	sample_interval = args.sample_interval if args.sample_interval is not None else analysis_cfg.get("sample_interval", 2)
	dedupe = args.dedupe if args.dedupe is not None else pipeline_cfg.get("dedupe", False)
	hash_threshold = args.hash_threshold if args.hash_threshold is not None else pipeline_cfg.get("hash_threshold", 6)
	use_rendered = args.use_rendered if args.use_rendered is not None else pipeline_cfg.get("use_rendered", False)
	skip_analysis = args.skip_analysis or pipeline_cfg.get("skip_analysis", False)
	skip_extract = args.skip_extract or pipeline_cfg.get("skip_extract", False)
	skip_export = args.skip_export or pipeline_cfg.get("skip_export", False)
	reels_only = args.reels_only
	auto_yes = args.yes
	mode = args.mode or config.get('mode', 'build')
	if reels_only:
		skip_analysis = True
		skip_extract = True
		skip_export = True

	print("\n=== AI Video Pipeline ===")
	print(f"Mode:        {mode}")
	print(f"Input dir:   {input_dir}")
	print(f"Output dir:  {output_dir}")
	print(f"Clips dir:   {clips_dir}")
	print(f"Timeline:    {timeline_path}")
	print(f"Sample step: {sample_interval}s")
	print(f"Dedupe:      {dedupe} (threshold {hash_threshold})")
	print(f"Rendered:    {use_rendered}")
	if mode == 'unboxing':
		print(f"Audio:       KEEP (unboxing mode - narration preserved)")
		print(f"Speed:       1.0x (no speedup)")

	try:
		if not skip_analysis:
			videos_to_analyze = []
			failed_before = []
			if video_path:
				if args.force_analysis:
					videos_to_analyze = [video_path]
				else:
					failed_marker = analysis_failed_marker(video_path, output_dir)
					if failed_marker.exists():
						failed_before.append(video_path)
					elif not analysis_exists(video_path, output_dir):
						videos_to_analyze = [video_path]
			else:
				for vid in list_videos(input_dir):
					if args.force_analysis:
						videos_to_analyze.append(vid)
						continue
					failed_marker = analysis_failed_marker(vid, output_dir)
					if failed_marker.exists():
						failed_before.append(vid)
						continue
					if not analysis_exists(vid, output_dir):
						videos_to_analyze.append(vid)
					elif not analysis_mode_matches(vid, output_dir, mode):
						print(f"   🔄 Re-analyzing {vid.name} (mode changed → {mode})")
						videos_to_analyze.append(vid)

			if not videos_to_analyze:
				print("\n▶ [1/3] Analyze videos (skipped - JSON exists)")
			else:
				# Run batch analysis with analyze_advanced5.py
				cmd = [
					python,
					str(base_dir / "analyze_advanced5.py"),
					"--config",
					str(Path(args.config).resolve()),
					"--input-dir",
					str(input_dir),
					"--output-dir",
					str(output_dir),
					"--sample-interval",
					str(sample_interval),
					"--skip-duplicate-captions",
					"--max-scene-length",
					"40",
					"--mode",
					mode,
				]
				if video_path:
					cmd.extend(["--video", str(video_path)])
				run_stage(f"[1/3] Analyze videos with Qwen2.5-VL-7B ({mode} mode)", cmd, base_dir)
				# Mark any still-missing analyses to avoid repeated attempts
				still_missing = [v for v in videos_to_analyze if not analysis_exists(v, output_dir)]
				for v in still_missing:
					marker = analysis_failed_marker(v, output_dir)
					marker.write_text("analysis failed or did not produce JSON\n")
				if still_missing:
					print("\n⚠️  Some analyses did not produce JSON and were marked as failed:")
					for v in still_missing:
						print(f"   - {v.name}")
					print("   (Delete the .failed file or use --force-analysis to retry.)")
				if failed_before:
					print("\nℹ️  Skipped previously failed analyses:")
					for v in failed_before:
						print(f"   - {v.name}")
		else:
			print("\n▶ [1/3] Analyze videos (skipped)")

		if not skip_extract:
			# Run batch extraction with extract_scenes.py
			cmd = [
				python,
				str(base_dir / "extract_scenes.py"),
				"--analysis-dir",
				str(output_dir),
				"--output-dir",
				str(clips_dir),
				"--mode",
				mode,
			]
			if video_dir != input_dir:
				cmd.extend(["--video-dir", str(video_dir)])
			run_stage("[2/3] Extract scenes and showcase moments", cmd, base_dir)
		else:
			print("\n▶ [2/3] Extract scenes (skipped)")

		if not skip_export:
			cmd = [
				python,
				str(base_dir / "export_resolve.py"),
				"--config",
				str(Path(args.config).resolve()),
				"--analysis",
				str(output_dir),
				"--video-dir",
				str(video_dir),
				"--clips-dir",
				str(clips_dir),
				"--output",
				str(timeline_path),
				"--mode",
				mode,
			]
			if use_rendered:
				cmd.append("--use-rendered")
			if dedupe:
				cmd.append("--dedupe")
				cmd.extend(["--hash-threshold", str(hash_threshold)])
			run_stage("[3/3] Export Resolve timeline", cmd, base_dir)
		else:
			print("\n▶ [3/3] Export Resolve timeline (skipped)")

		resolve_cfg = config.get("resolve", {})
		if resolve_cfg.get("auto_start") and not reels_only:
			print("\n▶ [4/5] Launch/attach DaVinci Resolve")
			resolve = _connect_resolve(retries=3, delay_seconds=1)
			process = None
			if not resolve:
				process = _launch_resolve(
					resolve_cfg.get("launch_cmd", "/opt/resolve/bin/resolve"),
					resolve_cfg.get("launch_env", {}),
				)
				startup_wait = resolve_cfg.get("startup_wait_seconds", 20)
				time.sleep(startup_wait)
				resolve = _connect_resolve(retries=60, delay_seconds=2)
			if not resolve:
				raise RuntimeError("Resolve is not available via scripting API.")
			print("✔ Resolve connected")

			print("\n▶ [5/5] Import timeline and apply LUT")
			project_name = resolve_cfg.get("project_name", "AI Pipeline")
			create_new_project = resolve_cfg.get("create_new_project", True)
			project = _create_or_load_project(resolve, project_name, create_new=create_new_project)
			if not project:
				raise RuntimeError("Failed to create or load Resolve project.")
			print(f"Project ready: {project.GetName()}")
			imported = _import_timeline(resolve, timeline_path, resolve_cfg.get("timeline_name"))
			print(f"Import timeline: {'ok' if imported else 'failed'}")
			import_wait = resolve_cfg.get("import_wait_seconds", 10)
			time.sleep(import_wait)
			if resolve_cfg.get("apply_lut_after_import", True):
				cmd = [
					python,
					str(base_dir / "apply_lut_resolve.py"),
					"--config",
					str(Path(args.config).resolve()),
					"--mode",
					resolve_cfg.get("apply_lut_mode", "mediapool"),
					"--property-key",
					resolve_cfg.get("lut_property_key", "Input LUT"),
				]
				run_stage("Apply LUT", cmd, base_dir)
			
			# Render final output
			if resolve_cfg.get("render_youtube_4k", False):
				youtube_cfg = config.get("youtube", {})
				upload_title = youtube_cfg.get("upload_title") or youtube_cfg.get("default_title", "")
				if upload_title:
					import re as _re
					safe_name = _re.sub(r'[^\w\s-]', '', upload_title).strip()
					safe_name = _re.sub(r'[\s]+', '_', safe_name)
					youtube_output = f"{safe_name}.mp4"
				else:
					youtube_output = resolve_cfg.get("youtube_output_path", "timeline_output_4k_youtube.mp4")
				youtube_output_path = base_dir / youtube_output
				
				print("\n" + "=" * 72)
				print("📌 TIMELINE VALIDATION REQUIRED")
				print("=" * 72)
				print("\n✓ Pipeline stages complete:")
				print("  [1/5] Analysis - DONE")
				print("  [2/5] Scene Extraction - DONE")
				print("  [3/5] Timeline Export - DONE")
				print("  [4/5] Resolve Import - DONE")
				print("  [5/5] LUT Applied - DONE")
				print("\n📺 In DaVinci Resolve:")
				print("   - Review the timeline with LUT applied")
				print("   - Verify all clips, transitions, and effects")
				print("   - Check audio levels and sync")
				print("   - Confirm 4K resolution (3840x2160)")
				print("\n📤 Ready for YouTube 4K render:")
				print("   - Format: MP4 (H.265 HEVC)")
				print("   - Bitrate: 45 Mbps (optimized for YouTube)")
				print("   - Output: " + str(youtube_output_path))
				print("\n" + "=" * 72)
				
				# Prompt user to validate
				if auto_yes:
					print("\n✓ Auto-confirmed (--yes): Starting render...")
				else:
					print("\n⏸️  Waiting for validation...")
					while True:
						response = input("Proceed with YouTube 4K render? (y/N): ").strip().lower()
						if response in ('y', 'yes'):
							print("✓ Starting render...")
							break
						elif response in ('n', 'no', ''):
							print("✗ Render cancelled")
							print("\nTimeline ready in Resolve for manual export if needed.")
							print(f"When ready, run: python3 render_youtube.py --output {youtube_output_path}")
							return
						else:
							print("Please enter 'y' or 'n'")
				
				print("\n")
				cmd = [
					python,
					str(base_dir / "render_youtube.py"),
					"--output",
					str(youtube_output_path),
					"--timeline-index",
					str(resolve_cfg.get("timeline_index", 1)),
				]
				
				# Try to run render, but handle API issues gracefully
				print(f"\n▶ [6/5] Render YouTube 4K MP4")
				print("-" * 72)
				print(" ".join(cmd))
				print("-" * 72)
				
				try:
					result = subprocess.run(cmd, cwd=base_dir, timeout=600)
					if result.returncode == 0:
						print(f"✔ YouTube render completed successfully")
						print(f"   Output: {youtube_output_path}")
					else:
						print(f"\n⚠️  Render script reported issues (exit code {result.returncode})")
						print("   Check the output above for manual render instructions")
				except subprocess.TimeoutExpired:
					print(f"\n⚠️  Render script timeout")
					print("   The render may still be processing in Resolve.")
					print("   Check Resolve's Deliver page for status.")

				# --- YouTube Upload ---
				render_output_dir = resolve_cfg.get("render_settings", {}).get("output_dir", "")
				if render_output_dir:
					actual_render_path = Path(render_output_dir) / youtube_output
				else:
					actual_render_path = youtube_output_path

				if actual_render_path.exists():
					print("\n" + "=" * 72)
					print("📤 YOUTUBE UPLOAD")
					print("=" * 72)
					print(f"   Video: {actual_render_path}")
					print(f"   Size:  {actual_render_path.stat().st_size / (1024 * 1024):.1f} MB")
					print("=" * 72)
					if auto_yes:
						response = 'y'
						print("\n✓ Auto-confirmed (--yes): Starting YouTube upload...")
					else:
						response = ''
						while True:
							response = input("\nUpload to YouTube? (y/N): ").strip().lower()
							if response in ('y', 'yes', 'n', 'no', ''):
								break
							print("Please enter 'y' or 'n'")
					if response in ('y', 'yes'):
						print("✓ Starting YouTube upload...")
						upload_cmd = [
							python,
							str(base_dir / "upload_youtube.py"),
							"--video",
							str(actual_render_path),
							"--config",
							str(base_dir / "project_config.json"),
						]
						print(f"\n▶ [7/5] Upload to YouTube")
						print("-" * 72)
						print(" ".join(upload_cmd))
						print("-" * 72)
						try:
							upload_result = subprocess.run(upload_cmd, cwd=base_dir)
							if upload_result.returncode == 0:
								print(f"✔ YouTube upload completed successfully")
							else:
								print(f"\n⚠️  Upload reported issues (exit code {upload_result.returncode})")
						except Exception as e:
							print(f"\n⚠️  Upload error: {e}")
					else:
						print("✗ Upload skipped")

		# ─── Reels / Shorts Pipeline ───
		reels_cfg = config.get("reels", {})
		reels_videos_folder = config.get("paths", {}).get("videos_reels", "")
		if reels_videos_folder:
			reels_dir = Path(reels_videos_folder)
			if not reels_dir.is_absolute():
				reels_dir = (base_dir / reels_dir).resolve()
		else:
			reels_dir = None

		reels_has_clips = (
			reels_dir
			and reels_dir.exists()
			and any(
				p.is_file() and p.suffix.lower() in {".mov", ".mp4", ".mkv", ".avi"}
				for p in reels_dir.iterdir()
			)
		)

		if reels_has_clips:
			print("\n" + "=" * 72)
			print("📱 REELS / SHORTS PIPELINE")
			print("=" * 72)
			print(f"  Clips folder:  {reels_dir}")
			print(f"  Music folder:  {reels_cfg.get('music_folder', 'N/A')}")
			print(f"  Max duration:  {reels_cfg.get('max_duration_seconds', 59)}s")
			print(f"  Resolution:    {reels_cfg.get('timeline_width', 1080)}x{reels_cfg.get('timeline_height', 1920)}")
			print("=" * 72)

			if auto_yes:
				print("\n✓ Auto-confirmed (--yes): Building Reels/Shorts timeline...")
			else:
				while True:
					response = input("\nBuild Reels/Shorts timeline? (y/N): ").strip().lower()
					if response in ('y', 'yes'):
						break
					elif response in ('n', 'no', ''):
						print("✗ Reels pipeline skipped")
						reels_has_clips = False
						break
					else:
						print("Please enter 'y' or 'n'")

		if reels_has_clips:
			# Export reels FCPXML
			reels_timeline = reels_cfg.get("timeline_file", "./timeline_reels.fcpxml")
			reels_timeline_path = Path(reels_timeline)
			if not reels_timeline_path.is_absolute():
				reels_timeline_path = (base_dir / reels_timeline_path).resolve()

			cmd = [
				python,
				str(base_dir / "export_reels.py"),
				"--config",
				str(Path(args.config).resolve()),
				"--output",
				str(reels_timeline_path),
			]
			run_stage("📱 [R1/4] Export Reels timeline (9:16)", cmd, base_dir)

			# Import reels timeline into Resolve
			if resolve_cfg.get("auto_start"):
				print("\n▶ 📱 [R2/4] Launch/attach Resolve & Import Reels timeline")
				resolve = _connect_resolve(retries=3, delay_seconds=1)
				if not resolve:
					process = _launch_resolve(
						resolve_cfg.get("launch_cmd", "/opt/resolve/bin/resolve"),
						resolve_cfg.get("launch_env", {}),
					)
					startup_wait = resolve_cfg.get("startup_wait_seconds", 20)
					time.sleep(startup_wait)
					resolve = _connect_resolve(retries=60, delay_seconds=2)
				if not resolve:
					raise RuntimeError("Resolve is not available via scripting API.")
				print("✔ Resolve connected")

				# Create project for reels
				project_name = resolve_cfg.get("project_name", "AI Pipeline")
				create_new_project = resolve_cfg.get("create_new_project", True)
				project = _create_or_load_project(resolve, f"{project_name}_Reels", create_new=create_new_project)
				if not project:
					raise RuntimeError("Failed to create or load Resolve project for Reels.")
				print(f"Reels project ready: {project.GetName()}")

				reels_imported = _import_timeline(
					resolve, reels_timeline_path,
					reels_cfg.get("timeline_name", "Reels Timeline (Resolve)"),
				)
				print(f"   Import reels timeline: {'ok' if reels_imported else 'failed'}")
				time.sleep(resolve_cfg.get("import_wait_seconds", 10))

				# Apply LUT to reels timeline
				if resolve_cfg.get("apply_lut_after_import", True):
					cmd = [
						python,
						str(base_dir / "apply_lut_resolve.py"),
						"--config",
						str(Path(args.config).resolve()),
						"--mode",
						resolve_cfg.get("apply_lut_mode", "mediapool"),
						"--property-key",
						resolve_cfg.get("lut_property_key", "Input LUT"),
					]
					run_stage("Apply LUT to Reels", cmd, base_dir)

				# Render reels
				reels_render_cfg = reels_cfg.get("render_settings", {})
				youtube_cfg = config.get("youtube", {})
				upload_title = youtube_cfg.get("upload_title") or youtube_cfg.get("default_title", "")
				if upload_title:
					import re as _re
					safe_name = _re.sub(r'[^\w\s-]', '', upload_title).strip()
					safe_name = _re.sub(r'[\s]+', '_', safe_name)
					reels_output_name = f"{safe_name}_shorts.mp4"
				else:
					reels_output_name = "reels_output_shorts.mp4"

				reels_render_dir = reels_render_cfg.get("output_dir", "/home/mazsola/Videos")

				print("\n" + "=" * 72)
				print("📱 REELS RENDER CONFIRMATION")
				print("=" * 72)
				print("\n📺 In DaVinci Resolve:")
				print("   - Review the Reels timeline")
				print("   - Verify vertical format (1080x1920)")
				print("   - Check audio levels")
				print(f"   - Total duration must be < {reels_cfg.get('max_duration_seconds', 59)}s")
				print(f"\n📤 Output: {Path(reels_render_dir) / reels_output_name}")
				print("=" * 72)

				if auto_yes:
					print("\n✓ Auto-confirmed (--yes): Starting reels render...")
				else:
					while True:
						response = input("\nProceed with Reels render? (y/N): ").strip().lower()
						if response in ('y', 'yes'):
							print("✓ Starting reels render...")
							break
						elif response in ('n', 'no', ''):
							print("✗ Reels render cancelled")
							reels_has_clips = False
							break
						else:
							print("Please enter 'y' or 'n'")

				if reels_has_clips:
					cmd = [
						python,
						str(base_dir / "render_reels.py"),
						"--output",
						reels_output_name,
						"--config",
						str(Path(args.config).resolve()),
					]
					print(f"\n▶ 📱 [R3/4] Render Reels (1080x1920)")
					print("-" * 72)
					print(" ".join(cmd))
					print("-" * 72)

					try:
						result = subprocess.run(cmd, cwd=base_dir, timeout=300)
						if result.returncode == 0:
							print(f"✔ Reels render completed")
						else:
							print(f"\n⚠️  Reels render issues (exit code {result.returncode})")
					except subprocess.TimeoutExpired:
						print("\n⚠️  Reels render timeout - check Resolve")

					# YouTube Shorts Upload
					actual_reels_path = Path(reels_render_dir) / reels_output_name
					print(f"\n🔍 Checking for rendered file: {actual_reels_path}")
					if actual_reels_path.exists():
						related_video_id = reels_cfg.get("related_video_id", "")

						print("\n" + "=" * 72)
						print("📤 YOUTUBE SHORTS UPLOAD")
						print("=" * 72)
						print(f"   Video:    {actual_reels_path}")
						print(f"   Size:     {actual_reels_path.stat().st_size / (1024 * 1024):.1f} MB")
						print(f"   Title:    {upload_title} #shorts")
						if related_video_id:
							print(f"   Related:  https://www.youtube.com/watch?v={related_video_id}")
						print("=" * 72)

						if auto_yes:
							response = 'y'
							print("\n✓ Auto-confirmed (--yes): Starting YouTube Shorts upload...")
						else:
							response = ''
							while True:
								response = input("\nUpload Shorts to YouTube? (y/N): ").strip().lower()
								if response in ('y', 'yes', 'n', 'no', ''):
									break
								print("Please enter 'y' or 'n'")
						if response in ('y', 'yes'):
							print("✓ Starting YouTube Shorts upload...")
							upload_cmd = [
								python,
								str(base_dir / "upload_youtube.py"),
								"--video",
								str(actual_reels_path),
								"--config",
								str(base_dir / "project_config.json"),
								"--shorts",
							]
							if related_video_id:
								upload_cmd.extend(["--related-video", related_video_id])

							print(f"\n▶ 📱 [R4/4] Upload Shorts to YouTube")
							print("-" * 72)
							print(" ".join(upload_cmd))
							print("-" * 72)
							try:
								upload_result = subprocess.run(upload_cmd, cwd=base_dir)
								if upload_result.returncode == 0:
									print(f"✔ YouTube Shorts upload completed")
								else:
									print(f"\n⚠️  Upload issues (exit code {upload_result.returncode})")
							except Exception as e:
								print(f"\n⚠️  Upload error: {e}")
						else:
							print("✗ Shorts upload skipped")
					else:
						print(f"\n⚠️  Rendered file not found: {actual_reels_path}")
						print("   Upload skipped — check Resolve render output directory")

		print("\n✅ Pipeline complete")
		print("Import all clips to Resolve Media Pool before importing the XML.")

	except subprocess.CalledProcessError as exc:
		print(f"\n❌ Pipeline failed at: {exc.cmd}")
		sys.exit(exc.returncode)


if __name__ == "__main__":
	main()
