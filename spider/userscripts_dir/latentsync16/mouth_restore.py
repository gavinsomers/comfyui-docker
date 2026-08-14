#!/usr/bin/env python3
"""Restore a LatentSync mouth region into its source LivePortrait video.

The approved V2 presenter method preserves every source pixel outside a small,
feathered ellipse tracked from 68-point lip landmarks. This script runs inside
the pinned CUDA 12.8 ``spider`` container, where the LivePortrait provider's
Face Alignment and BlazeFace implementation are available.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import cv2
import numpy as np
import torch
from ComfyUI_LivePortraitKJ.face_alignment import FaceAlignment, LandmarksType


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--generated", required=True)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--diagnostics", required=True)
    parser.add_argument("--fps", type=int, default=25)
    parser.add_argument("--frames", type=int, required=True)
    parser.add_argument("--pad-x", type=float, default=11.0)
    parser.add_argument("--pad-y", type=float, default=12.0)
    parser.add_argument("--minimum-radius-x", type=float, default=37.0)
    parser.add_argument("--minimum-radius-y", type=float, default=22.0)
    parser.add_argument("--feather-sigma", type=float, default=4.0)
    return parser.parse_args()


def read_frames(path: str, limit: int) -> tuple[list[np.ndarray], float]:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS))
    frames: list[np.ndarray] = []
    while len(frames) < limit:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    if not frames:
        raise RuntimeError(f"No frames decoded: {path}")
    return frames, fps


def largest_landmarks(aligner: FaceAlignment, frame: np.ndarray) -> np.ndarray | None:
    faces = aligner.get_landmarks_from_image(frame)
    if not faces:
        return None
    return max(
        (np.asarray(points, dtype=np.float32) for points in faces),
        key=lambda points: float(np.prod(points.max(axis=0) - points.min(axis=0))),
    )


def interpolate_missing(values: np.ndarray) -> np.ndarray:
    output = values.copy()
    indices = np.arange(len(output))
    for column in range(output.shape[1]):
        valid = np.isfinite(output[:, column])
        if not valid.any():
            raise RuntimeError("Face landmarks failed on every frame")
        output[:, column] = np.interp(indices, indices[valid], output[valid, column])
    return output


def temporal_smooth(values: np.ndarray) -> np.ndarray:
    padded = np.pad(values, ((2, 2), (0, 0)), mode="edge")
    median = np.stack([np.median(padded[i : i + 5], axis=0) for i in range(len(values))])
    smoothed = median.copy()
    for index in range(1, len(smoothed)):
        smoothed[index] = 0.65 * smoothed[index - 1] + 0.35 * median[index]
    return smoothed


def mask_for_frame(
    shape: tuple[int, int, int], parameters: np.ndarray, sigma: float
) -> np.ndarray:
    height, width = shape[:2]
    center_x, center_y, radius_x, radius_y = parameters
    hard = np.zeros((height, width), dtype=np.uint8)
    cv2.ellipse(
        hard,
        (int(round(center_x)), int(round(center_y))),
        (int(round(radius_x)), int(round(radius_y))),
        0,
        0,
        360,
        255,
        thickness=-1,
        lineType=cv2.LINE_AA,
    )
    kernel = max(3, int(round(sigma * 6)) | 1)
    alpha = cv2.GaussianBlur(
        hard.astype(np.float32) / 255.0,
        (kernel, kernel),
        sigmaX=sigma,
        sigmaY=sigma,
    )
    return np.clip(alpha, 0.0, 1.0)


def main() -> None:
    args = parse_args()
    source_frames, source_fps = read_frames(args.source, args.frames)
    generated_frames, generated_fps = read_frames(args.generated, args.frames)
    if len(source_frames) != args.frames:
        raise RuntimeError(
            f"Source frame count mismatch: expected {args.frames}, decoded {len(source_frames)}"
        )
    if len(generated_frames) != args.frames:
        raise RuntimeError(
            "Generated frame count mismatch: "
            f"expected {args.frames}, decoded {len(generated_frames)}"
        )
    if abs(source_fps - args.fps) > 0.01 or abs(generated_fps - args.fps) > 0.01:
        raise RuntimeError(
            f"FPS mismatch: expected {args.fps}, source {source_fps}, "
            f"generated {generated_fps}"
        )
    frame_count = args.frames
    if source_frames[0].shape != generated_frames[0].shape:
        raise RuntimeError(
            f"Frame shape mismatch: {source_frames[0].shape} "
            f"vs {generated_frames[0].shape}"
        )

    aligner = FaceAlignment(
        LandmarksType.TWO_D,
        flip_input=False,
        device="cuda",
        dtype=torch.float16,
        face_detector="blazeface",
        face_detector_kwargs={"back_model": True},
    )

    raw = np.full((frame_count, 4), np.nan, dtype=np.float32)
    detection_failures: list[int] = []
    for index, (source, generated) in enumerate(zip(source_frames, generated_frames)):
        source_landmarks = largest_landmarks(aligner, source)
        generated_landmarks = largest_landmarks(aligner, generated)
        if source_landmarks is None and generated_landmarks is None:
            detection_failures.append(index)
            continue
        mouth_sets = []
        if source_landmarks is not None:
            mouth_sets.append(source_landmarks[48:68])
        if generated_landmarks is not None:
            mouth_sets.append(generated_landmarks[48:68])
        mouth = np.concatenate(mouth_sets, axis=0)
        minimum = mouth.min(axis=0)
        maximum = mouth.max(axis=0)
        center = (minimum + maximum) / 2
        radius_x = max(args.minimum_radius_x, float((maximum[0] - minimum[0]) / 2 + args.pad_x))
        radius_y = max(args.minimum_radius_y, float((maximum[1] - minimum[1]) / 2 + args.pad_y))
        raw[index] = [center[0], center[1], radius_x, radius_y]

    parameters = temporal_smooth(interpolate_missing(raw))
    output = Path(args.output)
    diagnostics_path = Path(args.diagnostics)
    output.parent.mkdir(parents=True, exist_ok=True)
    diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
    silent = output.with_name(output.stem + "-silent.mp4")

    height, width = source_frames[0].shape[:2]
    ffmpeg = subprocess.Popen(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            f"{width}x{height}",
            "-r",
            str(args.fps),
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "12",
            "-pix_fmt",
            "yuv420p",
            str(silent),
        ],
        stdin=subprocess.PIPE,
    )
    if ffmpeg.stdin is None:
        raise RuntimeError("ffmpeg stdin unavailable")

    mask_fractions: list[float] = []
    feather_fractions: list[float] = []
    outside_absolute_differences: list[float] = []
    preview_candidates: list[tuple[float, int, np.ndarray, np.ndarray]] = []
    for index, (source, generated, frame_parameters) in enumerate(
        zip(source_frames, generated_frames, parameters)
    ):
        alpha = mask_for_frame(source.shape, frame_parameters, args.feather_sigma)
        alpha_3d = alpha[:, :, None]
        composite = np.rint(
            generated.astype(np.float32) * alpha_3d
            + source.astype(np.float32) * (1.0 - alpha_3d)
        )
        composite = np.clip(composite, 0, 255).astype(np.uint8)
        ffmpeg.stdin.write(composite.tobytes())

        hard_inside = alpha >= 0.999
        feather = (alpha > 0.001) & (alpha < 0.999)
        outside = alpha <= 0.001
        mask_fractions.append(float((alpha > 0.001).mean()))
        feather_fractions.append(float(feather.mean()))
        outside_difference = np.abs(composite.astype(np.int16) - source.astype(np.int16))[outside]
        outside_absolute_differences.append(
            float(outside_difference.max()) if outside_difference.size else 0.0
        )
        preview_candidates.append((float(frame_parameters[2] * frame_parameters[3]), index, composite, alpha))

    ffmpeg.stdin.close()
    return_code = ffmpeg.wait()
    if return_code:
        raise RuntimeError(f"ffmpeg video encode failed with exit code {return_code}")

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(silent),
            "-i",
            args.audio,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-frames:v",
            str(frame_count),
            "-t",
            f"{frame_count / args.fps:.3f}",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output),
        ],
        check=True,
    )
    silent.unlink()

    _, preview_index, preview_composite, preview_alpha = max(preview_candidates)
    preview_source = source_frames[preview_index]
    preview_generated = generated_frames[preview_index]
    heat = cv2.applyColorMap(np.uint8(preview_alpha * 255), cv2.COLORMAP_TURBO)
    overlay = cv2.addWeighted(preview_source, 0.65, heat, 0.35, 0)
    preview = np.concatenate([preview_source, preview_generated, preview_composite, overlay], axis=1)
    preview_path = diagnostics_path.with_name("mask-preview.png")
    cv2.imwrite(str(preview_path), preview)

    report = {
        "version": 1,
        "method": "tracked-source-preserving-mouth-composite",
        "source": args.source,
        "generated": args.generated,
        "audio": args.audio,
        "output": str(output),
        "frame_count": frame_count,
        "fps": args.fps,
        "source_fps": source_fps,
        "generated_fps": generated_fps,
        "landmarks": (
            "Face Alignment 68-point with BlazeFace; union of source and "
            "generated lip landmarks (48:68)"
        ),
        "mask": {
            "shape": "temporally-smoothed ellipse",
            "pad_x_pixels": args.pad_x,
            "pad_y_pixels": args.pad_y,
            "minimum_radius_x_pixels": args.minimum_radius_x,
            "minimum_radius_y_pixels": args.minimum_radius_y,
            "feather_sigma_pixels": args.feather_sigma,
            "mean_affected_frame_fraction": float(np.mean(mask_fractions)),
            "maximum_affected_frame_fraction": float(np.max(mask_fractions)),
            "mean_feather_frame_fraction": float(np.mean(feather_fractions)),
        },
        "source_preservation": {
            "definition": "Pixels with alpha <= 0.001 before video encoding",
            "maximum_absolute_channel_difference": float(np.max(outside_absolute_differences)),
        },
        "detection_failure_frames": detection_failures,
        "parameters_per_frame": parameters.round(4).tolist(),
        "preview_frame": preview_index,
        "preview": str(preview_path),
    }
    diagnostics_path.write_text(json.dumps(report, indent=2) + "\n")
    summary_keys = (
        "output",
        "frame_count",
        "mask",
        "source_preservation",
        "detection_failure_frames",
        "preview",
    )
    print(json.dumps({key: report[key] for key in summary_keys}, indent=2))


if __name__ == "__main__":
    main()
