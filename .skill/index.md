# Project Skill Index

This repository did not previously have project-specific Codex skill guidance.

Current reusable workflow:

- Inspect the existing Python CLI and pipeline before adding new modes.
- Prefer deterministic JSON outputs and testable Python modules over external services.
- Use dry-run/mock paths for features that would otherwise require local AI models or large media fixtures.
- For media and preview work, prefer FFmpeg-compatible implementations that run on macOS without CUDA/NVIDIA assumptions.
- For Final Cut Pro XML exports targeting current Final Cut Pro, use FCPXML 1.14; `asset` elements must contain one or more `media-rep` children, and file URLs belong on `media-rep src`, not `asset src`.
- For `event_memory` work, keep the lightweight pipeline under `event_memory/` deterministic and inspectable; do not route it through CUDA-heavy `analyze_advanced5.py`.
- Test new `event_memory` behavior with `python -m unittest tests.test_event_memory`.
