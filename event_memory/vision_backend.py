from __future__ import annotations

from abc import ABC, abstractmethod

from .models import AnalysisSegment


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


class LMStudioVisionBackend(VisionBackend):
    def __init__(self, *_args, **_kwargs) -> None:
        raise NotImplementedError("LM Studio backend is reserved for a later implementation.")


def create_backend(name: str, dry_run: bool = True) -> VisionBackend:
    backend_name = (name or "mock").lower()
    if dry_run or backend_name == "mock":
        return MockVisionBackend()
    if backend_name == "lmstudio":
        return LMStudioVisionBackend()
    raise ValueError(f"Unsupported event_memory vision backend: {name}")
