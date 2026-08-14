#!/usr/bin/env python3
"""Prepare ignored, rights-cleared local assets for the mosquito-control V2 render."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "aedes": {
        "url": "https://upload.wikimedia.org/wikipedia/commons/0/00/Mosquito-aedes-aegypti-high-res.webm",
        "sha256": "2d4a5ea6ab06b9c9dcde20e8a5908deefa2b71f22eb004f5229e47b08916f49e",
        "license": "Public domain",
        "creator": "Centers for Disease Control and Prevention",
        "title": "Mosquito-aedes-aegypti-high-res.webm",
    },
    "hibiscus": {
        "url": "https://upload.wikimedia.org/wikipedia/commons/9/9c/Bees_on_Hibiscus_syriacus.webm",
        "sha256": "b2f5ec1b0b3fc8c4aad33e167a8896c26c96bf8fc16dc215b5edb091a1152eed",
        "license": "CC0",
        "creator": "Cbaile19",
        "title": "Bees on Hibiscus syriacus.webm",
    },
    "meadow": {
        "url": "https://upload.wikimedia.org/wikipedia/commons/5/55/Flowers_%2820210715-FPAC-KLS-0002%29.webm",
        "sha256": "5fd25e23c9814b0caf44628ed87a9818d15461ed837767ff562d35b78e906e98",
        "license": "Public domain",
        "creator": "USDA / Kirsten Strough",
        "title": "Flowers (20210715-FPAC-KLS-0002).webm",
    },
}
CLIPS = {
    "mosquito-adult.mp4": ("aedes", 145.0, 5.5),
    "mosquito-larvae.mp4": ("aedes", 25.0, 5.5),
    "bees-hibiscus.mp4": ("hibiscus", 20.0, 5.5),
    "bees-meadow.mp4": ("meadow", 0.0, 5.5),
}
APPROVED_CORRECTIONS = {
    "s0006-murky-water.png": {
        "relative_source": "benchmarks/s0006-fix/seed-509906_00001_.png",
        "sha256": "3d46f883a2914680b52bb375602fd18bd4d5c6f6492d03d4d2a444d3f0538609",
    },
    "s0013-covered-bucket.png": {
        "relative_source": "benchmarks/s0013-fix/tight-510313_00001_.png",
        "sha256": "03550e35a342634b0cc0033617783bcd59eb8f1939f1af4de698e681fde0a551",
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(source: dict[str, str], target: Path) -> None:
    if target.exists() and sha256_file(target) == source["sha256"]:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(
        source["url"], headers={"User-Agent": "ComfyUI video-factory asset fetcher/2"}
    )
    with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as output:
        shutil.copyfileobj(response, output, length=1024 * 1024)
    actual = sha256_file(temporary)
    if actual != source["sha256"]:
        temporary.unlink(missing_ok=True)
        raise ValueError(
            f"Checksum mismatch for {source['title']}: {actual} != {source['sha256']}"
        )
    temporary.replace(target)


def extract_clip(source: Path, target: Path, start: float, duration: float) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(source),
            "-t",
            f"{duration:.3f}",
            "-an",
            "-vf",
            "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,fps=30",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(target),
        ],
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--basedir", type=Path, default=REPO_ROOT / "basedir")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    basedir = args.basedir.expanduser().resolve()
    root = basedir / "input" / "video_factory" / "mosquito-control-pilot-v2"
    downloads = root / ".downloads"
    stock = root / "stock"
    references = root / "references"
    approved = root / "approved"

    downloaded = {}
    for name, source in SOURCES.items():
        target = downloads / f"{name}.webm"
        print(f"asset source: {name}")
        download(source, target)
        downloaded[name] = target

    for filename, (source_name, start, duration) in CLIPS.items():
        target = stock / filename
        if args.overwrite or not target.exists():
            print(f"extracting: {filename}")
            extract_clip(downloaded[source_name], target, start, duration)

    v1_root = basedir / "output" / "video_factory" / "mosquito-control-pilot"
    reference_inputs = {
        v1_root / "presenter" / "master_00001_.png": references / "presenter-close.png",
        v1_root / "shots" / "s0003_00003_.mp4": references / "subtle-driving.mp4",
    }
    references.mkdir(parents=True, exist_ok=True)
    for source, target in reference_inputs.items():
        if not source.exists():
            raise FileNotFoundError(
                f"V1 presenter source is missing: {source}. Supply an equivalent authorized asset."
            )
        shutil.copy2(source, target)
        print(f"reference: {target.name}")

    v2_output = basedir / "output" / "video_factory" / "mosquito-control-pilot-v2"
    approved.mkdir(parents=True, exist_ok=True)
    prepared_corrections = {}
    for filename, correction in APPROVED_CORRECTIONS.items():
        source = v2_output / correction["relative_source"]
        target = approved / filename
        if source.exists() and sha256_file(source) == correction["sha256"]:
            shutil.copy2(source, target)
            print(f"approved correction: {target.name}")
        if not target.exists() or sha256_file(target) != correction["sha256"]:
            print(
                "approved correction unavailable: "
                f"{filename}; rerender and approve {correction['relative_source']}"
            )
            continue
        prepared_corrections[filename] = {
            "source": correction["relative_source"],
            "sha256": correction["sha256"],
        }

    credits = {
        "version": 1,
        "note": "Generated presenter assets are local V1 outputs. Stock source metadata follows.",
        "sources": list(SOURCES.values()),
        "prepared_clips": {
            filename: {
                "source": source_name,
                "source_start": start,
                "duration": duration,
                "sha256": sha256_file(stock / filename),
            }
            for filename, (source_name, start, duration) in CLIPS.items()
        },
        "approved_generated_corrections": prepared_corrections,
    }
    (root / "credits.json").write_text(
        json.dumps(credits, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Prepared V2 assets: {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
