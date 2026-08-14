"""Pinned external LatentSync 1.6 presenter stage for the video factory."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .core import sha256_file, stable_hash, stage_input

ENGINE_NAME = "latentsync16_mouthrestore"
FACTORY_ROOT = Path(__file__).resolve().parent
RUNTIME_ASSETS = FACTORY_ROOT.parent / "spider" / "userscripts_dir" / "latentsync16"
MOUTH_RESTORE_SCRIPT = RUNTIME_ASSETS / "mouth_restore.py"
RUNTIME_CONFIG = RUNTIME_ASSETS / "stage2_512-mask3.yaml"
RUNTIME_PATCH = RUNTIME_ASSETS / "runtime.patch"
CONTAINER_RUNTIME_ROOT = Path("/comfy/mnt/latentsync-1.6")
CONTAINER_SOURCE = CONTAINER_RUNTIME_ROOT / "source"
CONTAINER_MODELS = CONTAINER_RUNTIME_ROOT / "models"
CONTAINER_CONFIG = CONTAINER_RUNTIME_ROOT / "stage2_512-mask3.yaml"
CONTAINER_PYTHON = "/comfy/mnt/venv/bin/python3"
CONTAINER_PYTHONPATH = ":".join(
    (
        "/comfy/mnt/latentsync-1.6/pydeps",
        "/basedir/custom_nodes",
        "/comfy/mnt/ComfyUI",
        "/comfy/mnt/latentsync-1.6/source",
    )
)

RUNTIME_CONTRACT = {
    "version": 1,
    "container_torch": "2.11.0+cu128",
    "container_cuda": "12.8",
    "source_repository": "https://github.com/bytedance/LatentSync.git",
    "source_commit": "a229c3948406bc2cf6eaf4873e662e70c6a04746",
    "model_repository": "ByteDance/LatentSync-1.6",
    "model_revision": "c42c7e6c8e9c213626389fa7d9a3c444b8536353",
    "model_license": "openrail++",
    "checkpoint_sha256": "0a478e89eb660f82da4c35dbdde8a5adfb27f99d1b4e50edd03729e1e98316d3",
    "whisper_sha256": "65147644a518d12f04e32d6f3b26facc3f8dd46e5390956a9424a650c0ce22b9",
    "vae_revision": "31f26fdeee1355a5c34592e401dd41e45d25a493",
    "vae_sha256": "a1d993488569e928462932c8c38a0760b874d166399b14414135bd9c42df5815",
    "patched_files": {
        "latentsync/utils/face_detector.py": "6c4afe5715ab75b7992197bba9eeea04ce177286d349e92952d5a26c76d1f8dc",
        "latentsync/utils/image_processor.py": "a755a32c3e4dab84609dd5dd3beafa77a8c2016ec439a012ce8391dabd05b0a9",
        "scripts/inference.py": "c5ed1903951fb9bd7c0a3c6e278e9e0f1081015567e03eb651f23b888425f728",
    },
    "config_sha256": "1b4d2dd6ae6b592d70775898ffd2f3c303a037cfeacbe60c6b0c8d8bbfe102ef",
    "detector": "ComfyUI-LivePortraitKJ Face Alignment 68-point + BlazeFace",
    "comfyui_source": "/comfy/mnt/ComfyUI",
}
MINIMUM_FREE_GPU_MEMORY_MIB = 18000

DEFAULT_OPTIONS: dict[str, Any] = {
    "mask": "mask3",
    "inference_steps": 20,
    "guidance_scale": 1.5,
    "enable_deepcache": False,
    "pad_x": 11.0,
    "pad_y": 12.0,
    "minimum_radius_x": 37.0,
    "minimum_radius_y": 22.0,
    "feather_sigma": 4.0,
}


def normalize_options(options: dict[str, Any] | None = None) -> dict[str, Any]:
    resolved = {**DEFAULT_OPTIONS, **(options or {})}
    if resolved["mask"] != "mask3":
        raise ValueError("The approved LatentSync presenter path requires mask3")
    resolved["inference_steps"] = int(resolved["inference_steps"])
    resolved["guidance_scale"] = float(resolved["guidance_scale"])
    resolved["enable_deepcache"] = bool(resolved["enable_deepcache"])
    for key in (
        "pad_x",
        "pad_y",
        "minimum_radius_x",
        "minimum_radius_y",
        "feather_sigma",
    ):
        resolved[key] = float(resolved[key])
    if resolved["inference_steps"] < 1:
        raise ValueError("LatentSync inference_steps must be positive")
    if resolved["guidance_scale"] <= 0:
        raise ValueError("LatentSync guidance_scale must be positive")
    return resolved


def runtime_fingerprint() -> dict[str, Any]:
    return {
        **RUNTIME_CONTRACT,
        "runtime_patch_sha256": sha256_file(RUNTIME_PATCH),
        "mouth_restore_sha256": sha256_file(MOUTH_RESTORE_SCRIPT),
        "tracked_config_sha256": sha256_file(RUNTIME_CONFIG),
    }


def build_cache_key(
    source_video: Path,
    audio: Path,
    *,
    seed: int,
    frame_count: int,
    fps: int,
    options: dict[str, Any] | None = None,
) -> str:
    return stable_hash(
        {
            "adapter": ENGINE_NAME,
            "source_video_sha256": sha256_file(source_video),
            "audio_sha256": sha256_file(audio),
            "seed": int(seed),
            "frame_count": int(frame_count),
            "fps": int(fps),
            "options": normalize_options(options),
            "runtime": runtime_fingerprint(),
        }
    )


def _container_path(path: Path, basedir: Path) -> str:
    try:
        relative = path.resolve().relative_to(basedir.resolve())
    except ValueError as exc:
        raise ValueError(f"LatentSync input must be under the selected basedir: {path}") from exc
    return (Path("/basedir") / relative).as_posix()


def _docker_prefix(container: str, user: str) -> list[str]:
    return ["docker", "exec", "--user", user]


def validate_runtime(
    container: str = "spider",
    user: str = "1000:984",
    timeout_seconds: float = 180,
) -> dict[str, Any]:
    """Verify the immutable runtime contract before a production render."""
    check_script = r'''
import hashlib, json, subprocess
from pathlib import Path
import torch
expected = json.loads(__import__("sys").argv[1])
root = Path("/comfy/mnt/latentsync-1.6")
source = root / "source"
def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()
def git(*args):
    return subprocess.run(["git", "-C", str(source), *args], check=True, capture_output=True, text=True).stdout.strip()
actual = {
    "torch": torch.__version__,
    "cuda": torch.version.cuda,
    "source_commit": git("rev-parse", "HEAD"),
    "checkpoint_sha256": digest(root / "models" / "latentsync_unet.pt"),
    "whisper_sha256": digest(root / "models" / "whisper" / "tiny.pt"),
    "vae_sha256": digest(root / "models" / "vae" / "diffusion_pytorch_model.safetensors"),
    "config_sha256": digest(root / "stage2_512-mask3.yaml"),
    "patched_files": {name: digest(source / name) for name in expected["patched_files"]},
}
failures = []
checks = {
    "torch": expected["container_torch"],
    "cuda": expected["container_cuda"],
    "source_commit": expected["source_commit"],
    "checkpoint_sha256": expected["checkpoint_sha256"],
    "whisper_sha256": expected["whisper_sha256"],
    "vae_sha256": expected["vae_sha256"],
    "config_sha256": expected["config_sha256"],
}
for key, wanted in checks.items():
    if actual[key] != wanted:
        failures.append(f"{key}: expected {wanted}, found {actual[key]}")
for name, wanted in expected["patched_files"].items():
    if actual["patched_files"][name] != wanted:
        failures.append(f"{name}: expected {wanted}, found {actual['patched_files'][name]}")
if failures:
    raise SystemExit("LatentSync runtime contract failed:\n- " + "\n- ".join(failures))
print(json.dumps(actual, sort_keys=True))
'''
    command = [
        *_docker_prefix(container, user),
        container,
        CONTAINER_PYTHON,
        "-c",
        check_script,
        json.dumps(RUNTIME_CONTRACT, sort_keys=True),
    ]
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def _free_gpu_memory_mib(container: str, user: str) -> int:
    result = subprocess.run(
        [
            *_docker_prefix(container, user),
            container,
            CONTAINER_PYTHON,
            "-c",
            "import torch; print(torch.cuda.mem_get_info()[0] // (1024 * 1024))",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return int(result.stdout.strip().splitlines()[-1])


def _run_logged(command: list[str], log_path: Path, timeout_seconds: float) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        subprocess.run(
            command,
            check=True,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_seconds,
        )


def _probe_video(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height,avg_frame_rate,nb_frames",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _validate_output(path: Path, frame_count: int, fps: int) -> dict[str, Any]:
    probe = _probe_video(path)
    video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
    audio = [stream for stream in probe["streams"] if stream["codec_type"] == "audio"]
    if int(video.get("nb_frames", 0)) != frame_count:
        raise RuntimeError(
            f"LatentSync output frame count mismatch: {video.get('nb_frames')} != {frame_count}"
        )
    if video.get("avg_frame_rate") != f"{fps}/1":
        raise RuntimeError(
            f"LatentSync output FPS mismatch: {video.get('avg_frame_rate')} != {fps}/1"
        )
    expected_duration = frame_count / fps
    if not math.isclose(float(probe["format"]["duration"]), expected_duration, abs_tol=0.001):
        raise RuntimeError("LatentSync output duration does not match frame contract")
    if not audio:
        raise RuntimeError("LatentSync output is missing its exact shot audio")
    return probe


def record_for_existing_output(
    output: Path,
    source_video: Path,
    audio: Path,
    *,
    seed: int,
    frame_count: int,
    fps: int,
    options: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    probe = _validate_output(output, frame_count, fps)
    return {
        "status": "success",
        "adapter": ENGINE_NAME,
        "cache_key": build_cache_key(
            source_video,
            audio,
            seed=seed,
            frame_count=frame_count,
            fps=fps,
            options=options,
        ),
        "output_path": str(output.resolve()),
        "prompt_id": None,
        "source_video_sha256": sha256_file(source_video),
        "audio_sha256": sha256_file(audio),
        "output_sha256": sha256_file(output),
        "parameters": {
            "seed": int(seed),
            "frame_count": int(frame_count),
            "fps": int(fps),
            **normalize_options(options),
        },
        "runtime": runtime_fingerprint(),
        "probe": probe,
        **(provenance or {}),
    }


def render(
    source_video: Path,
    audio: Path,
    output: Path,
    *,
    basedir: Path,
    seed: int,
    frame_count: int,
    fps: int,
    options: dict[str, Any] | None = None,
    container: str = "spider",
    user: str = "1000:984",
    timeout_seconds: float = 1800,
) -> dict[str, Any]:
    """Run exact LatentSync mask3 inference and the approved mouth restore."""
    resolved = normalize_options(options)
    free_memory = _free_gpu_memory_mib(container, user)
    minimum_memory = MINIMUM_FREE_GPU_MEMORY_MIB
    if free_memory < minimum_memory:
        raise RuntimeError(
            "LatentSync needs at least "
            f"{minimum_memory} MiB free GPU memory; only {free_memory} MiB is free. "
            "Unload other local GPU models and retry."
        )
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    runtime_dir = output.parent / ".latentsync16" / output.stem
    runtime_dir.mkdir(parents=True, exist_ok=True)
    raw = runtime_dir / "raw-mask3.mp4"
    diagnostics = runtime_dir / "mask-diagnostics.json"
    inference_log = runtime_dir / "inference.log"
    composite_log = runtime_dir / "mouth-restore.log"
    temp_dir = runtime_dir / "temp"
    work_dir = runtime_dir / "work"
    for disposable in (temp_dir, work_dir):
        if disposable.exists():
            shutil.rmtree(disposable)
        disposable.mkdir(parents=True)

    staged_script_name = stage_input(
        MOUTH_RESTORE_SCRIPT,
        basedir,
        "video_factory/runtime/latentsync16/mouth_restore.py",
    )
    staged_script = (Path("/basedir/input") / staged_script_name).as_posix()
    source_name = _container_path(source_video, basedir)
    audio_name = _container_path(audio, basedir)
    raw_name = _container_path(raw, basedir)
    output_name = _container_path(output, basedir)
    diagnostics_name = _container_path(diagnostics, basedir)
    temp_name = _container_path(temp_dir, basedir)
    work_name = _container_path(work_dir, basedir)

    inference = [
        *_docker_prefix(container, user),
        "--workdir",
        work_name,
        "-e",
        f"PYTHONPATH={CONTAINER_PYTHONPATH}",
        "-e",
        f"TORCH_HOME={CONTAINER_RUNTIME_ROOT / 'torch-cache'}",
        container,
        CONTAINER_PYTHON,
        "-m",
        "scripts.inference",
        "--unet_config_path",
        str(CONTAINER_CONFIG),
        "--inference_ckpt_path",
        str(CONTAINER_MODELS / "latentsync_unet.pt"),
        "--inference_steps",
        str(resolved["inference_steps"]),
        "--guidance_scale",
        str(resolved["guidance_scale"]),
        "--video_path",
        source_name,
        "--audio_path",
        audio_name,
        "--video_out_path",
        raw_name,
        "--temp_dir",
        temp_name,
        "--seed",
        str(int(seed)),
    ]
    if resolved["enable_deepcache"]:
        inference.append("--enable_deepcache")
    _run_logged(inference, inference_log, timeout_seconds)

    composite = [
        *_docker_prefix(container, user),
        "-e",
        f"PYTHONPATH={CONTAINER_PYTHONPATH}",
        container,
        CONTAINER_PYTHON,
        staged_script,
        "--source",
        source_name,
        "--generated",
        raw_name,
        "--audio",
        audio_name,
        "--output",
        output_name,
        "--diagnostics",
        diagnostics_name,
        "--fps",
        str(int(fps)),
        "--frames",
        str(int(frame_count)),
        "--pad-x",
        str(resolved["pad_x"]),
        "--pad-y",
        str(resolved["pad_y"]),
        "--minimum-radius-x",
        str(resolved["minimum_radius_x"]),
        "--minimum-radius-y",
        str(resolved["minimum_radius_y"]),
        "--feather-sigma",
        str(resolved["feather_sigma"]),
    ]
    _run_logged(composite, composite_log, timeout_seconds)
    record = record_for_existing_output(
        output,
        source_video,
        audio,
        seed=seed,
        frame_count=frame_count,
        fps=fps,
        options=resolved,
        provenance={
            "raw_output_path": str(raw),
            "diagnostics_path": str(diagnostics),
            "inference_log": str(inference_log),
            "composite_log": str(composite_log),
            "runtime_container": container,
            "free_gpu_memory_before_inference_mib": free_memory,
        },
    )
    diagnostics_report = json.loads(diagnostics.read_text(encoding="utf-8"))
    if diagnostics_report["detection_failure_frames"]:
        raise RuntimeError("Mouth restore had face-detection failures")
    if diagnostics_report["source_preservation"]["maximum_absolute_channel_difference"] != 0:
        raise RuntimeError("Mouth restore changed source pixels outside its pre-encoding mask")
    record["mask_summary"] = diagnostics_report["mask"]
    return record


def default_container() -> str:
    return os.environ.get("VIDEO_FACTORY_RUNTIME_CONTAINER", "spider")


def default_container_user() -> str:
    return os.environ.get("VIDEO_FACTORY_RUNTIME_USER", "1000:984")
