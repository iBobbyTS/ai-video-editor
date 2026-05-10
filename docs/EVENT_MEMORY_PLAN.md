# event_memory MVP Implementation Plan

## Goal

Add a deterministic `event_memory` mode for reviewable activity and event recap videos built from real footage and photos.

This mode is not a generated-video workflow and does not introduce a generic agent framework. The first version produces inspectable JSON artifacts and can run in dry-run/mock mode without real model calls.

## Scope

Implemented in this MVP:

- Scan mixed media inputs: `.mp4`, `.mov`, `.m4v`, `.jpg`, `.jpeg`, `.png`, and `.heic` when metadata can be read.
- Write `media_index.json` with deterministic asset IDs and media metadata.
- Create video and image analysis segments.
- Split long videos into fixed windows with small overlap.
- Treat each image as a still clip with a subtle deterministic motion preset.
- Merge optional `human_notes.csv`.
- Use a mock vision backend for dry-run analysis.
- Score event-memory candidates with human-note boosts and exclusion rules.
- Write `analysis_segments.json`, `scored_segments.json`, `candidate_events.json`, and `timeline.json`.
- Optionally render `review_preview.mp4` with FFmpeg when `--render-preview` is passed.
- Optionally export a simple FCPXML 1.14 `event_memory.fcpxml` when timeline data is available, using `media-rep` children for asset file references.

Intentionally left for later:

- Subtitles.
- AI voiceover.
- Background music selection.
- Beat-sync editing.
- Commercial polish.
- Real VLM integration beyond a backend interface.
- CUDA/NVIDIA/NVENC assumptions.

## Integration Strategy

Keep the existing `build`, `unboxing`, and `reels` pipeline intact. Add an early `event_memory` branch in `run_pipeline.py` that calls a lightweight Python module instead of the existing CUDA-heavy analysis pipeline.

The new implementation lives under `event_memory/`:

- `models.py`: dataclasses and JSON serialization helpers.
- `media_indexer.py`: media scanning and metadata probing.
- `notes.py`: `human_notes.csv` parsing and matching.
- `segmentation.py`: video windowing and image segment creation.
- `vision_backend.py`: backend abstraction and mock analysis.
- `scoring.py`: event-specific scoring and candidate selection.
- `timeline.py`: neutral timeline generation and markdown review output.
- `preview.py`: optional FFmpeg preview rendering and simple FCPXML export.
- `pipeline.py`: orchestration used by the CLI.

## Test Strategy

Add focused unit tests under `tests/` that use mocked metadata and tiny fake files where possible. Tests must not require real video models, cloud APIs, CUDA, or large media fixtures.

Minimum coverage:

- Mixed media indexing recognizes video and image files.
- Image assets become image analysis segments.
- Long videos split into fixed windows.
- `human_notes.csv` merges correctly.
- `must_include` and `avoid_use` influence scoring.
- Timeline includes video and image clips.
- Image motion presets are assigned.
- Dry-run/mock pipeline completes without model calls.
