import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from scripts.video_factory import _atempo_filter, conform_narration_speed
from video_factory.core import (
    FACTORY_ROOT,
    build_adapter_prompt,
    compile_shot_manifest,
    load_project,
    split_script_by_target_words,
    stable_hash,
    write_srt,
)


EXAMPLE_PROJECT = (
    FACTORY_ROOT
    / "example_projects"
    / "mosquito_control"
    / "project.yaml"
)


class ProjectCompilationTests(unittest.TestCase):
    def test_example_project_compiles_to_expected_modular_mix(self):
        context = load_project(EXAMPLE_PROJECT)
        manifest = compile_shot_manifest(context)

        self.assertEqual(manifest["project"], "mosquito-control-pilot")
        self.assertEqual(manifest["word_count"], 239)
        self.assertEqual(len(manifest["shots"]), 20)
        self.assertAlmostEqual(manifest["duration"], 239 / 163 * 60, places=3)
        self.assertEqual(
            Counter(shot["type"] for shot in manifest["shots"]),
            Counter({"broll": 8, "still": 7, "presenter": 5}),
        )

    def test_compiled_timeline_is_contiguous_and_ends_at_total_duration(self):
        manifest = compile_shot_manifest(load_project(EXAMPLE_PROJECT))
        previous_end = 0.0
        for shot in manifest["shots"]:
            self.assertAlmostEqual(shot["start"], previous_end, places=3)
            self.assertGreater(shot["duration"], 0)
            previous_end = shot["end"]
        self.assertAlmostEqual(previous_end, manifest["duration"], places=3)

    def test_storyboard_prompts_remain_content_specific_but_use_generic_engines(self):
        manifest = compile_shot_manifest(load_project(EXAMPLE_PROJECT))
        first = manifest["shots"][0]
        presenter = next(shot for shot in manifest["shots"] if shot["type"] == "presenter")

        self.assertEqual(first["engine"], "ltx25_t2v")
        self.assertIn("dark bucket", first["prompt"].lower())
        self.assertEqual(presenter["engine"], "ltx23_presenter")
        self.assertIn("same fictional rural homesteader", presenter["prompt"].lower())

    def test_writes_valid_srt_from_shot_timing(self):
        manifest = compile_shot_manifest(load_project(EXAMPLE_PROJECT))
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "captions.srt"
            write_srt(manifest, path)
            text = path.read_text(encoding="utf-8")

        self.assertIn("1\n00:00:00,000 -->", text)
        self.assertIn(manifest["shots"][0]["narration"], text)
        self.assertIn("20\n", text)


class AdapterTests(unittest.TestCase):
    def test_z_image_adapter_patches_named_slots_without_graph_edits(self):
        _, prompt = build_adapter_prompt(
            "z_image_turbo",
            {
                "prompt": "A completely different product in a different room.",
                "seed": 77,
                "width": 1024,
                "height": 576,
                "output_prefix": "video_factory/test/shot",
            },
        )

        self.assertEqual(prompt["45"]["inputs"]["text"], "A completely different product in a different room.")
        self.assertEqual(prompt["44"]["inputs"]["seed"], 77)
        self.assertEqual(prompt["41"]["inputs"]["width"], 1024)
        self.assertEqual(prompt["9"]["inputs"]["filename_prefix"], "video_factory/test/shot")

    def test_ltx_presenter_adapter_replaces_person_audio_and_duration(self):
        _, prompt = build_adapter_prompt(
            "ltx23_presenter",
            {
                "prompt": "A different avatar speaks in a different environment.",
                "seed": 88,
                "duration": 5.25,
                "image": "video_factory/test/avatar.png",
                "audio": "video_factory/test/narration.wav",
                "output_prefix": "video_factory/test/presenter",
            },
        )

        self.assertEqual(prompt["269"]["inputs"]["image"], "video_factory/test/avatar.png")
        self.assertEqual(prompt["346"]["inputs"]["audio"], "video_factory/test/narration.wav")
        self.assertEqual(prompt["340:331"]["inputs"]["value"], 5.25)
        self.assertNotIn("gavin", json.dumps(prompt).lower())

    def test_stable_hash_ignores_dictionary_insertion_order(self):
        self.assertEqual(stable_hash({"a": 1, "b": 2}), stable_hash({"b": 2, "a": 1}))


class NarrationConformanceTests(unittest.TestCase):
    def test_atempo_filter_supports_rates_above_ffmpeg_single_filter_limit(self):
        self.assertEqual(_atempo_filter(4.0), "atempo=2.00000000,atempo=2.00000000")

    def test_conformance_uses_script_word_count_and_target_wpm(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "raw.flac"
            target = Path(temp_dir) / "conformed.wav"
            source.write_bytes(b"raw")
            with patch("scripts.video_factory.ffprobe_duration", return_value=120.0), patch(
                "scripts.video_factory.subprocess.run"
            ) as run:
                metrics = conform_narration_speed(source, target, words=240, target_wpm=160)

        self.assertEqual(metrics["target_duration"], 90.0)
        self.assertAlmostEqual(metrics["speed_factor"], 4 / 3)
        command = run.call_args.args[0]
        self.assertIn("atempo=1.33333333", command)


class AutomaticStoryboardTests(unittest.TestCase):
    def test_script_splitter_produces_short_reusable_chunks(self):
        text = (
            "This is the first concise sentence. "
            "This second sentence explains a completely different product. "
            "The third sentence changes the environment again."
        )
        chunks = split_script_by_target_words(text, target_words=9)

        self.assertGreaterEqual(len(chunks), 2)
        self.assertEqual(" ".join(chunks), text)


if __name__ == "__main__":
    unittest.main()
