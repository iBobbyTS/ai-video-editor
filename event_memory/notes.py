from __future__ import annotations

import csv
from pathlib import Path

from .models import AnalysisSegment, HumanNote, MediaAsset


def parse_bool(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_time(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if ":" not in text:
        try:
            return float(text)
        except ValueError:
            return None
    parts = text.split(":")
    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        return None
    seconds = 0.0
    for number in numbers:
        seconds = seconds * 60 + number
    return seconds


def load_human_notes(input_dir: Path) -> list[HumanNote]:
    notes_path = Path(input_dir) / "human_notes.csv"
    if not notes_path.exists():
        return []
    notes: list[HumanNote] = []
    with notes_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            file_name = (row.get("file") or "").strip()
            if not file_name:
                continue
            notes.append(
                HumanNote(
                    file=file_name,
                    start=parse_time(row.get("start")),
                    end=parse_time(row.get("end")),
                    human_note=(row.get("human_note") or "").strip(),
                    importance=(row.get("importance") or "").strip().lower(),
                    must_include=parse_bool(row.get("must_include")),
                    avoid_use=parse_bool(row.get("avoid_use")),
                    event_role=(row.get("event_role") or "").strip().lower(),
                    preferred_use=(row.get("preferred_use") or "").strip().lower(),
                )
            )
    return notes


def merge_notes_into_assets(assets: list[MediaAsset], notes: list[HumanNote]) -> None:
    notes_by_file: dict[str, list[HumanNote]] = {}
    for note in notes:
        notes_by_file.setdefault(note.file.lower(), []).append(note)

    for asset in assets:
        file_name = Path(asset.source_path).name.lower()
        asset.human_notes = list(notes_by_file.get(file_name, []))


def _note_overlaps_segment(note: HumanNote, segment: AnalysisSegment) -> bool:
    if note.start is None and note.end is None:
        return True
    note_start = note.start if note.start is not None else 0.0
    note_end = note.end if note.end is not None else note_start
    segment_end = segment.end_sec if segment.end_sec is not None else segment.start_sec + segment.duration_sec

    if note.end is None and note.start is not None:
        return segment.start_sec <= note.start <= segment_end
    return note_start < segment_end and note_end > segment.start_sec


def merge_notes_into_segments(segments: list[AnalysisSegment], notes: list[HumanNote]) -> None:
    notes_by_file: dict[str, list[HumanNote]] = {}
    for note in notes:
        notes_by_file.setdefault(note.file.lower(), []).append(note)

    for segment in segments:
        file_notes = notes_by_file.get(segment.file_name.lower(), [])
        segment.human_notes = [note for note in file_notes if _note_overlaps_segment(note, segment)]
        if not segment.human_notes:
            continue
        segment.must_include = any(note.must_include for note in segment.human_notes)
        segment.avoid_use = any(note.avoid_use for note in segment.human_notes)
        preferred = [note.preferred_use for note in segment.human_notes if note.preferred_use]
        if preferred:
            segment.preferred_use = preferred[0]
        for note in segment.human_notes:
            if note.human_note:
                segment.notes.append(note.human_note)
            if note.event_role:
                segment.event_role = note.event_role
            if note.preferred_use == "do_not_use":
                segment.avoid_use = True
            if note.importance == "reject":
                segment.avoid_use = True
