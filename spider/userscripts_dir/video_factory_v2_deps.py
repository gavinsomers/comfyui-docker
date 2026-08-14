#!/usr/bin/env python3
"""Reproducibility checks for the approved V2 LivePortrait motion provider."""

from __future__ import annotations

import hashlib
import importlib
import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Iterable, Sequence


REQUIREMENTS = (
    "more-itertools>=10.8,<11",
    "pykalman>=0.10,<1",
    "scikit-image>=0.25,<1",
)
IMPORTS = ("more_itertools", "pykalman", "skimage")
CORE_NODE_CLASSES = (
    "EmptyImage",
    "FeatherMask",
    "ImageCompositeMasked",
    "ImageCrop",
    "ImageScale",
    "LoadImage",
    "SolidMask",
    "VAELoader",
)
EXISTING_PROVIDER_CLASSES = (
    "GrowMaskWithBlur",
    "VHS_LoadAudio",
    "VHS_LoadVideo",
    "VHS_VideoCombine",
)
CUSTOM_NODE_PROVIDERS = {
    "ComfyUI-LivePortraitKJ": {
        "directory": "ComfyUI_LivePortraitKJ",
        "repository": "https://github.com/kijai/ComfyUI-LivePortraitKJ.git",
        "commit": "4d9dc6205b793ffd0fb319816136d9b8c0dbfdff",
        "classes": (
            "DownloadAndLoadLivePortraitModels",
            "LivePortraitComposite",
            "LivePortraitCropper",
            "LivePortraitLoadFaceAlignmentCropper",
            "LivePortraitProcess",
        ),
    },
}
LANDMARK_REPOSITORY = "Kijai/LivePortrait_safetensors"
LANDMARK_REVISION = "59f30f36d7b791929c25437df7461d5b0e0010b1"
LANDMARK_FILENAME = "landmark.onnx"
LANDMARK_SHA256 = "31d22a5041326c31f19b78886939a634a5aedcaa5ab8b9b951a1167595d147db"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def landmark_model_failure(path: Path) -> str | None:
    if not path.is_file():
        return f"LivePortrait landmark model is missing: {path}"
    actual = sha256_file(path)
    if actual != LANDMARK_SHA256:
        return f"LivePortrait landmark model SHA-256 is {actual}, expected {LANDMARK_SHA256}"
    return None


def _download_landmark() -> Path:
    from huggingface_hub import hf_hub_download

    return Path(
        hf_hub_download(
            repo_id=LANDMARK_REPOSITORY,
            filename=LANDMARK_FILENAME,
            revision=LANDMARK_REVISION,
        )
    )


def ensure_landmark_model(models_dir: str | Path) -> Path:
    target = Path(models_dir) / "liveportrait" / LANDMARK_FILENAME
    if landmark_model_failure(target) is None:
        return target

    source = _download_landmark()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    try:
        shutil.copy2(source, temporary)
        failure = landmark_model_failure(temporary)
        if failure:
            raise RuntimeError(f"Downloaded {failure}")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def requirements_satisfied(
    requirements: Sequence[str] = REQUIREMENTS,
    modules: Sequence[str] = IMPORTS,
) -> bool:
    try:
        from packaging.requirements import Requirement
    except ImportError:
        return False
    for text in requirements:
        requirement = Requirement(text)
        try:
            installed = version(requirement.name)
        except PackageNotFoundError:
            return False
        if not requirement.specifier.contains(installed, prereleases=True):
            return False
    try:
        for module in modules:
            importlib.import_module(module)
    except ImportError:
        return False
    return True


def _git_output(provider_dir: Path, *args: str) -> tuple[bool, str]:
    result = subprocess.run(
        ["git", "-C", str(provider_dir), *args],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0, result.stdout.strip()


def _repository_identity(repository: str) -> str:
    normalized = repository.strip().rstrip("/")
    for prefix in (
        "https://github.com/",
        "http://github.com/",
        "ssh://git@github.com/",
        "git@github.com:",
    ):
        if normalized.casefold().startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    return normalized.removesuffix(".git").casefold()


def custom_node_provider_failures(
    custom_nodes_dir: str | Path,
) -> dict[str, tuple[str, ...]]:
    root = Path(custom_nodes_dir)
    failures = {}
    for name, provider in CUSTOM_NODE_PROVIDERS.items():
        provider_dir = root / provider["directory"]
        problems = []
        if not provider_dir.is_dir():
            failures[name] = ("provider directory is missing",)
            continue
        source = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in provider_dir.rglob("*.py")
        )
        missing_classes = [item for item in provider["classes"] if item not in source]
        if missing_classes:
            problems.append(f"missing node classes: {', '.join(missing_classes)}")
        remote_ok, remote = _git_output(
            provider_dir, "config", "--get", "remote.origin.url"
        )
        commit_ok, commit = _git_output(provider_dir, "rev-parse", "HEAD")
        status_ok, status = _git_output(
            provider_dir, "status", "--porcelain", "--untracked-files=all"
        )
        if not (remote_ok and commit_ok and status_ok):
            problems.append("provider is not a readable Git checkout")
        else:
            if _repository_identity(remote) != _repository_identity(
                provider["repository"]
            ):
                problems.append(f"origin is {remote or '<unset>'}")
            if commit.casefold() != provider["commit"]:
                problems.append(f"HEAD is {commit or '<unknown>'}")
            if status:
                problems.append("checkout has local or untracked changes")
        if problems:
            failures[name] = tuple(problems)
    return failures


def missing_registered_nodes(
    available_node_classes: Iterable[str],
) -> tuple[str, ...]:
    required = set(CORE_NODE_CLASSES) | set(EXISTING_PROVIDER_CLASSES)
    for provider in CUSTOM_NODE_PROVIDERS.values():
        required.update(provider["classes"])
    return tuple(sorted(required - set(available_node_classes)))


def format_provider_failures(
    failures: dict[str, tuple[str, ...]], custom_nodes_dir: str | Path
) -> str:
    root = Path(custom_nodes_dir)
    lines = ["V2 presenter custom-node preflight failed:"]
    for name, problems in failures.items():
        provider = CUSTOM_NODE_PROVIDERS[name]
        provider_dir = root / provider["directory"]
        lines.extend(
            [
                f"- {name}: {'; '.join(problems)}",
                f"  install: git clone --no-checkout {provider['repository']} {provider_dir}",
                f"  pin: git -C {provider_dir} checkout --detach {provider['commit']}",
            ]
        )
    lines.append("Move an existing non-Git or modified directory aside before installing.")
    return "\n".join(lines)


def main(argv: Sequence[str]) -> int:
    if list(argv) == ["requirements"]:
        print("\n".join(REQUIREMENTS))
        return 0
    if list(argv) == ["check"]:
        return 0 if requirements_satisfied() else 1
    if len(argv) == 2 and argv[0] == "check-nodes":
        failures = custom_node_provider_failures(argv[1])
        if failures:
            print(format_provider_failures(failures, argv[1]), file=sys.stderr)
            return 1
        return 0
    if len(argv) == 2 and argv[0] == "ensure-landmark":
        target = ensure_landmark_model(argv[1])
        failure = landmark_model_failure(target)
        if failure:
            print(failure, file=sys.stderr)
            return 1
        print(f"LivePortrait landmark model ready: {target}")
        return 0
    raise SystemExit(
        "usage: video_factory_v2_deps.py "
        "{check|requirements|check-nodes CUSTOM_NODES_DIR|ensure-landmark MODELS_DIR}"
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
