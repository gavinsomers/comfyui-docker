import argparse
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.video_factory import (
    _atempo_filter,
    assemble_project,
    conform_narration_speed,
    narration_path,
    render_narration,
)
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

    def test_project_schema_rejects_unimplemented_voice_clone_mode(self):
        project = yaml.safe_load(EXAMPLE_PROJECT.read_text(encoding="utf-8"))
        project["voice"]["mode"] = "voice_clone"
        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir) / "project.yaml"
            project_path.write_text(yaml.safe_dump(project), encoding="utf-8")

            with self.assertRaisesRegex(Exception, "voice_clone"):
                load_project(project_path)


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

    def test_raw_narration_becomes_active_when_conformance_is_disabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            script = root / "script.md"
            script.write_text("A short narration.", encoding="utf-8")
            raw_audio = root / "raw.wav"
            raw_audio.write_bytes(b"raw audio")
            state = {"version": 1, "assets": {}}
            state_path = root / "state.json"
            context = {
                "slug": "test-project",
                "script_file": script,
                "project": {
                    "seed": 10,
                    "voice": {
                        "mode": "custom_voice",
                        "engine": "test_voice",
                    },
                },
            }
            paths = {"root": root, "state": state_path}
            args = argparse.Namespace(
                basedir=root,
                server="http://127.0.0.1:8188",
                dry_run=False,
                overwrite=False,
                timeout_seconds=1,
            )

            def fake_render_adapter(name, values, *, dry_run=False, **kwargs):
                return {
                    "adapter": name,
                    "cache_key": "workflow-cache-key",
                    "output_path": None if dry_run else str(raw_audio),
                    "prompt_id": None,
                }

            with patch(
                "scripts.video_factory.render_adapter",
                side_effect=fake_render_adapter,
            ):
                record = render_narration(context, state, paths, args)

            self.assertEqual(record["kind"], "narration")
            self.assertEqual(state["assets"]["narration"]["output_path"], str(raw_audio))
            self.assertEqual(narration_path(context, state), raw_audio)
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["assets"]["narration"]["output_path"], str(raw_audio))


class AssemblyTests(unittest.TestCase):
    def _fixture(self, root: Path, delivery: dict | None = None):
        narration = root / "narration.wav"
        narration.write_bytes(b"narration")
        asset = root / "shot.mp4"
        asset.write_bytes(b"shot-v1")
        manifest = {
            "duration": 4.0,
            "delivery": delivery or {"width": 1920, "height": 1080, "fps": 24},
            "shots": [
                {
                    "shot_id": "s0001",
                    "index": 1,
                    "duration": 4.0,
                }
            ],
        }
        state = {
            "assets": {
                "narration": {"output_path": str(narration), "status": "success"},
                "shot:s0001": {"output_path": str(asset), "status": "success"},
            }
        }
        context = {"project": {"voice": {"mode": "custom_voice"}}}
        paths = {
            "clips": root / "assembly" / "clips",
            "delivery": root / "delivery.mp4",
            "manifest": root / "shot-manifest.json",
        }
        return context, manifest, state, paths, asset

    @staticmethod
    def _fake_ffmpeg(command, check):
        output = Path(command[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.touch()

    def test_profile_delivery_settings_control_clip_and_final_encoding(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context, manifest, state, paths, _ = self._fixture(
                root,
                {
                    "width": 1920,
                    "height": 1080,
                    "fps": 24,
                    "video_codec": "libx265",
                    "pixel_format": "yuv444p",
                    "audio_lufs": -20,
                },
            )
            with patch(
                "scripts.video_factory.subprocess.run",
                side_effect=self._fake_ffmpeg,
            ) as run:
                assemble_project(context, manifest, state, paths, overwrite=False)

        clip_command = run.call_args_list[0].args[0]
        final_command = run.call_args_list[-1].args[0]
        self.assertEqual(clip_command[clip_command.index("-c:v") + 1], "libx265")
        self.assertEqual(clip_command[clip_command.index("-pix_fmt") + 1], "yuv444p")
        self.assertIn("format=yuv444p", clip_command[clip_command.index("-vf") + 1])
        self.assertEqual(final_command[final_command.index("-c:v") + 1], "libx265")
        self.assertEqual(final_command[final_command.index("-pix_fmt") + 1], "yuv444p")
        self.assertEqual(
            final_command[final_command.index("-af") + 1],
            "loudnorm=I=-20:TP=-1.5:LRA=11",
        )

    def test_missing_delivery_settings_use_safe_defaults(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context, manifest, state, paths, _ = self._fixture(root)
            with patch(
                "scripts.video_factory.subprocess.run",
                side_effect=self._fake_ffmpeg,
            ) as run:
                assemble_project(context, manifest, state, paths, overwrite=False)

        clip_command = run.call_args_list[0].args[0]
        final_command = run.call_args_list[-1].args[0]
        self.assertEqual(clip_command[clip_command.index("-c:v") + 1], "libx264")
        self.assertEqual(clip_command[clip_command.index("-pix_fmt") + 1], "yuv420p")
        self.assertEqual(final_command[final_command.index("-c:v") + 1], "libx264")
        self.assertEqual(
            final_command[final_command.index("-af") + 1],
            "loudnorm=I=-16:TP=-1.5:LRA=11",
        )

    def test_normalized_clip_cache_tracks_source_duration_and_delivery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context, manifest, state, paths, asset = self._fixture(root)

            def fake_create(asset, target, *args):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"normalized")

            with patch(
                "scripts.video_factory.create_assembly_clip",
                side_effect=fake_create,
            ) as create_clip, patch(
                "scripts.video_factory.subprocess.run",
                side_effect=self._fake_ffmpeg,
            ):
                assemble_project(context, manifest, state, paths, overwrite=False)
                assemble_project(context, manifest, state, paths, overwrite=False)
                self.assertEqual(create_clip.call_count, 1)

                asset.write_bytes(b"shot-v2")
                assemble_project(context, manifest, state, paths, overwrite=False)
                self.assertEqual(create_clip.call_count, 2)

                manifest["shots"][0]["duration"] = 5.0
                assemble_project(context, manifest, state, paths, overwrite=False)
                self.assertEqual(create_clip.call_count, 3)

                manifest["delivery"]["audio_lufs"] = -18
                assemble_project(context, manifest, state, paths, overwrite=False)
                self.assertEqual(create_clip.call_count, 4)


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
