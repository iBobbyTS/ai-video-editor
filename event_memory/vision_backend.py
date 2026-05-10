from __future__ import annotations

import json
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .models import AnalysisSegment
from .frame_sampler import extract_segment_frames, make_contact_sheet
from .llm_client import OpenAICompatibleClient, image_data_url, parse_json_object


EVENT_ROLE_SET = {
    "opening",
    "arrival",
    "location_context",
    "walking",
    "group_photo",
    "portrait_moment",
    "smiling_reaction",
    "main_activity",
    "animal_subject",
    "environment_broll",
    "venue_detail",
    "interaction",
    "transition",
    "closing",
    "dead_time",
    "uncertain",
}


class VisionBackend(ABC):
    @abstractmethod
    def analyze(self, segment: AnalysisSegment) -> AnalysisSegment:
        """Return an analyzed segment without mutating external state."""


class MockVisionBackend(VisionBackend):
    """Deterministic filename/note based backend for tests and dry-runs."""

    KEYWORD_ROLES = (
        (("opening", "start", "intro"), "opening"),
        (("arrival", "arrive", "entrance"), "arrival"),
        (("location", "venue", "sign", "map", "building"), "location_context"),
        (("walk", "walking", "corridor"), "walking"),
        (("group", "team", "club", "class"), "group_photo"),
        (("portrait", "person", "kid", "student"), "portrait_moment"),
        (("smile", "laugh", "reaction", "happy"), "smiling_reaction"),
        (("activity", "workshop", "game", "show", "demo"), "main_activity"),
        (("animal", "zoo", "bear", "lion", "penguin", "giraffe", "elephant"), "animal_subject"),
        (("scenery", "garden", "landscape", "exhibit"), "environment_broll"),
        (("detail", "closeup", "close-up", "ticket", "poster"), "venue_detail"),
        (("interaction", "talk", "feed", "meet"), "interaction"),
        (("transition", "between"), "transition"),
        (("closing", "end", "final", "bye"), "closing"),
        (("dead", "boring", "empty", "accidental", "pocket"), "dead_time"),
    )

    def analyze(self, segment: AnalysisSegment) -> AnalysisSegment:
        text = " ".join(
            [
                segment.file_name.lower(),
                segment.preferred_use.lower(),
                " ".join(segment.notes).lower(),
                " ".join(note.human_note.lower() for note in segment.human_notes),
                " ".join(note.event_role.lower() for note in segment.human_notes),
            ]
        )

        role = segment.event_role if segment.event_role != "uncertain" else "uncertain"
        labels: list[str] = []
        for keywords, keyword_role in self.KEYWORD_ROLES:
            if any(keyword in text for keyword in keywords):
                labels.append(keyword_role)
                if role == "uncertain":
                    role = keyword_role

        if role == "uncertain" and segment.media_type == "image":
            role = "portrait_moment"
            labels.append("memory_photo")

        segment.event_role = role
        segment.labels = sorted(set(segment.labels + labels))
        if role == "dead_time":
            segment.visual_quality = 0.2
        elif segment.media_type == "image":
            segment.visual_quality = 0.68
        else:
            segment.visual_quality = 0.55
        return segment


def _analysis_prompt(segment: AnalysisSegment) -> str:
    return f"""Analyze this event-memory recap media segment.

Context:
- File: {segment.file_name}
- Media type: {segment.media_type}
- Segment: {segment.start_sec:.1f}s to {(segment.end_sec if segment.end_sec is not None else segment.duration_sec):.1f}s
- Human notes: {'; '.join(segment.notes) if segment.notes else 'none'}

Return JSON only with this schema:
{{
  "event_role": "one of opening, arrival, location_context, walking, group_photo, portrait_moment, smiling_reaction, main_activity, animal_subject, environment_broll, venue_detail, interaction, transition, closing, dead_time, uncertain",
  "summary": "short factual description of visible content, or unknown if unclear",
  "labels": ["short labels"],
  "visual_quality": 0.0,
  "recap_value": 0.0,
  "confidence": 0.0,
  "is_accidental_recording": false,
  "reason": "brief reason"
}}

Score low and use event_role dead_time or uncertain when content is unclear, accidental recording, empty floor/sky/pocket footage, long filler walking, unusable shake, or no meaningful event is visible.
Prioritize clear people, group photos, smiles, arrival/location context, main activity, animals/exhibits, venue details, and closing moments."""


def _apply_model_result(segment: AnalysisSegment, data: dict[str, Any]) -> AnalysisSegment:
    role = str(data.get("event_role") or "uncertain").strip().lower()
    if role not in EVENT_ROLE_SET:
        role = "uncertain"
    if segment.event_role == "uncertain" or role in {"dead_time", "uncertain"}:
        segment.event_role = role
    else:
        segment.event_role = role
    labels = data.get("labels") if isinstance(data.get("labels"), list) else []
    segment.labels = sorted(set(segment.labels + [str(label).strip().lower() for label in labels if str(label).strip()]))
    segment.summary = str(data.get("summary") or data.get("reason") or "").strip()
    reason = str(data.get("reason") or "").strip()
    if reason:
        segment.notes.append(reason)
    for key, attr in [("visual_quality", "visual_quality"), ("recap_value", "recap_value"), ("confidence", "analysis_confidence")]:
        try:
            value = float(data.get(key))
        except (TypeError, ValueError):
            continue
        setattr(segment, attr, max(0.0, min(1.0, value)))
    if data.get("is_accidental_recording") is True and not segment.must_include:
        segment.event_role = "dead_time"
        segment.avoid_use = True
        segment.notes.append("Model marked this as accidental recording.")
    return segment


def _result_from_freeform_text(text: str) -> dict[str, Any]:
    lowered = text.lower()
    role = "uncertain"
    dead_time_negated = any(
        phrase in lowered
        for phrase in [
            "no dead_time",
            "not dead_time",
            "not really dead_time",
            "not a dead time",
            "not an accidental",
            "not accidental",
        ]
    )
    keyword_roles = [
        (("arrival", "entrance", "entering"), "arrival"),
        (("group photo", "group shot", "posed group"), "group_photo"),
        (("smiling", "smile", "laugh", "reaction"), "smiling_reaction"),
        (("animal", "zoo", "exhibit", "bear", "lion", "penguin", "giraffe", "elephant"), "animal_subject"),
        (("interaction", "talking", "facing each other", "dialogue"), "interaction"),
        (("walking", "walk"), "walking"),
        (("venue", "location", "background", "building", "sign"), "location_context"),
        (("closing", "ending", "final"), "closing"),
        (("activity", "main activity", "event"), "main_activity"),
        (("portrait", "person", "people", "character"), "portrait_moment"),
        (("environment", "scenery", "landscape"), "environment_broll"),
        (("detail", "close-up", "closeup"), "venue_detail"),
    ]
    for keywords, candidate_role in keyword_roles:
        if any(keyword in lowered for keyword in keywords):
            role = candidate_role
            break
    if role == "uncertain" and not dead_time_negated and any(
        keyword in lowered for keyword in ("dead time", "accidental", "pocket", "empty", "unclear", "not meaningful")
    ):
        role = "dead_time"
    if "clear" in lowered or "visible" in lowered:
        quality = 0.65
    elif role in {"dead_time", "uncertain"}:
        quality = 0.25
    else:
        quality = 0.55
    if role in {"dead_time", "uncertain"}:
        recap_value = 0.2
    elif role in {"group_photo", "smiling_reaction", "main_activity", "animal_subject", "interaction"}:
        recap_value = 0.75
    else:
        recap_value = 0.55
    return {
        "event_role": role,
        "summary": text.strip()[:1200] or "unknown",
        "labels": [role],
        "visual_quality": quality,
        "recap_value": recap_value,
        "confidence": 0.45,
        "is_accidental_recording": role == "dead_time",
        "reason": "Structured from Qwen free-form response.",
    }


def _parse_model_result(text: str) -> dict[str, Any]:
    try:
        return parse_json_object(text)
    except Exception:
        return _result_from_freeform_text(text)


class LMStudioHTTPVisionBackend(VisionBackend):
    def __init__(
        self,
        base_url: str = "http://192.168.31.76:1234/v1",
        api_key: str = "lm-studio",
        model: str = "qwen3.6-35b-a3b",
        ffmpeg_bin: str | None = None,
        frames_dir: Path | None = None,
        timeout: int = 240,
    ) -> None:
        self.client = OpenAICompatibleClient(base_url=base_url, api_key=api_key, model=model, timeout=timeout)
        self.ffmpeg_bin = ffmpeg_bin
        self.frames_dir = frames_dir

    def analyze(self, segment: AnalysisSegment) -> AnalysisSegment:
        frames_dir = self.frames_dir or Path(tempfile.mkdtemp(prefix="event_memory_frames_"))
        frames = extract_segment_frames(segment, frames_dir, ffmpeg_bin=self.ffmpeg_bin)
        sheet = make_contact_sheet(frames, frames_dir / f"{segment.segment_id}_sheet.jpg")
        content: list[dict[str, Any]] = [{"type": "text", "text": _analysis_prompt(segment)}]
        content.append({"type": "image_url", "image_url": {"url": image_data_url(sheet)}})
        text = self.client.chat(
            [
                {"role": "system", "content": "You are a strict video/event recap visual analyst. Return JSON only."},
                {"role": "user", "content": content},
            ],
            temperature=0.0,
            max_tokens=1200,
        )
        if not text.strip():
            raise ValueError("LM Studio returned an empty response")
        return _apply_model_result(segment, _parse_model_result(text))


class MLXVLMQwenVisionBackend(VisionBackend):
    def __init__(
        self,
        model_path: str = "/Users/ibobby/.lmstudio/models/mlx-community/Qwen3.6-35B-A3B-mxfp4",
        ffmpeg_bin: str | None = None,
        frames_dir: Path | None = None,
    ) -> None:
        self.model_path = model_path
        self.ffmpeg_bin = ffmpeg_bin
        self.frames_dir = frames_dir
        self._model = None
        self._processor = None
        self._config = None

    def _load(self):
        if self._model is not None and self._processor is not None and self._config is not None:
            return self._model, self._processor, self._config
        from mlx_vlm import generate, load
        from mlx_vlm.prompt_utils import apply_chat_template
        from mlx_vlm.utils import load_config

        self._generate = generate
        self._apply_chat_template = apply_chat_template
        self._model, self._processor = load(self.model_path)
        self._config = load_config(self.model_path)
        return self._model, self._processor, self._config

    def analyze(self, segment: AnalysisSegment) -> AnalysisSegment:
        frames_dir = self.frames_dir or Path(tempfile.mkdtemp(prefix="event_memory_frames_"))
        frames = extract_segment_frames(segment, frames_dir, ffmpeg_bin=self.ffmpeg_bin)
        if not frames:
            raise RuntimeError("No frames extracted for mlx-vlm analysis")
        sheet = make_contact_sheet(frames, frames_dir / f"{segment.segment_id}_sheet.jpg")
        model, processor, _config = self._load()
        prompt = self._apply_chat_template(processor, _config, _analysis_prompt(segment), num_images=1)
        result = self._generate(
            model,
            processor,
            prompt,
            image=str(sheet),
            verbose=False,
            max_tokens=1200,
            temperature=0.0,
            resize_shape=(720, 1280),
        )
        text = getattr(result, "text", None) or str(result)
        return _apply_model_result(segment, _parse_model_result(text))


class QwenVisionBackend(VisionBackend):
    def __init__(
        self,
        ffmpeg_bin: str | None = None,
        frames_dir: Path | None = None,
        mlx_model_path: str = "/Users/ibobby/.lmstudio/models/mlx-community/Qwen3.6-35B-A3B-mxfp4",
        lmstudio_base_url: str = "http://192.168.31.76:1234/v1",
        lmstudio_model: str = "qwen3.6-35b-a3b",
        lmstudio_api_key: str = "lm-studio",
        allow_mock_fallback: bool = True,
    ) -> None:
        self.backends: list[VisionBackend] = [
            MLXVLMQwenVisionBackend(model_path=mlx_model_path, ffmpeg_bin=ffmpeg_bin, frames_dir=frames_dir),
            LMStudioHTTPVisionBackend(
                base_url=lmstudio_base_url,
                api_key=lmstudio_api_key,
                model=lmstudio_model,
                ffmpeg_bin=ffmpeg_bin,
                frames_dir=frames_dir,
            ),
        ]
        self.mock = MockVisionBackend() if allow_mock_fallback else None
        self.disabled_backend_names: set[str] = set()

    def analyze(self, segment: AnalysisSegment) -> AnalysisSegment:
        errors: list[str] = []
        for backend in self.backends:
            backend_name = backend.__class__.__name__
            if backend_name in self.disabled_backend_names:
                continue
            try:
                return backend.analyze(segment)
            except Exception as exc:
                errors.append(f"{backend_name}: {exc}")
                if backend_name == "MLXVLMQwenVisionBackend":
                    self.disabled_backend_names.add(backend_name)
        segment.notes.append("Qwen analysis failed; using mock fallback. " + " | ".join(errors[:2]))
        if self.mock:
            return self.mock.analyze(segment)
        raise RuntimeError("; ".join(errors))


def create_backend(name: str, dry_run: bool = True, **kwargs) -> VisionBackend:
    backend_name = (name or "mock").lower()
    if dry_run or backend_name == "mock":
        return MockVisionBackend()
    if backend_name in {"qwen", "qwen3.6", "qwen3.6-35b-a3b"}:
        return QwenVisionBackend(**kwargs)
    if backend_name == "lmstudio":
        return LMStudioHTTPVisionBackend(
            base_url=kwargs.get("lmstudio_base_url", "http://192.168.31.76:1234/v1"),
            api_key=kwargs.get("lmstudio_api_key", "lm-studio"),
            model=kwargs.get("lmstudio_model", "qwen3.6-35b-a3b"),
            ffmpeg_bin=kwargs.get("ffmpeg_bin"),
            frames_dir=kwargs.get("frames_dir"),
        )
    if backend_name == "mlx-vlm":
        return MLXVLMQwenVisionBackend(
            model_path=kwargs.get("mlx_model_path", "/Users/ibobby/.lmstudio/models/mlx-community/Qwen3.6-35B-A3B-mxfp4"),
            ffmpeg_bin=kwargs.get("ffmpeg_bin"),
            frames_dir=kwargs.get("frames_dir"),
        )
    raise ValueError(f"Unsupported event_memory vision backend: {name}")
