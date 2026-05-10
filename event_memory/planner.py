from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .llm_client import OpenAICompatibleClient, parse_json_object
from .models import ScoredSegment


@dataclass
class PlannerResult:
    candidates: list[ScoredSegment]
    used_model: bool
    notes: list[str]
    raw_response: str = ""


def _segment_brief(item: ScoredSegment) -> dict:
    segment = item.segment
    return {
        "segment_id": segment.segment_id,
        "file_name": segment.file_name,
        "media_type": segment.media_type,
        "start_sec": segment.start_sec,
        "end_sec": segment.end_sec,
        "duration_sec": segment.duration_sec,
        "event_role": segment.event_role,
        "score": item.score,
        "summary": segment.summary,
        "labels": segment.labels,
        "must_include": segment.must_include,
        "preferred_use": segment.preferred_use,
        "notes": segment.notes[:3],
    }


class HeuristicTimelinePlanner:
    def plan(self, candidates: list[ScoredSegment], target_duration_sec: float) -> PlannerResult:
        return PlannerResult(candidates=candidates, used_model=False, notes=["heuristic planner"])


class GPT55FinalTimelinePlanner:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "gpt-5.5",
        log_path: Path | None = None,
    ) -> None:
        self.client = OpenAICompatibleClient(base_url=base_url, api_key=api_key, model=model, timeout=240)
        self.log_path = log_path

    def plan(self, candidates: list[ScoredSegment], target_duration_sec: float) -> PlannerResult:
        allowed = [item for item in candidates if not item.excluded]
        by_id = {item.segment.segment_id: item for item in allowed}
        must_ids = {item.segment.segment_id for item in allowed if item.segment.must_include}
        prompt = {
            "task": "Create the final clip order for a warm event-memory recap video.",
            "style": "gentle chronological activity recap, clear people/group/activity/context shots, stable simple editing",
            "target_duration_sec": target_duration_sec,
            "hard_rules": [
                "Only use segment_id values from the provided candidate list.",
                "Do not include dead_time, unclear filler, or repeated walking unless must_include is true.",
                "All must_include segment ids must be present.",
                "Prefer chronological flow with opening/context first and closing/group moment last.",
                "Return JSON only.",
            ],
            "return_schema": {
                "selected_segment_ids": ["segment id in final order"],
                "editor_notes": ["short note"],
            },
            "candidates": [_segment_brief(item) for item in allowed],
        }
        raw = self.client.chat(
            [
                {"role": "system", "content": "You are a final cut editor. Return strict JSON only."},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            temperature=0.1,
            max_tokens=4096,
        )
        data = parse_json_object(raw)
        selected_ids = data.get("selected_segment_ids")
        if not isinstance(selected_ids, list):
            raise ValueError("GPT planner response missing selected_segment_ids")

        ordered: list[ScoredSegment] = []
        seen: set[str] = set()
        for value in selected_ids:
            segment_id = str(value)
            if segment_id in by_id and segment_id not in seen:
                ordered.append(by_id[segment_id])
                seen.add(segment_id)

        for segment_id in must_ids:
            if segment_id not in seen:
                ordered.append(by_id[segment_id])
                seen.add(segment_id)

        if not ordered:
            raise ValueError("GPT planner selected no valid segments")

        notes = data.get("editor_notes") if isinstance(data.get("editor_notes"), list) else []
        notes = [str(note) for note in notes]
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_path.write_text(raw, encoding="utf-8")
        return PlannerResult(candidates=ordered, used_model=True, notes=notes, raw_response=raw)


def create_timeline_planner(
    name: str,
    openai_base_url: str = "",
    openai_api_key: str = "",
    model: str = "gpt-5.5",
    log_path: Path | None = None,
):
    planner_name = (name or "heuristic").lower()
    if planner_name in {"heuristic", "mock", "none"}:
        return HeuristicTimelinePlanner()
    if planner_name in {"gpt-5.5", "gpt55", "gpt"}:
        return GPT55FinalTimelinePlanner(base_url=openai_base_url, api_key=openai_api_key, model=model, log_path=log_path)
    raise ValueError(f"Unsupported event_memory timeline planner: {name}")
