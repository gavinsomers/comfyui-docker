import argparse
import json
import os
import subprocess
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.video_factory import (
    _atempo_filter,
    assemble_project,
    build_parser,
    build_presenter_qa_report,
    conform_narration_speed,
    narration_path,
    presenter_qa_failures,
    render_narration,
)
from scripts.presenter_benchmark import ENGINE_SETTINGS, preflight_longcat_nodes
from spider.userscripts_dir.presenter_benchmark_deps import (
    CORE_NODE_CLASSES,
    CUSTOM_NODE_PROVIDERS,
    DEPENDENCY_CONTRACT,
    IMPORTS,
    REQUIREMENTS,
    custom_node_provider_failures,
    format_custom_node_provider_failures,
    format_missing_custom_nodes,
    missing_custom_node_sources,
    missing_registered_nodes,
    requirements_satisfied,
)
from video_factory.core import (
    FACTORY_ROOT,
    build_adapter_prompt,
    compile_shot_manifest,
    load_project,
    sha256_file,
    split_script_by_target_words,
    stable_hash,
    write_srt,
)
from video_factory.qa import (
    DEFAULT_MOUTH_ROI,
    MANUAL_CRITERIA,
    build_presenter_qa_policy,
    evaluate_presenter_qa,
    presenter_qa_policy_fingerprint,
    summarize_motion,
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
        self.assertEqual(presenter["engine"], "ltx25_presenter")
        self.assertIn("same fictional rural homesteader", presenter["prompt"].lower())

    def test_compiled_manifest_preserves_validated_presenter_seed_overrides(self):
        shots = {
            shot["shot_id"]: shot
            for shot in compile_shot_manifest(load_project(EXAMPLE_PROJECT))["shots"]
        }

        self.assertEqual(shots["s0011"]["seed"], 509111)
        self.assertEqual(shots["s0016"]["seed"], 509116)

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

    def test_ltx25_presenter_adapter_is_local_and_patches_exact_av_inputs(self):
        _, prompt = build_adapter_prompt(
            "ltx25_presenter",
            {
                "prompt": "The same test prompt for every local engine.",
                "seed": 99,
                "duration": 4.017,
                "width": 1024,
                "height": 576,
                "image": "video_factory/test/master.png",
                "audio": "video_factory/test/s0003.wav",
                "output_prefix": "video_factory/test/ltx25-presenter",
            },
        )

        self.assertEqual(prompt["269"]["inputs"]["image"], "video_factory/test/master.png")
        self.assertEqual(prompt["346"]["inputs"]["audio"], "video_factory/test/s0003.wav")
        self.assertEqual(prompt["405:362"]["inputs"]["value"], 4.017)
        self.assertEqual(prompt["405:372"]["inputs"]["value"], 1024)
        self.assertIn("LTXVAudioVAEEncode", {node["class_type"] for node in prompt.values()})
        self.assertNotIn("LtxApi25AudioToVideo", {node["class_type"] for node in prompt.values()})

    def test_longcat_benchmark_adapter_uses_local_model_nodes(self):
        _, prompt = build_adapter_prompt(
            "longcat_avatar_presenter",
            {
                "prompt": "The same test prompt for every local engine.",
                "seed": 99,
                "duration": 4.017,
                "width": 768,
                "height": 432,
                "image": "video_factory/test/master.png",
                "audio": "video_factory/test/s0003.wav",
                "output_prefix": "video_factory/test/longcat",
            },
        )

        classes = {node["class_type"] for node in prompt.values()}
        self.assertIn("WanVideoLongCatAvatarExtendEmbeds", classes)
        self.assertIn("MultiTalkWav2VecEmbeds", classes)
        self.assertEqual(prompt["134"]["inputs"]["blocks_to_swap"], 35)
        self.assertEqual(prompt["453"]["inputs"]["filename_prefix"], "video_factory/test/longcat")

    def test_presenter_benchmark_engines_use_exactly_matching_aspect_ratios(self):
        for engine, dimensions in ENGINE_SETTINGS.items():
            with self.subTest(engine=engine):
                self.assertEqual(dimensions["width"] * 9, dimensions["height"] * 16)

    def test_stable_hash_ignores_dictionary_insertion_order(self):
        self.assertEqual(stable_hash({"a": 1, "b": 2}), stable_hash({"b": 2, "a": 1}))


class PresenterQATests(unittest.TestCase):
    @staticmethod
    def _analysis(mean: float, p95: float) -> dict:
        return {
            "video_sha256": "abc123",
            "sample_fps": 12,
            "mouth_roi": DEFAULT_MOUTH_ROI,
            "motion": {
                "mean_absolute_luma_delta": mean,
                "p95_absolute_luma_delta": p95,
            },
            "metric_limitations": "Human review is still required.",
        }

    @staticmethod
    def _manual_review(**overrides: str) -> dict[str, str]:
        review = {criterion: "pass" for criterion in MANUAL_CRITERIA}
        review.update(overrides)
        return review

    def test_motion_summary_uses_mean_and_true_p95(self):
        summary = summarize_motion([1, 2, 3, 4, 10])

        self.assertEqual(summary["frame_pairs"], 5)
        self.assertEqual(summary["mean_absolute_luma_delta"], 4)
        self.assertEqual(summary["p95_absolute_luma_delta"], 10)

    def test_visible_motion_screen_rejects_nearly_frozen_mouth(self):
        qa = evaluate_presenter_qa(
            self._analysis(4.8, 7.9),
            minimum_mean=5.0,
            minimum_p95=9.0,
            manual_review=self._manual_review(),
        )

        self.assertEqual(qa["automatic_visible_motion_screen"]["status"], "fail")
        self.assertEqual(qa["status"], "fail")

    def test_automatic_motion_cannot_replace_manual_articulation_review(self):
        pending = evaluate_presenter_qa(
            self._analysis(8.5, 14.5),
            minimum_mean=5.0,
            minimum_p95=9.0,
        )
        passed = evaluate_presenter_qa(
            self._analysis(8.5, 14.5),
            minimum_mean=5.0,
            minimum_p95=9.0,
            manual_review=self._manual_review(),
        )

        self.assertEqual(pending["status"], "pending")
        self.assertEqual(passed["status"], "pass")

    def test_manual_failure_overrides_passing_motion_screen(self):
        qa = evaluate_presenter_qa(
            self._analysis(8.5, 14.5),
            minimum_mean=5.0,
            minimum_p95=9.0,
            manual_review=self._manual_review(visible_articulation="fail"),
        )

        self.assertEqual(qa["automatic_visible_motion_screen"]["status"], "pass")
        self.assertEqual(qa["status"], "fail")

    def test_text_artifact_failure_overrides_passing_presenter_checks(self):
        qa = evaluate_presenter_qa(
            self._analysis(8.5, 14.5),
            minimum_mean=5.0,
            minimum_p95=9.0,
            manual_review=self._manual_review(text_artifact_free="fail"),
        )

        self.assertEqual(qa["automatic_visible_motion_screen"]["status"], "pass")
        self.assertEqual(qa["manual_review"]["status"], "fail")
        self.assertEqual(qa["status"], "fail")

    def test_cli_accepts_text_artifact_free_presenter_review(self):
        args = build_parser().parse_args(
            [
                "qa-presenter",
                "project.yaml",
                "--text-artifact-free",
                "pass",
            ]
        )

        self.assertEqual(args.text_artifact_free, "pass")

    def test_presenter_qa_report_includes_all_state_records(self):
        manifest = {
            "shots": [
                {"shot_id": "s0001", "type": "presenter"},
                {"shot_id": "s0002", "type": "still"},
                {"shot_id": "s0003", "type": "presenter"},
            ]
        }
        state = {
            "assets": {
                "shot:s0001": {"qa": {"status": "pass"}},
                "shot:s0003": {"qa": {"status": "pending"}},
            }
        }

        report = build_presenter_qa_report(
            {"slug": "test-project"}, manifest, state
        )

        self.assertEqual(set(report["shots"]), {"s0001", "s0003"})
        self.assertEqual(report["shots"]["s0001"]["status"], "pass")
        self.assertEqual(report["shots"]["s0003"]["status"], "pending")

    def test_assembly_gate_rejects_missing_pending_or_stale_presenter_qa(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "shot.mp4"
            video.write_bytes(b"presenter")
            context = {
                "project": {
                    "presenter": {"qa": {"require_pass_before_assembly": False}}
                }
            }
            manifest = {"shots": [{"shot_id": "s0001", "type": "presenter"}]}
            state = {"assets": {"shot:s0001": {"status": "success", "output_path": str(video)}}}

            self.assertEqual(
                presenter_qa_failures(context, manifest, state),
                ["s0001 (QA missing)"],
            )
            state["assets"]["shot:s0001"]["qa"] = {
                "status": "pending",
                "source_sha256": "stale",
            }
            self.assertEqual(
                presenter_qa_failures(context, manifest, state),
                ["s0001 (QA stale)"],
            )
            state["assets"]["shot:s0001"]["qa"] = {
                "status": "pass",
                "source_sha256": stable_hash("not-the-file-hash"),
            }
            self.assertEqual(
                presenter_qa_failures(context, manifest, state),
                ["s0001 (QA stale)"],
            )
            state["assets"]["shot:s0001"]["qa"]["source_sha256"] = sha256_file(video)
            self.assertEqual(
                presenter_qa_failures(context, manifest, state),
                ["s0001 (QA stale)"],
            )
            state["assets"]["shot:s0001"]["qa"]["policy_fingerprint"] = (
                presenter_qa_policy_fingerprint(build_presenter_qa_policy())
            )
            self.assertEqual(
                presenter_qa_failures(context, manifest, state),
                ["s0001 (QA incomplete)"],
            )
            state["assets"]["shot:s0001"]["qa"].update(
                {
                    "automatic_visible_motion_screen": {"status": "pass"},
                    "manual_review": {
                        "status": "pass",
                        "criteria": self._manual_review(),
                    },
                }
            )
            self.assertEqual(presenter_qa_failures(context, manifest, state), [])

            context["project"]["presenter"]["qa"]["sample_fps"] = 13
            self.assertEqual(
                presenter_qa_failures(context, manifest, state),
                ["s0001 (QA stale)"],
            )

    def test_assembly_gate_does_not_affect_projects_without_presenter_shots(self):
        manifest = {"shots": [{"shot_id": "s0001", "type": "still"}]}

        self.assertEqual(presenter_qa_failures({"project": {}}, manifest, {}), [])

    def test_presenter_qa_fingerprint_covers_roi_thresholds_and_algorithm(self):
        baseline = presenter_qa_policy_fingerprint(build_presenter_qa_policy())
        changed_roi = dict(DEFAULT_MOUTH_ROI, width=0.2)

        self.assertNotEqual(
            baseline,
            presenter_qa_policy_fingerprint(
                build_presenter_qa_policy(roi=changed_roi)
            ),
        )
        self.assertNotEqual(
            baseline,
            presenter_qa_policy_fingerprint(
                build_presenter_qa_policy(minimum_mean=6.0)
            ),
        )
        with patch("video_factory.qa.PRESENTER_QA_ALGORITHM_VERSION", 2):
            self.assertNotEqual(
                baseline,
                presenter_qa_policy_fingerprint(build_presenter_qa_policy()),
            )


class PresenterBenchmarkDependencyTests(unittest.TestCase):
    def test_dependency_contract_covers_pinned_provider_manifests(self):
        expected = {
            "ComfyUI-WanVideoWrapper": {
                "accelerate",
                "diffusers",
                "einops",
                "ftfy",
                "gguf",
                "opencv",
                "peft",
                "protobuf",
                "pyloudnorm",
                "scipy",
                "sentencepiece",
            },
            "ComfyUI-KJNodes": {
                "color-matcher",
                "huggingface_hub",
                "matplotlib",
                "mss",
                "numpy",
                "opencv",
                "pillow",
                "scipy",
            },
            "ComfyUI-MelBandRoFormer": {"einops", "rotary_embedding_torch"},
            "ComfyUI-VideoHelperSuite": {"imageio-ffmpeg", "opencv"},
        }

        for provider_name, dependencies in expected.items():
            self.assertEqual(
                set(CUSTOM_NODE_PROVIDERS[provider_name]["dependencies"]),
                dependencies,
            )
        self.assertEqual(
            set(DEPENDENCY_CONTRACT),
            {"packaging"}.union(*expected.values()),
        )

    def test_install_and_import_checks_derive_from_dependency_contract(self):
        self.assertEqual(
            set(REQUIREMENTS),
            {requirement for requirement, _module in DEPENDENCY_CONTRACT.values()},
        )
        self.assertEqual(
            set(IMPORTS),
            {module for _requirement, module in DEPENDENCY_CONTRACT.values()},
        )
        for requirement in REQUIREMENTS:
            self.assertRegex(requirement, r"[<=>]")

    def test_dependency_preflight_rejects_missing_or_outdated_distributions(self):
        self.assertTrue(requirements_satisfied(("pip>=0",), ("json",)))
        self.assertFalse(requirements_satisfied(("pip>=9999",), ("json",)))
        self.assertFalse(
            requirements_satisfied(("definitely-not-installed>=1",), ("json",))
        )

    def test_longcat_provider_manifest_covers_every_non_core_workflow_class(self):
        template = json.loads(
            (
                FACTORY_ROOT
                / "api_templates"
                / "longcat_avatar_presenter.json"
            ).read_text(encoding="utf-8")
        )
        workflow_classes = {node["class_type"] for node in template.values()}
        mapped_classes = {
            node_class
            for provider in CUSTOM_NODE_PROVIDERS.values()
            for node_class in provider["classes"]
        }

        self.assertEqual(workflow_classes - set(CORE_NODE_CLASSES), mapped_classes)
        for provider in CUSTOM_NODE_PROVIDERS.values():
            self.assertRegex(provider["commit"], r"^[0-9a-f]{40}$")
            self.assertNotIn("revision", provider)

    def test_custom_node_source_preflight_reports_absent_providers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing = custom_node_provider_failures(root)

        self.assertEqual(set(missing), set(CUSTOM_NODE_PROVIDERS))
        guidance = format_custom_node_provider_failures(missing, root)
        self.assertIn("ComfyUI-WanVideoWrapper", guidance)
        self.assertIn("e091c4a77425d6a4a7f90ab30c513d24f8cb91cf", guidance)
        self.assertIn(str(root / "ComfyUI-WanVideoWrapper"), guidance)
        self.assertIn("checkout --detach", guidance)

    def test_startup_preflight_fails_before_pip_when_providers_are_absent(self):
        script = (
            FACTORY_ROOT.parent
            / "spider"
            / "userscripts_dir"
            / "08-install-presenter-benchmark-deps.sh"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            activation = root / "activate"
            activation.write_text("", encoding="utf-8")
            result = subprocess.run(
                ["bash", str(script)],
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "PRESENTER_BENCHMARK_VENV_ACTIVATE": str(activation),
                    "PRESENTER_BENCHMARK_CUSTOM_NODES_DIR": str(
                        root / "custom_nodes"
                    ),
                },
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("LongCat custom-node preflight failed", result.stderr)
        self.assertNotIn("Installing presenter benchmark dependencies", result.stdout)

    def test_custom_node_source_preflight_accepts_complete_providers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for provider_name, provider in CUSTOM_NODE_PROVIDERS.items():
                provider_dir = root / provider["directory"]
                provider_dir.mkdir()
                (provider_dir / "nodes.py").write_text(
                    "\n".join(provider["classes"]), encoding="utf-8"
                )

            def git_output(provider_dir, *args):
                provider = next(
                    item
                    for item in CUSTOM_NODE_PROVIDERS.values()
                    if item["directory"] == provider_dir.name
                )
                if args == ("config", "--get", "remote.origin.url"):
                    return True, provider["repository"].removesuffix(".git")
                if args == ("rev-parse", "HEAD"):
                    return True, provider["commit"]
                return True, ""

            with patch(
                "spider.userscripts_dir.presenter_benchmark_deps._git_output",
                side_effect=git_output,
            ):
                self.assertEqual(custom_node_provider_failures(root), {})

    def test_custom_node_preflight_rejects_wrong_git_identity_and_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for provider in CUSTOM_NODE_PROVIDERS.values():
                provider_dir = root / provider["directory"]
                provider_dir.mkdir()
                (provider_dir / "nodes.py").write_text(
                    "\n".join(provider["classes"]), encoding="utf-8"
                )

            outputs = {
                "config": (True, "https://github.com/example/wrong-provider.git"),
                "rev-parse": (True, "0" * 40),
                "status": (True, "?? local-node.py"),
            }
            with patch(
                "spider.userscripts_dir.presenter_benchmark_deps._git_output",
                side_effect=lambda _provider_dir, *args: outputs[args[0]],
            ):
                failures = custom_node_provider_failures(root)

        for provider_failures in failures.values():
            self.assertIn(
                "origin is https://github.com/example/wrong-provider.git",
                provider_failures,
            )
            self.assertIn("HEAD is " + "0" * 40, provider_failures)
            self.assertIn(
                "checkout has local or untracked changes", provider_failures
            )

    def test_registered_node_preflight_groups_missing_classes_by_provider(self):
        available = set(CORE_NODE_CLASSES)
        for provider in CUSTOM_NODE_PROVIDERS.values():
            available.update(provider["classes"])
        available.remove("VHS_VideoCombine")

        self.assertEqual(
            missing_registered_nodes(available),
            {"ComfyUI-VideoHelperSuite": ("VHS_VideoCombine",)},
        )

    def test_benchmark_preflight_fails_before_rendering_missing_server_nodes(self):
        available = {
            node_class: {}
            for provider in CUSTOM_NODE_PROVIDERS.values()
            for node_class in provider["classes"]
            if node_class != "WanVideoLongCatAvatarExtendEmbeds"
        }
        with patch("scripts.presenter_benchmark.request_json", return_value=available):
            with self.assertRaisesRegex(
                RuntimeError,
                "ComfyUI-WanVideoWrapper.*WanVideoLongCatAvatarExtendEmbeds",
            ):
                preflight_longcat_nodes("http://127.0.0.1:8188")


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

    def test_failed_overwrite_preserves_resumable_clip_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context, manifest, state, paths, _ = self._fixture(root)
            clip = paths["clips"] / "0001-s0001.mp4"
            temporary_clip = paths["clips"] / ".0001-s0001.tmp.mp4"

            def create_valid_clip(asset, target, *args):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"valid normalized clip")

            def fail_after_partial_write(asset, target, *args):
                target.write_bytes(b"partial clip")
                raise RuntimeError("encoding interrupted")

            with patch(
                "scripts.video_factory.create_assembly_clip",
                side_effect=create_valid_clip,
            ) as create_clip, patch(
                "scripts.video_factory.subprocess.run",
                side_effect=self._fake_ffmpeg,
            ):
                assemble_project(context, manifest, state, paths, overwrite=False)
                cache_record = clip.with_suffix(".cache.json").read_bytes()

                create_clip.side_effect = fail_after_partial_write
                with self.assertRaisesRegex(RuntimeError, "encoding interrupted"):
                    assemble_project(context, manifest, state, paths, overwrite=True)

                self.assertEqual(clip.read_bytes(), b"valid normalized clip")
                self.assertEqual(
                    clip.with_suffix(".cache.json").read_bytes(),
                    cache_record,
                )
                self.assertFalse(temporary_clip.exists())

                create_clip.side_effect = create_valid_clip
                create_clip.reset_mock()
                assemble_project(context, manifest, state, paths, overwrite=False)
                create_clip.assert_not_called()


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
