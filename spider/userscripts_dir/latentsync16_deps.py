#!/usr/bin/env python3
"""Install and verify the pinned LatentSync 1.6 CUDA 12.8 runtime."""

from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.metadata
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

SOURCE_REPOSITORY = "https://github.com/bytedance/LatentSync.git"
SOURCE_COMMIT = "a229c3948406bc2cf6eaf4873e662e70c6a04746"
MODEL_REPOSITORY = "ByteDance/LatentSync-1.6"
MODEL_REVISION = "c42c7e6c8e9c213626389fa7d9a3c444b8536353"
VAE_REPOSITORY = "stabilityai/sd-vae-ft-mse"
VAE_REVISION = "31f26fdeee1355a5c34592e401dd41e45d25a493"
SCRIPT_DIR = Path(__file__).resolve().parent
ASSETS_DIR = SCRIPT_DIR / "latentsync16"
PATCH = ASSETS_DIR / "runtime.patch"
CONFIG = ASSETS_DIR / "stage2_512-mask3.yaml"

TARGET_REQUIREMENTS = (
    "antlr4-python3-runtime==4.9.3",
    "decord==0.6.0",
    "ffmpeg-python==0.2.0",
    "future==1.0.0",
    "omegaconf==2.3.0",
    "python_speech_features==0.6",
)
TARGET_IMPORTS = (
    "antlr4",
    "decord",
    "ffmpeg",
    "future",
    "omegaconf",
    "python_speech_features",
)
EXPECTED_FILES = {
    "models/latentsync_unet.pt": "0a478e89eb660f82da4c35dbdde8a5adfb27f99d1b4e50edd03729e1e98316d3",
    "models/whisper/tiny.pt": "65147644a518d12f04e32d6f3b26facc3f8dd46e5390956a9424a650c0ce22b9",
    "models/vae/config.json": "92d3dfb746fca211a2c9e019e285f8597412211728dce3c5bcf4eda0f2d62e7e",
    "models/vae/diffusion_pytorch_model.safetensors": "a1d993488569e928462932c8c38a0760b874d166399b14414135bd9c42df5815",
    "source/latentsync/utils/face_detector.py": "6c4afe5715ab75b7992197bba9eeea04ce177286d349e92952d5a26c76d1f8dc",
    "source/latentsync/utils/image_processor.py": "a755a32c3e4dab84609dd5dd3beafa77a8c2016ec439a012ce8391dabd05b0a9",
    "source/scripts/inference.py": "c5ed1903951fb9bd7c0a3c6e278e9e0f1081015567e03eb651f23b888425f728",
    "stage2_512-mask3.yaml": "1b4d2dd6ae6b592d70775898ffd2f3c303a037cfeacbe60c6b0c8d8bbfe102ef",
}
EXPECTED_DIRTY_PATHS = {
    "latentsync/utils/face_detector.py",
    "latentsync/utils/image_processor.py",
    "scripts/inference.py",
    "checkpoints",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git(source: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(source), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def _repository_identity(value: str) -> str:
    normalized = value.strip().rstrip("/")
    for prefix in ("https://github.com/", "http://github.com/", "git@github.com:"):
        if normalized.casefold().startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    return normalized.removesuffix(".git").casefold()


def target_requirements_satisfied(target: Path) -> bool:
    if not target.is_dir():
        return False
    expected_versions = {
        re.sub(r"[-_.]+", "-", name).casefold(): version
        for name, version in (requirement.split("==", 1) for requirement in TARGET_REQUIREMENTS)
    }
    installed_versions: dict[str, set[str]] = {}
    for distribution in importlib.metadata.distributions(path=[str(target)]):
        name = distribution.metadata.get("Name")
        if not name:
            continue
        normalized = re.sub(r"[-_.]+", "-", name).casefold()
        installed_versions.setdefault(normalized, set()).add(distribution.version)
    if set(installed_versions) != set(expected_versions):
        return False
    if any(
        installed_versions.get(name) != {version}
        for name, version in expected_versions.items()
    ):
        return False

    resolved_target = target.resolve()
    for module in TARGET_IMPORTS:
        spec = importlib.machinery.PathFinder.find_spec(module, [str(target)])
        if spec is None or not spec.origin:
            return False
        if not Path(spec.origin).resolve().is_relative_to(resolved_target):
            return False
    return True


def runtime_failures(runtime: Path, verify_large_files: bool = True) -> list[str]:
    failures: list[str] = []
    source = runtime / "source"
    if not source.is_dir():
        return [f"source checkout is missing: {source}"]
    try:
        remote = _git(source, "config", "--get", "remote.origin.url").stdout.strip()
        commit = _git(source, "rev-parse", "HEAD").stdout.strip()
        status = _git(
            source, "status", "--porcelain", "--untracked-files=all"
        ).stdout.splitlines()
    except subprocess.CalledProcessError:
        return ["source is not a readable Git checkout"]
    if _repository_identity(remote) != _repository_identity(SOURCE_REPOSITORY):
        failures.append(f"source origin is {remote or '<unset>'}")
    if commit != SOURCE_COMMIT:
        failures.append(f"source HEAD is {commit or '<unknown>'}")
    dirty_paths = {line[3:] for line in status if len(line) >= 4}
    unexpected = dirty_paths - EXPECTED_DIRTY_PATHS
    missing_patches = EXPECTED_DIRTY_PATHS - {"checkpoints"} - dirty_paths
    if unexpected:
        failures.append("unexpected source changes: " + ", ".join(sorted(unexpected)))
    if missing_patches:
        failures.append("runtime patches missing: " + ", ".join(sorted(missing_patches)))
    if not target_requirements_satisfied(runtime / "pydeps"):
        failures.append("isolated Python dependencies are missing")

    for relative, expected in EXPECTED_FILES.items():
        path = runtime / relative
        if not path.is_file():
            failures.append(f"required file is missing: {relative}")
            continue
        if not verify_large_files and path.stat().st_size > 1024 * 1024 * 1024:
            continue
        actual = sha256_file(path)
        if actual != expected:
            failures.append(f"{relative} SHA-256 is {actual}, expected {expected}")
    checkpoint_link = source / "checkpoints"
    if not checkpoint_link.is_symlink() or checkpoint_link.resolve() != (runtime / "models").resolve():
        failures.append("source/checkpoints must link to the pinned runtime models directory")
    return failures


def _apply_runtime_patch(source: Path) -> None:
    check = _git(source, "apply", "--check", str(PATCH), check=False)
    if check.returncode == 0:
        _git(source, "apply", str(PATCH))
        return
    reverse = _git(source, "apply", "--reverse", "--check", str(PATCH), check=False)
    if reverse.returncode != 0:
        raise RuntimeError("LatentSync runtime patch cannot be applied cleanly")


def _install_source(runtime: Path) -> None:
    source = runtime / "source"
    if not source.exists():
        subprocess.run(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                SOURCE_REPOSITORY,
                str(source),
            ],
            check=True,
        )
        _git(source, "checkout", "--detach", SOURCE_COMMIT)
    else:
        commit = _git(source, "rev-parse", "HEAD").stdout.strip()
        remote = _git(source, "config", "--get", "remote.origin.url").stdout.strip()
        if commit != SOURCE_COMMIT or _repository_identity(remote) != _repository_identity(
            SOURCE_REPOSITORY
        ):
            raise RuntimeError("Refusing to replace a non-matching LatentSync checkout")
    _apply_runtime_patch(source)
    models = runtime / "models"
    models.mkdir(parents=True, exist_ok=True)
    checkpoint_link = source / "checkpoints"
    if checkpoint_link.exists() or checkpoint_link.is_symlink():
        if not checkpoint_link.is_symlink() or checkpoint_link.resolve() != models.resolve():
            raise RuntimeError("Refusing to replace source/checkpoints")
    else:
        checkpoint_link.symlink_to(models)
    shutil.copy2(CONFIG, runtime / "stage2_512-mask3.yaml")


def _install_target_requirements(runtime: Path) -> None:
    target = runtime / "pydeps"
    if target_requirements_satisfied(target):
        return
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise RuntimeError(f"Refusing to replace invalid pydeps target: {target}")
    runtime.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".pydeps-", dir=runtime) as temp_dir:
        clean_target = Path(temp_dir)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--upgrade",
                "--target",
                str(clean_target),
                "--no-deps",
                *TARGET_REQUIREMENTS,
            ],
            check=True,
        )
        if not target_requirements_satisfied(clean_target):
            raise RuntimeError(
                "LatentSync pinned dependency validation failed after install"
            )
        if target.exists():
            shutil.rmtree(target)
        clean_target.replace(target)


def _download_models(runtime: Path) -> None:
    from huggingface_hub import hf_hub_download, snapshot_download

    models = runtime / "models"
    models.mkdir(parents=True, exist_ok=True)
    for filename in ("latentsync_unet.pt", "whisper/tiny.pt"):
        target = models / filename
        expected = EXPECTED_FILES[f"models/{filename}"]
        if target.is_file() and sha256_file(target) == expected:
            continue
        hf_hub_download(
            repo_id=MODEL_REPOSITORY,
            filename=filename,
            revision=MODEL_REVISION,
            local_dir=models,
        )
    vae = models / "vae"
    if not (
        (vae / "config.json").is_file()
        and (vae / "diffusion_pytorch_model.safetensors").is_file()
        and sha256_file(vae / "config.json") == EXPECTED_FILES["models/vae/config.json"]
        and sha256_file(vae / "diffusion_pytorch_model.safetensors")
        == EXPECTED_FILES["models/vae/diffusion_pytorch_model.safetensors"]
    ):
        snapshot_download(
            repo_id=VAE_REPOSITORY,
            revision=VAE_REVISION,
            allow_patterns=("config.json", "diffusion_pytorch_model.safetensors"),
            local_dir=vae,
        )


def install_runtime(runtime: Path) -> None:
    runtime.mkdir(parents=True, exist_ok=True)
    _install_source(runtime)
    _install_target_requirements(runtime)
    _download_models(runtime)
    failures = runtime_failures(runtime)
    if failures:
        raise RuntimeError("LatentSync runtime validation failed:\n- " + "\n- ".join(failures))


def main(argv: Sequence[str]) -> int:
    if len(argv) != 2 or argv[0] not in {"check", "install"}:
        raise SystemExit("usage: latentsync16_deps.py {check|install} RUNTIME_DIR")
    runtime = Path(argv[1]).resolve()
    if argv[0] == "install":
        install_runtime(runtime)
    else:
        failures = runtime_failures(runtime)
        if failures:
            print("LatentSync runtime validation failed:", file=sys.stderr)
            print("\n".join(f"- {failure}" for failure in failures), file=sys.stderr)
            return 1
    print(f"LatentSync 1.6 runtime ready: {runtime}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
