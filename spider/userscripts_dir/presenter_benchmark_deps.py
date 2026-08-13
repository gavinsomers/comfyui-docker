#!/usr/bin/env python3
"""Check the optional local presenter benchmark dependencies."""

from __future__ import annotations

import importlib
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Iterable, Sequence


REQUIREMENTS = (
    "packaging",
    "ftfy",
    "diffusers>=0.33.0",
    "peft>=0.17.0",
    "pyloudnorm",
    "gguf>=0.17.1",
    "opencv-python-headless",
    "rotary_embedding_torch",
    "imageio-ffmpeg",
    "color-matcher",
    "matplotlib",
    "mss",
)
IMPORTS = (
    "cv2",
    "diffusers",
    "ftfy",
    "gguf",
    "imageio_ffmpeg",
    "matplotlib",
    "mss",
    "peft",
    "pyloudnorm",
    "rotary_embedding_torch",
)
CORE_NODE_CLASSES = ("LoadAudio", "LoadImage", "TrimAudioDuration")
CUSTOM_NODE_PROVIDERS = {
    "ComfyUI-WanVideoWrapper": {
        "directory": "ComfyUI-WanVideoWrapper",
        "repository": "https://github.com/kijai/ComfyUI-WanVideoWrapper.git",
        "commit": "e091c4a77425d6a4a7f90ab30c513d24f8cb91cf",
        "classes": (
            "DownloadAndLoadWav2VecModel",
            "MultiTalkWav2VecEmbeds",
            "WanVideoBlockSwap",
            "WanVideoDecode",
            "WanVideoEncode",
            "WanVideoLongCatAvatarExtendEmbeds",
            "WanVideoLoraSelect",
            "WanVideoModelLoader",
            "WanVideoSamplerv2",
            "WanVideoSchedulerv2",
            "WanVideoTextEncodeCached",
            "WanVideoVAELoader",
        ),
    },
    "ComfyUI-KJNodes": {
        "directory": "comfyui-kjnodes",
        "repository": "https://github.com/kijai/ComfyUI-KJNodes.git",
        "commit": "f710f2635dbadbaf1ccf7d25572daa7dfec80bfd",
        "classes": ("FloatConstant", "ImageResizeKJv2", "INTConstant"),
    },
    "ComfyUI-MelBandRoFormer": {
        "directory": "ComfyUI-MelBandRoFormer",
        "repository": "https://github.com/kijai/ComfyUI-MelBandRoFormer.git",
        "commit": "92c86854e6654f4aacc97484471af95c98ea16d4",
        "classes": ("MelBandRoFormerModelLoader", "MelBandRoFormerSampler"),
    },
    "ComfyUI-VideoHelperSuite": {
        "directory": "comfyui-videohelpersuite",
        "repository": "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git",
        "commit": "3234937ff5f3ca19068aaba5042771514de2429d",
        "classes": ("VHS_VideoCombine",),
    },
}


def requirements_satisfied(
    requirements: Sequence[str] = REQUIREMENTS,
    modules: Sequence[str] = IMPORTS,
) -> bool:
    try:
        from packaging.requirements import Requirement
    except ImportError:
        return False

    for requirement_text in requirements:
        requirement = Requirement(requirement_text)
        try:
            installed_version = version(requirement.name)
        except PackageNotFoundError:
            return False
        if not requirement.specifier.contains(installed_version, prereleases=True):
            return False
    try:
        for module in modules:
            importlib.import_module(module)
    except ImportError:
        return False
    return True


def missing_custom_node_sources(
    custom_nodes_dir: str | Path,
) -> dict[str, tuple[str, ...]]:
    root = Path(custom_nodes_dir)
    missing = {}
    for provider_name, provider in CUSTOM_NODE_PROVIDERS.items():
        provider_dir = root / provider["directory"]
        source_files = (
            list(provider_dir.rglob("*.py")) if provider_dir.is_dir() else []
        )
        source = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in source_files
        )
        missing_classes = tuple(
            node_class
            for node_class in provider["classes"]
            if node_class not in source
        )
        if missing_classes:
            missing[provider_name] = missing_classes
    return missing


def _git_output(provider_dir: Path, *args: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(provider_dir), *args],
            capture_output=True,
            text=True,
        )
    except OSError:
        return False, ""
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
    if normalized.casefold().endswith(".git"):
        normalized = normalized[:-4]
    return normalized.casefold()


def custom_node_provider_failures(
    custom_nodes_dir: str | Path,
) -> dict[str, tuple[str, ...]]:
    root = Path(custom_nodes_dir)
    missing_sources = missing_custom_node_sources(root)
    failures = {}
    for provider_name, provider in CUSTOM_NODE_PROVIDERS.items():
        provider_dir = root / provider["directory"]
        provider_failures = []
        if not provider_dir.is_dir():
            failures[provider_name] = ("provider directory is missing",)
            continue

        if missing_classes := missing_sources.get(provider_name):
            provider_failures.append(
                f"missing node classes: {', '.join(missing_classes)}"
            )

        remote_ok, remote = _git_output(
            provider_dir, "config", "--get", "remote.origin.url"
        )
        commit_ok, commit = _git_output(provider_dir, "rev-parse", "HEAD")
        status_ok, status = _git_output(
            provider_dir, "status", "--porcelain", "--untracked-files=all"
        )
        if not (remote_ok and commit_ok and status_ok):
            provider_failures.append("provider is not a readable Git checkout")
        else:
            if _repository_identity(remote) != _repository_identity(
                provider["repository"]
            ):
                provider_failures.append(f"origin is {remote or '<unset>'}")
            if commit.casefold() != provider["commit"]:
                provider_failures.append(f"HEAD is {commit or '<unknown>'}")
            if status:
                provider_failures.append("checkout has local or untracked changes")

        if provider_failures:
            failures[provider_name] = tuple(provider_failures)
    return failures


def missing_registered_nodes(
    available_node_classes: Iterable[str],
) -> dict[str, tuple[str, ...]]:
    available = set(available_node_classes)
    return {
        provider_name: missing_classes
        for provider_name, provider in CUSTOM_NODE_PROVIDERS.items()
        if (
            missing_classes := tuple(
                node_class
                for node_class in provider["classes"]
                if node_class not in available
            )
        )
    }


def format_missing_custom_nodes(
    missing: dict[str, tuple[str, ...]],
    custom_nodes_dir: str | Path = "/basedir/custom_nodes",
) -> str:
    root = Path(custom_nodes_dir)
    lines = ["LongCat custom-node preflight failed:"]
    for provider_name, missing_classes in missing.items():
        provider = CUSTOM_NODE_PROVIDERS[provider_name]
        lines.append(
            f"- {provider_name} ({provider['commit']}): "
            f"missing {', '.join(missing_classes)}"
        )
        lines.append(f"  expected: {root / provider['directory']}")
        lines.append(
            f"  provider: {provider['repository']} commit {provider['commit']}"
        )
    lines.append("Install or update these providers, then restart ComfyUI.")
    return "\n".join(lines)


def format_custom_node_provider_failures(
    failures: dict[str, tuple[str, ...]],
    custom_nodes_dir: str | Path = "/basedir/custom_nodes",
) -> str:
    root = Path(custom_nodes_dir)
    lines = ["LongCat custom-node preflight failed:"]
    for provider_name, provider_failures in failures.items():
        provider = CUSTOM_NODE_PROVIDERS[provider_name]
        provider_dir = root / provider["directory"]
        lines.append(f"- {provider_name}: {'; '.join(provider_failures)}")
        lines.append(f"  expected path: {provider_dir}")
        lines.append(f"  expected origin: {provider['repository']}")
        lines.append(f"  expected commit: {provider['commit']}")
        lines.append(
            f"  install: git clone --no-checkout {provider['repository']} "
            f"{provider_dir}"
        )
        lines.append(
            f"  pin: git -C {provider_dir} checkout --detach "
            f"{provider['commit']}"
        )
    lines.append(
        "Move any non-Git or modified provider directory aside before installing, "
        "then restart ComfyUI."
    )
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
            print(
                format_custom_node_provider_failures(failures, argv[1]),
                file=sys.stderr,
            )
            return 1
        return 0
    raise SystemExit(
        "usage: presenter_benchmark_deps.py "
        "{check|requirements|check-nodes CUSTOM_NODES_DIR}"
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
