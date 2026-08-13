#!/usr/bin/env python3
"""Check the optional local presenter benchmark dependencies."""

from __future__ import annotations

import importlib
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Sequence


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


def main(argv: Sequence[str]) -> int:
    if list(argv) == ["requirements"]:
        print("\n".join(REQUIREMENTS))
        return 0
    if list(argv) == ["check"]:
        return 0 if requirements_satisfied() else 1
    raise SystemExit("usage: presenter_benchmark_deps.py {check|requirements}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
