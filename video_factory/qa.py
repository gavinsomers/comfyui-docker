#!/usr/bin/env python3
"""Objective screen and human-review records for presenter lip-sync QA."""

from __future__ import annotations

import json
import math
import statistics
import struct
import subprocess
from pathlib import Path
from typing import Any, Sequence

from video_factory.core import ffprobe_duration, sha256_file, stable_hash

DEFAULT_MOUTH_ROI = {"x": 0.38, "y": 0.38, "width": 0.24, "height": 0.25}
DEFAULT_SAMPLE_FPS = 12
DEFAULT_FRAME_WIDTH = 96
DEFAULT_FRAME_HEIGHT = 64
PRESENTER_QA_ALGORITHM = "lower-face-luma-motion"
PRESENTER_QA_ALGORITHM_VERSION = 1
AUDIO_SAMPLES_PER_WINDOW = 100
MANUAL_CRITERIA = ("visible_articulation", "identity_stability", "temporal_stability")


def build_presenter_qa_policy(
    *,
    sample_fps: int = DEFAULT_SAMPLE_FPS,
    minimum_mean: float = 5.0,
    minimum_p95: float = 9.0,
    roi: dict[str, float] | None = None,
) -> dict[str, Any]:
    return {
        "algorithm": PRESENTER_QA_ALGORITHM,
        "algorithm_version": PRESENTER_QA_ALGORITHM_VERSION,
        "sample_fps": int(sample_fps),
        "mouth_roi": dict(DEFAULT_MOUTH_ROI if roi is None else roi),
        "frame_width": DEFAULT_FRAME_WIDTH,
        "frame_height": DEFAULT_FRAME_HEIGHT,
        "audio_samples_per_window": AUDIO_SAMPLES_PER_WINDOW,
        "minimum_mouth_motion_mean": float(minimum_mean),
        "minimum_mouth_motion_p95": float(minimum_p95),
        "manual_criteria": list(MANUAL_CRITERIA),
    }


def presenter_qa_policy_fingerprint(policy: dict[str, Any]) -> str:
    return stable_hash(policy)


def _decode_gray_frames(
    video: Path,
    *,
    roi: dict[str, float],
    fps: int,
    width: int = DEFAULT_FRAME_WIDTH,
    height: int = DEFAULT_FRAME_HEIGHT,
) -> list[bytes]:
    crop = (
        f"crop=iw*{roi['width']}:ih*{roi['height']}:"
        f"iw*{roi['x']}:ih*{roi['y']}"
    )
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-an",
            "-vf",
            f"fps={fps},{crop},scale={width}:{height},format=gray",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
    )
    frame_bytes = width * height
    return [
        result.stdout[offset : offset + frame_bytes]
        for offset in range(0, len(result.stdout), frame_bytes)
        if len(result.stdout[offset : offset + frame_bytes]) == frame_bytes
    ]


def frame_motion_series(frames: Sequence[bytes]) -> list[float]:
    """Return mean absolute luma change for every adjacent frame pair."""
    deltas = []
    for previous, current in zip(frames, frames[1:]):
        if len(previous) != len(current) or not previous:
            raise ValueError("Motion frames must be non-empty and equally sized")
        deltas.append(
            sum(abs(before - after) for before, after in zip(previous, current))
            / len(previous)
        )
    return deltas


def summarize_motion(deltas: Sequence[float]) -> dict[str, float | int]:
    if not deltas:
        raise ValueError("At least two decoded frames are required for motion QA")
    ordered = sorted(float(value) for value in deltas)
    p95_index = math.ceil(0.95 * len(ordered)) - 1
    return {
        "frame_pairs": len(ordered),
        "mean_absolute_luma_delta": round(statistics.mean(ordered), 4),
        "p95_absolute_luma_delta": round(ordered[p95_index], 4),
        "maximum_absolute_luma_delta": round(ordered[-1], 4),
    }


def _audio_envelope(video: Path, fps: int) -> list[float]:
    sample_rate = fps * AUDIO_SAMPLES_PER_WINDOW
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-f",
            "f32le",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
    )
    count = len(result.stdout) // 4
    if not count:
        return []
    samples = struct.unpack(f"<{count}f", result.stdout[: count * 4])
    return [
        math.sqrt(sum(sample * sample for sample in window) / len(window))
        for offset in range(0, len(samples), AUDIO_SAMPLES_PER_WINDOW)
        if len(window := samples[offset : offset + AUDIO_SAMPLES_PER_WINDOW])
        == AUDIO_SAMPLES_PER_WINDOW
    ]


def _correlation(left: Sequence[float], right: Sequence[float]) -> float | None:
    count = min(len(left), len(right))
    if count < 3:
        return None
    left = left[:count]
    right = right[:count]
    left_mean = statistics.mean(left)
    right_mean = statistics.mean(right)
    numerator = sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left, right)
    )
    denominator = math.sqrt(
        sum((value - left_mean) ** 2 for value in left)
        * sum((value - right_mean) ** 2 for value in right)
    )
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def analyze_presenter_video(
    video: str | Path,
    *,
    roi: dict[str, float] | None = None,
    sample_fps: int = DEFAULT_SAMPLE_FPS,
) -> dict[str, Any]:
    """Measure lower-face motion; this screens frozen mouths but is not SyncNet."""
    video_path = Path(video).expanduser().resolve()
    if not video_path.exists():
        raise FileNotFoundError(f"Presenter video not found: {video_path}")
    selected_roi = dict(DEFAULT_MOUTH_ROI if roi is None else roi)
    frames = _decode_gray_frames(video_path, roi=selected_roi, fps=sample_fps)
    deltas = frame_motion_series(frames)
    audio_envelope = _audio_envelope(video_path, sample_fps)
    # A frame delta describes motion ending at the current frame, hence [1:].
    zero_lag = _correlation(deltas, audio_envelope[1:])
    return {
        "version": 1,
        "video": str(video_path),
        "video_sha256": sha256_file(video_path),
        "duration_seconds": round(ffprobe_duration(video_path), 3),
        "sample_fps": sample_fps,
        "mouth_roi": selected_roi,
        "decoded_frames": len(frames),
        "motion": summarize_motion(deltas),
        "audio_motion_correlation_zero_lag": zero_lag,
        "metric_limitations": (
            "Lower-face luma motion rejects nearly frozen mouths but does not prove "
            "phoneme timing. Human visible-articulation review remains mandatory."
        ),
    }


def evaluate_presenter_qa(
    analysis: dict[str, Any],
    *,
    minimum_mean: float,
    minimum_p95: float,
    manual_review: dict[str, str | None] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    policy = build_presenter_qa_policy(
        sample_fps=int(analysis.get("sample_fps", DEFAULT_SAMPLE_FPS)),
        minimum_mean=minimum_mean,
        minimum_p95=minimum_p95,
        roi=analysis.get("mouth_roi"),
    )
    motion = analysis["motion"]
    screen_checks = {
        "mean_mouth_motion": {
            "value": motion["mean_absolute_luma_delta"],
            "minimum": float(minimum_mean),
        },
        "p95_mouth_motion": {
            "value": motion["p95_absolute_luma_delta"],
            "minimum": float(minimum_p95),
        },
    }
    for check in screen_checks.values():
        check["status"] = "pass" if check["value"] >= check["minimum"] else "fail"
    screen_status = (
        "pass"
        if all(check["status"] == "pass" for check in screen_checks.values())
        else "fail"
    )

    supplied_review = manual_review or {}
    normalized_review = {
        criterion: supplied_review.get(criterion) for criterion in MANUAL_CRITERIA
    }
    invalid = {
        criterion: value
        for criterion, value in normalized_review.items()
        if value not in {None, "pass", "fail"}
    }
    if invalid:
        raise ValueError(f"Invalid manual presenter QA values: {invalid}")
    if any(value == "fail" for value in normalized_review.values()):
        manual_status = "fail"
    elif all(value == "pass" for value in normalized_review.values()):
        manual_status = "pass"
    else:
        manual_status = "pending"

    if screen_status == "fail" or manual_status == "fail":
        status = "fail"
    elif screen_status == "pass" and manual_status == "pass":
        status = "pass"
    else:
        status = "pending"
    return {
        "version": 2,
        "status": status,
        "source_sha256": analysis["video_sha256"],
        "policy": policy,
        "policy_fingerprint": presenter_qa_policy_fingerprint(policy),
        "automatic_visible_motion_screen": {
            "status": screen_status,
            "checks": screen_checks,
            "limitations": analysis["metric_limitations"],
        },
        "manual_review": {
            "status": manual_status,
            "criteria": normalized_review,
            "notes": notes or "",
        },
        "analysis": analysis,
    }


def write_contact_sheets(video: str | Path, output_prefix: str | Path) -> dict[str, str]:
    """Write full-frame and lower-face sheets with 24 samples across the shot."""
    video_path = Path(video).expanduser().resolve()
    prefix = Path(output_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    duration = ffprobe_duration(video_path)
    sample_fps = max(0.1, 24 / max(duration, 0.1))
    full_path = prefix.with_name(f"{prefix.name}-full.png")
    mouth_path = prefix.with_name(f"{prefix.name}-mouth.png")
    roi = DEFAULT_MOUTH_ROI
    filters = {
        full_path: f"fps={sample_fps:.8f},scale=320:-2,tile=8x3",
        mouth_path: (
            f"fps={sample_fps:.8f},"
            f"crop=iw*{roi['width']}:ih*{roi['height']}:"
            f"iw*{roi['x']}:ih*{roi['y']},scale=320:240,tile=8x3"
        ),
    }
    for target, video_filter in filters.items():
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(video_path),
                "-vf",
                video_filter,
                "-frames:v",
                "1",
                str(target),
            ],
            check=True,
        )
    return {"full": str(full_path.resolve()), "mouth": str(mouth_path.resolve())}


def read_video_stream(path: str | Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    streams = json.loads(result.stdout).get("streams", [])
    return streams[0] if streams else {}
