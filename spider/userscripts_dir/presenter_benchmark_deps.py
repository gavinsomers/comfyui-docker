#!/usr/bin/env python3
"""Check the optional local presenter benchmark dependencies."""

from __future__ import annotations

import importlib
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
        "repository": "https://github.com/kijai/ComfyUI-WanVideoWrapper.git",
        "revision": "longcat_avatar",
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
        "repository": "https://github.com/kijai/ComfyUI-KJNodes.git",
        "revision": "main",
        "classes": ("FloatConstant", "ImageResizeKJv2", "INTConstant"),
    },
    "ComfyUI-MelBandRoFormer": {
        "repository": "https://github.com/kijai/ComfyUI-MelBandRoFormer.git",
        "revision": "main",
        "classes": ("MelBandRoFormerModelLoader", "MelBandRoFormerSampler"),
    },
    "ComfyUI-VideoHelperSuite": {
        "repository": "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git",
        "revision": "main",
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
        provider_dir = root / provider_name
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
            f"- {provider_name} ({provider['revision']}): "
            f"missing {', '.join(missing_classes)}"
        )
        lines.append(f"  expected: {root / provider_name}")
        lines.append(
            f"  provider: {provider['repository']} revision {provider['revision']}"
        )
    lines.append("Install or update these providers, then restart ComfyUI.")
    return "\n".join(lines)


def main(argv: Sequence[str]) -> int:
    if list(argv) == ["requirements"]:
        print("\n".join(REQUIREMENTS))
        return 0
    if list(argv) == ["check"]:
        return 0 if requirements_satisfied() else 1
    if len(argv) == 2 and argv[0] == "check-nodes":
        missing = missing_custom_node_sources(argv[1])
        if missing:
            print(format_missing_custom_nodes(missing, argv[1]), file=sys.stderr)
            return 1
        return 0
    raise SystemExit(
        "usage: presenter_benchmark_deps.py "
        "{check|requirements|check-nodes CUSTOM_NODES_DIR}"
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
