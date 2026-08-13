#!/usr/bin/env python3
"""Render a same-input local presenter benchmark through modular adapters."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from video_factory.core import (  # noqa: E402
    DEFAULT_BASEDIR,
    DEFAULT_SERVER,
    ffprobe_duration,
    render_adapter,
    sha256_file,
    stage_input,
    write_json_atomic,
)
from video_factory.qa import (  # noqa: E402
    analyze_presenter_video,
    evaluate_presenter_qa,
    write_contact_sheets,
)

DEFAULT_PROMPT = (
    "A single presenter speaks directly to the camera in a chest-up composition. "
    "Natural lip movement follows the supplied narration, with restrained blinking, "
    "tiny head movements, and modest facial expression. Preserve the exact face, "
    "clothing, background, lighting, and camera distance from the input portrait. "
    "Locked-off camera, no cuts, no zoom, no hand gestures entering frame, no "
    "subtitles, no text, no logos, and no identity change."
)
ENGINE_SETTINGS = {
    "ltx23_presenter": {"width": 1024, "height": 576},
    "ltx25_presenter": {"width": 1024, "height": 576},
    # 1024x576 exhausted 32 GB on the RTX 5090 proof. This retains the exact
    # 16:9 center-crop framing at LongCat's practical local resolution.
    "longcat_avatar_presenter": {"width": 768, "height": 432},
}


class GpuMonitor:
    def __init__(self, sample_seconds: float = 0.5):
        self.sample_seconds = sample_seconds
        self.samples: list[dict[str, float | int]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _sample(self) -> None:
        while not self._stop.is_set():
            try:
                result = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=memory.used,utilization.gpu",
                        "--format=csv,noheader,nounits",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                memory, utilization = result.stdout.strip().split(",")[:2]
                self.samples.append(
                    {
                        "elapsed_seconds": round(time.monotonic() - self.started, 3),
                        "memory_used_mib": int(memory.strip()),
                        "utilization_percent": int(utilization.strip()),
                    }
                )
            except (OSError, subprocess.SubprocessError, ValueError):
                pass
            self._stop.wait(self.sample_seconds)

    def __enter__(self) -> "GpuMonitor":
        self.started = time.monotonic()
        self._thread = threading.Thread(target=self._sample, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.sample_seconds * 2)

    def summary(self) -> dict[str, int | None]:
        return {
            "samples": len(self.samples),
            "peak_memory_used_mib": max(
                (int(sample["memory_used_mib"]) for sample in self.samples),
                default=None,
            ),
            "peak_utilization_percent": max(
                (int(sample["utilization_percent"]) for sample in self.samples),
                default=None,
            ),
        }


def exact_audio_proof(rendered: Path, audio: Path, target: Path, duration: float) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(rendered),
            "-i",
            str(audio),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-vf",
            f"tpad=stop_mode=clone:stop_duration={duration:.3f}",
            "-t",
            f"{duration:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(target),
        ],
        check=True,
    )
    return target


def render_engine(
    engine: str,
    *,
    image_name: str,
    audio_name: str,
    source_audio: Path,
    prompt: str,
    seed: int,
    duration: float,
    basedir: Path,
    server: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    dimensions = ENGINE_SETTINGS[engine]
    prefix = f"video_factory/presenter-benchmark/{engine}/{engine}"
    values: dict[str, Any] = {
        "prompt": prompt,
        "seed": seed,
        "duration": duration,
        "image": image_name,
        "audio": audio_name,
        "output_prefix": prefix,
    }
    if engine != "ltx23_presenter":
        values.update(dimensions)

    started = time.monotonic()
    with GpuMonitor() as gpu:
        rendered = render_adapter(
            engine,
            values,
            basedir=basedir,
            server=server,
            timeout_seconds=timeout_seconds,
        )
    render_seconds = time.monotonic() - started
    raw_path = Path(rendered["output_path"])
    proof_path = (
        basedir
        / "output"
        / "video_factory"
        / "presenter-benchmark"
        / engine
        / f"{engine}-exact-audio.mp4"
    )
    exact_audio_proof(raw_path, source_audio, proof_path, duration)
    contact_sheets = write_contact_sheets(
        proof_path,
        proof_path.with_suffix("").with_name(f"{proof_path.stem}-contact"),
    )
    diagnostics = analyze_presenter_video(proof_path)
    automatic_qa = evaluate_presenter_qa(
        diagnostics,
        minimum_mean=5.0,
        minimum_p95=9.0,
    )["automatic_visible_motion_screen"]
    return {
        "engine": engine,
        "prompt_id": rendered["prompt_id"],
        "dimensions": dimensions,
        "render_seconds": round(render_seconds, 3),
        "gpu": gpu.summary(),
        "raw_output": str(raw_path.resolve()),
        "raw_output_sha256": sha256_file(raw_path),
        "exact_audio_proof": str(proof_path.resolve()),
        "exact_audio_proof_sha256": sha256_file(proof_path),
        "contact_sheets": contact_sheets,
        "qa_diagnostics": diagnostics,
        "automatic_visible_motion_screen": automatic_qa,
        "manual_review": {
            "visible_articulation": "pending",
            "identity_stability": "pending",
            "beard_and_lip_visibility": "pending",
            "temporal_stability": "pending",
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark local presenter engines with identical source assets."
    )
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--seed", type=int, default=509003)
    parser.add_argument(
        "--engines",
        nargs="+",
        choices=sorted(ENGINE_SETTINGS),
        default=list(ENGINE_SETTINGS),
    )
    parser.add_argument("--basedir", type=Path, default=DEFAULT_BASEDIR)
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--timeout-seconds", type=float, default=3600)
    parser.add_argument("--report", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    basedir = args.basedir.expanduser().resolve()
    image = args.image.expanduser().resolve()
    audio = args.audio.expanduser().resolve()
    duration = ffprobe_duration(audio)
    if "longcat_avatar_presenter" in args.engines and not 3.8 <= duration <= 4.1:
        raise ValueError(
            "The included LongCat benchmark template is one 65-frame window and "
            "requires approximately four seconds of audio"
        )

    image_name = stage_input(
        image,
        basedir,
        f"video_factory/presenter-benchmark/inputs/portrait{image.suffix.lower()}",
    )
    audio_name = stage_input(
        audio,
        basedir,
        f"video_factory/presenter-benchmark/inputs/audio{audio.suffix.lower()}",
    )
    report = {
        "version": 1,
        "status": "manual-review-required",
        "selection": None,
        "selection_reason": "Complete visible-articulation review before selecting an engine.",
        "inputs": {
            "image": str(image),
            "image_sha256": sha256_file(image),
            "audio": str(audio),
            "audio_sha256": sha256_file(audio),
            "duration_seconds": round(duration, 3),
            "prompt": args.prompt,
            "seed": args.seed,
            "framing": "16:9 center crop",
        },
        "results": [],
    }
    report_path = (
        args.report.expanduser().resolve()
        if args.report
        else basedir
        / "output"
        / "video_factory"
        / "presenter-benchmark"
        / "benchmark-report.json"
    )
    for engine in args.engines:
        print(f"{engine}: rendering", flush=True)
        result = render_engine(
            engine,
            image_name=image_name,
            audio_name=audio_name,
            source_audio=audio,
            prompt=args.prompt,
            seed=args.seed,
            duration=duration,
            basedir=basedir,
            server=args.server,
            timeout_seconds=args.timeout_seconds,
        )
        report["results"].append(result)
        write_json_atomic(report_path, report)
        print(
            f"{engine}: {result['render_seconds']:.1f}s, "
            f"peak {result['gpu']['peak_memory_used_mib']} MiB -> "
            f"{result['exact_audio_proof']}",
            flush=True,
        )
    print(f"Benchmark report: {report_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
