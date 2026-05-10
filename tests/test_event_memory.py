import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from event_memory.media_indexer import index_media
from event_memory.models import AnalysisSegment, MediaAsset, Timeline, TimelineClip
from event_memory.notes import load_human_notes, merge_notes_into_segments
from event_memory.pipeline import EventMemoryOptions, run_event_memory_pipeline
from event_memory.preview import render_preview, write_simple_fcpxml
from event_memory.scoring import score_segment, score_segments, select_candidates
from event_memory.segmentation import create_analysis_segments, create_video_segments
from event_memory.timeline import build_timeline


def _asset(path: Path, media_type: str, duration: float | None = None, width: int = 1920, height: int = 1080, order: int = 0):
    return MediaAsset(
        media_id=f"media_{order + 1:04d}_{path.stem}",
        source_path=str(path),
        media_type=media_type,
        duration_sec=duration,
        width=width,
        height=height,
        fps=30.0 if media_type == "video" else None,
        codec="h264" if media_type == "video" else None,
        created_at="2026-01-01T00:00:00+00:00",
        sort_order=order,
    )


class EventMemoryTests(unittest.TestCase):
    def test_media_indexing_recognizes_video_and_images(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "clip.mov").write_bytes(b"fake")
            (root / "photo.jpg").write_bytes(b"fake")
            (root / "notes.txt").write_text("skip")

            with patch(
                "event_memory.media_indexer.probe_video",
                return_value={"duration_sec": 10.0, "width": 1920, "height": 1080, "fps": 30.0, "codec": "h264", "created_at": None},
            ), patch(
                "event_memory.media_indexer.probe_image",
                return_value={"duration_sec": None, "width": 1200, "height": 800, "fps": None, "codec": None, "created_at": None},
            ):
                assets, warnings = index_media(root)

            self.assertEqual([asset.media_type for asset in assets], ["video", "image"])
            self.assertTrue(any("notes.txt" in warning for warning in warnings))

    def test_image_assets_become_analysis_segments_with_motion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = _asset(root / "group_photo.jpg", "image", width=1200, height=800)

            segments = create_analysis_segments([image])

            self.assertEqual(len(segments), 1)
            self.assertEqual(segments[0].media_type, "image")
            self.assertIsNotNone(segments[0].image_motion_preset)
            self.assertEqual(segments[0].duration_sec, 4.0)

    def test_long_video_clips_are_split_into_fixed_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = _asset(root / "long_activity.mp4", "video", duration=70.0)

            segments = create_video_segments(video, window_sec=25.0, overlap_sec=3.0)

            self.assertEqual(
                [(segment.start_sec, segment.end_sec) for segment in segments],
                [(0.0, 25.0), (22.0, 47.0), (44.0, 69.0), (66.0, 70.0)],
            )

    def test_human_notes_merge_correctly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "human_notes.csv").write_text(
                "file,start,end,human_note,importance,must_include,avoid_use,event_role,preferred_use\n"
                "clip.mp4,5,15,Great reaction,high,true,false,smiling_reaction,main_story\n",
                encoding="utf-8",
            )
            segment = AnalysisSegment(
                segment_id="seg1",
                media_id="media1",
                source_path=str(root / "clip.mp4"),
                file_name="clip.mp4",
                media_type="video",
                start_sec=0,
                end_sec=10,
                duration_sec=10,
                sort_order=1,
            )

            notes = load_human_notes(root)
            merge_notes_into_segments([segment], notes)

            self.assertTrue(segment.must_include)
            self.assertFalse(segment.avoid_use)
            self.assertEqual(segment.event_role, "smiling_reaction")
            self.assertEqual(segment.preferred_use, "main_story")
            self.assertEqual(segment.notes, ["Great reaction"])

    def test_must_include_and_avoid_use_affect_scoring(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            include_segment = AnalysisSegment(
                segment_id="include",
                media_id="media1",
                source_path=str(root / "group.jpg"),
                file_name="group.jpg",
                media_type="image",
                start_sec=0,
                end_sec=None,
                duration_sec=4,
                sort_order=1,
                event_role="group_photo",
                must_include=True,
            )
            avoid_segment = AnalysisSegment(
                segment_id="avoid",
                media_id="media2",
                source_path=str(root / "empty.mp4"),
                file_name="empty.mp4",
                media_type="video",
                start_sec=0,
                end_sec=5,
                duration_sec=5,
                sort_order=2,
                event_role="dead_time",
                avoid_use=True,
            )

            include_score = score_segment(include_segment)
            avoid_score = score_segment(avoid_segment)

            self.assertGreater(include_score.score, 150)
            self.assertTrue(avoid_score.excluded)

    def test_must_include_enters_candidates_and_avoid_use_is_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            must_segment = AnalysisSegment(
                segment_id="must",
                media_id="media1",
                source_path=str(root / "must.jpg"),
                file_name="must.jpg",
                media_type="image",
                start_sec=0,
                end_sec=None,
                duration_sec=4,
                sort_order=10,
                event_role="uncertain",
                must_include=True,
            )
            avoid_segment = AnalysisSegment(
                segment_id="avoid",
                media_id="media2",
                source_path=str(root / "avoid.mp4"),
                file_name="avoid.mp4",
                media_type="video",
                start_sec=0,
                end_sec=4,
                duration_sec=4,
                sort_order=1,
                event_role="main_activity",
                avoid_use=True,
            )

            candidates = select_candidates(score_segments([avoid_segment, must_segment]), max_candidates=1)

            self.assertEqual([candidate.segment.segment_id for candidate in candidates], ["must"])

    def test_timeline_includes_video_and_image_clips(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video_segment = AnalysisSegment(
                segment_id="video",
                media_id="media1",
                source_path=str(root / "arrival.mp4"),
                file_name="arrival.mp4",
                media_type="video",
                start_sec=0,
                end_sec=8,
                duration_sec=8,
                sort_order=1,
                event_role="arrival",
            )
            image_segment = AnalysisSegment(
                segment_id="image",
                media_id="media2",
                source_path=str(root / "group.jpg"),
                file_name="group.jpg",
                media_type="image",
                start_sec=0,
                end_sec=None,
                duration_sec=4,
                sort_order=2,
                event_role="group_photo",
                image_motion_preset="zoom_in",
            )

            timeline = build_timeline([score_segment(video_segment), score_segment(image_segment)], target_duration_sec=30)

            self.assertEqual([clip.media_type for clip in timeline.clips], ["video", "image"])
            self.assertEqual(timeline.clips[1].image_motion_preset, "zoom_in")

    def test_timeline_defaults_to_90_seconds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            segment = AnalysisSegment(
                segment_id="video",
                media_id="media1",
                source_path=str(root / "activity.mp4"),
                file_name="activity.mp4",
                media_type="video",
                start_sec=0,
                end_sec=10,
                duration_sec=10,
                sort_order=1,
                event_role="main_activity",
            )

            timeline = build_timeline([score_segment(segment)])

            self.assertEqual(timeline.target_duration_sec, 90.0)

    def test_timeline_preserve_order_for_model_planner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            later = AnalysisSegment(
                segment_id="later",
                media_id="media1",
                source_path=str(root / "later.mp4"),
                file_name="later.mp4",
                media_type="video",
                start_sec=0,
                end_sec=4,
                duration_sec=4,
                sort_order=2,
                event_role="closing",
            )
            earlier = AnalysisSegment(
                segment_id="earlier",
                media_id="media2",
                source_path=str(root / "earlier.mp4"),
                file_name="earlier.mp4",
                media_type="video",
                start_sec=0,
                end_sec=4,
                duration_sec=4,
                sort_order=1,
                event_role="opening",
            )

            timeline = build_timeline([score_segment(later), score_segment(earlier)], preserve_order=True)

            self.assertEqual([clip.segment_id for clip in timeline.clips], ["later", "earlier"])

    def test_long_video_segments_become_short_recap_shots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            segment = AnalysisSegment(
                segment_id="long",
                media_id="media1",
                source_path=str(root / "long.mp4"),
                file_name="long.mp4",
                media_type="video",
                start_sec=100,
                end_sec=130,
                duration_sec=30,
                sort_order=1,
                event_role="group_photo",
            )

            timeline = build_timeline([score_segment(segment)], target_duration_sec=90)

            self.assertEqual(len(timeline.clips), 1)
            self.assertLessEqual(timeline.clips[0].timeline_duration, 6.0)
            self.assertEqual(timeline.clips[0].source_in, 100)
            self.assertEqual(timeline.clips[0].source_out, 106)

    def test_render_preview_uses_injected_ffmpeg_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "clip.mp4"
            source.write_bytes(b"fake")
            segment = AnalysisSegment(
                segment_id="video",
                media_id="media1",
                source_path=str(source),
                file_name="clip.mp4",
                media_type="video",
                start_sec=0,
                end_sec=2,
                duration_sec=2,
                sort_order=1,
                event_role="main_activity",
            )
            timeline = build_timeline([score_segment(segment)])
            ffmpeg_bin = root / "ffmpeg"
            ffmpeg_bin.write_text("#!/bin/sh\n")
            calls = []

            def fake_run(cmd):
                calls.append(cmd)
                if "part_0001.mp4" in cmd[-1]:
                    Path(cmd[-1]).write_bytes(b"fake")

            with patch("event_memory.preview._run", side_effect=fake_run):
                render_preview(timeline, root / "out.mp4", root / "parts", ffmpeg_bin=str(ffmpeg_bin))

            self.assertEqual(calls[0][0], str(ffmpeg_bin))

    def test_simple_fcpxml_uses_modern_media_rep_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "clip with space.mp4"
            source.write_bytes(b"fake")
            output = root / "event_memory.fcpxml"
            timeline = Timeline(
                project_title="Trip Recap",
                mode="event_memory",
                target_duration_sec=8.0,
                total_duration_sec=2.0,
                clips=[
                    TimelineClip(
                        clip_id="clip_1",
                        source_path=str(source),
                        media_type="video",
                        source_in=1.0,
                        source_out=3.0,
                        timeline_duration=2.0,
                        event_role="main_activity",
                        score=0.8,
                    )
                ],
            )

            write_simple_fcpxml(timeline, output)

            text = output.read_text(encoding="utf-8")
            self.assertIn('<fcpxml version="1.14">', text)
            self.assertNotIn(" src=", text.split("<asset", 1)[1].split(">", 1)[0])
            root_element = ET.fromstring(text)
            asset = root_element.find("./resources/asset")
            self.assertIsNotNone(asset)
            self.assertNotIn("src", asset.attrib)
            media_rep = asset.find("media-rep")
            self.assertIsNotNone(media_rep)
            self.assertEqual(media_rep.attrib["kind"], "original-media")
            self.assertIn("clip%20with%20space.mp4", media_rep.attrib["src"])

    def test_dry_run_mock_pipeline_completes_without_model_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "arrival.mp4").write_bytes(b"fake")
            (root / "group_photo.jpg").write_bytes(b"fake")
            (root / "human_notes.csv").write_text(
                "file,start,end,human_note,importance,must_include,avoid_use,event_role,preferred_use\n"
                "group_photo.jpg,,,,high,true,false,group_photo,closing\n",
                encoding="utf-8",
            )
            output_dir = root / "output"

            with patch(
                "event_memory.media_indexer.probe_video",
                return_value={"duration_sec": 16.0, "width": 1920, "height": 1080, "fps": 30.0, "codec": "h264", "created_at": None},
            ), patch(
                "event_memory.media_indexer.probe_image",
                return_value={"duration_sec": None, "width": 1200, "height": 800, "fps": None, "codec": None, "created_at": None},
            ):
                timeline = run_event_memory_pipeline(
                    EventMemoryOptions(input_dir=root, output_dir=output_dir, dry_run=True, export_fcpxml=False)
                )

            self.assertTrue(timeline.clips)
            for artifact in [
                "media_index.json",
                "analysis_segments.json",
                "scored_segments.json",
                "candidate_events.json",
                "timeline.json",
            ]:
                self.assertTrue((output_dir / artifact).exists())

            data = json.loads((output_dir / "timeline.json").read_text())
            self.assertEqual({clip["media_type"] for clip in data["clips"]}, {"video", "image"})


if __name__ == "__main__":
    unittest.main()
