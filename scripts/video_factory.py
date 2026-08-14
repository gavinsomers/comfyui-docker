#!/usr/bin/env python3
"""CLI for the reusable, manifest-driven local ComfyUI video factory."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from video_factory.core import (  # noqa: E402
    DEFAULT_BASEDIR,
    DEFAULT_SERVER,
    bind_project_assets,
    compile_shot_manifest,
    extract_audio_segment,
    ffprobe_duration,
    load_adapter,
    load_adapter_definition,
    load_project,
    load_state,
    missing_project_assets,
    render_adapter,
    request_json,
    save_state,
    sha256_file,
    stable_hash,
    stage_input,
    word_count,
    write_json_atomic,
    write_srt,
)
from video_factory.latentsync import (  # noqa: E402
    ENGINE_NAME as LATENTSYNC_ENGINE,
    build_cache_key as build_latentsync_cache_key,
    default_container as default_runtime_container,
    default_container_user as default_runtime_user,
    render as render_latentsync,
    validate_runtime as validate_latentsync_runtime,
)
from video_factory.qa import (  # noqa: E402
    MANUAL_CRITERIA,
    analyze_presenter_video,
    build_presenter_qa_policy,
    evaluate_presenter_qa,
    presenter_qa_policy_fingerprint,
    write_contact_sheets,
)


def runtime_paths(context: dict[str, Any], basedir: Path) -> dict[str, Path]:
    root = basedir / "output" / "video_factory" / context["slug"]
    return {
        "root": root,
        "manifest": root / "shot-manifest.json",
        "state": root / "state.json",
        "captions": root / "captions.srt",
        "presenter_qa": root / "presenter-qa.json",
        "presenter_qa_media": root / "qa" / "presenter",
        "clips": root / "assembly" / "clips",
        "delivery": root / f"{context['slug']}-1080p.mp4",
    }


def state_asset(state: dict[str, Any], key: str) -> dict[str, Any] | None:
    record = state.get("assets", {}).get(key)
    if not record:
        return None
    output_path = record.get("output_path")
    if output_path and Path(output_path).exists():
        return record
    return None


def record_supplied_asset(
    path: Path, kind: str, metadata: dict[str, Any] | None = None
) -> dict[str, Any]:
    details = metadata or {}
    source_sha256 = _verify_expected_sha256(
        path,
        details.get("source_expected_sha256"),
        f"Supplied {kind} asset {path}",
    )
    return {
        "status": "supplied",
        "kind": kind,
        "output_path": str(path.resolve()),
        "source_sha256": source_sha256,
        "cache_key": stable_hash(
            {"kind": kind, "sha256": source_sha256, "metadata": details}
        ),
        **details,
    }


def _verify_expected_sha256(path: Path, expected: str | None, label: str) -> str:
    actual = sha256_file(path)
    if expected and actual.casefold() != expected.casefold():
        raise ValueError(f"{label} SHA-256 is {actual}, expected {expected}")
    return actual


def render_with_cache(
    *,
    state: dict[str, Any],
    state_path: Path,
    key: str,
    adapter_name: str,
    values: dict[str, Any],
    basedir: Path,
    server: str,
    dry_run: bool,
    overwrite: bool,
    input_paths: list[Path] | None = None,
    timeout_seconds: float = 1800,
) -> dict[str, Any]:
    preview = render_adapter(
        adapter_name,
        values,
        basedir=basedir,
        server=server,
        dry_run=True,
    )
    input_hashes = [sha256_file(path) for path in input_paths or []]
    cache_key = stable_hash(
        {"workflow": preview["cache_key"], "input_hashes": input_hashes}
    )
    existing = state_asset(state, key)
    if existing and existing.get("cache_key") == cache_key and not overwrite:
        print(f"{key}: cached -> {existing['output_path']}")
        return existing

    if dry_run:
        print(f"{key}: would render with {adapter_name}")
        return {
            "status": "dry-run",
            "adapter": adapter_name,
            "cache_key": cache_key,
            "output_path": None,
        }

    print(f"{key}: rendering with {adapter_name}")
    state.setdefault("assets", {})[key] = {
        "status": "queued",
        "adapter": adapter_name,
        "cache_key": cache_key,
    }
    save_state(state_path, state)
    try:
        record = render_adapter(
            adapter_name,
            values,
            basedir=basedir,
            server=server,
            timeout_seconds=timeout_seconds,
        )
        record.update({"status": "success", "cache_key": cache_key})
    except Exception as exc:
        record = {
            "status": "error",
            "adapter": adapter_name,
            "cache_key": cache_key,
            "error": str(exc),
        }
        state["assets"][key] = record
        save_state(state_path, state)
        raise
    state["assets"][key] = record
    save_state(state_path, state)
    print(f"{key}: wrote {record['output_path']}")
    return record


def render_latentsync_with_cache(
    *,
    state: dict[str, Any],
    state_path: Path,
    key: str,
    source_video: Path,
    audio: Path,
    output: Path,
    seed: int,
    frame_count: int,
    fps: int,
    options: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    cache_key = build_latentsync_cache_key(
        source_video,
        audio,
        seed=seed,
        frame_count=frame_count,
        fps=fps,
        options=options,
    )
    existing = state_asset(state, key)
    if existing and existing.get("cache_key") == cache_key and not args.overwrite:
        print(f"{key}: cached -> {existing['output_path']}")
        return existing

    print(f"{key}: rendering with {LATENTSYNC_ENGINE}")
    request_json(
        args.server,
        "POST",
        "/free",
        {"unload_models": True, "free_memory": True},
    )
    time.sleep(3)
    state.setdefault("assets", {})[key] = {
        "status": "queued",
        "adapter": LATENTSYNC_ENGINE,
        "cache_key": cache_key,
    }
    save_state(state_path, state)
    try:
        record = render_latentsync(
            source_video,
            audio,
            output,
            basedir=args.basedir,
            seed=seed,
            frame_count=frame_count,
            fps=fps,
            options=options,
            container=getattr(args, "runtime_container", default_runtime_container()),
            user=getattr(args, "runtime_user", default_runtime_user()),
            timeout_seconds=args.timeout_seconds,
        )
        record["cache_key"] = cache_key
    except Exception as exc:
        state["assets"][key] = {
            "status": "error",
            "adapter": LATENTSYNC_ENGINE,
            "cache_key": cache_key,
            "error": str(exc),
        }
        save_state(state_path, state)
        raise
    state["assets"][key] = record
    save_state(state_path, state)
    print(f"{key}: wrote {record['output_path']}")
    return record


def _atempo_filter(speed: float) -> str:
    factors = []
    remaining = speed
    while remaining > 2.0:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        factors.append(0.5)
        remaining /= 0.5
    factors.append(remaining)
    return ",".join(f"atempo={factor:.8f}" for factor in factors)


def conform_presenter_driving(
    source: Path,
    target: Path,
    *,
    frame_count: int,
    fps: int,
) -> Path:
    """Extend a short driver with a forward/reverse loop and exact frame count."""
    target.parent.mkdir(parents=True, exist_ok=True)
    cache_path = target.with_suffix(".cache.json")
    cache_key = stable_hash(
        {
            "algorithm": "forward-reverse-loop-v2",
            "source_sha256": sha256_file(source),
            "frame_count": int(frame_count),
            "fps": int(fps),
        }
    )
    if target.exists() and cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if cached.get("cache_key") == cache_key:
            return target
    temporary = target.with_name(target.stem + ".tmp" + target.suffix)
    video_filter = (
        f"[0:v]fps={fps},split=2[forward][backward];"
        "[backward]reverse[reversed];"
        "[forward][reversed]concat=n=2:v=1:a=0,"
        "loop=loop=-1:size=32767:start=0,"
        f"trim=end_frame={frame_count},"
        f"setpts=N/({fps}*TB)[video]"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-filter_complex",
            video_filter,
            "-map",
            "[video]",
            "-frames:v",
            str(frame_count),
            "-an",
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
            str(temporary),
        ],
        check=True,
    )
    temporary.replace(target)
    write_json_atomic(
        cache_path,
        {
            "version": 1,
            "cache_key": cache_key,
            "source": str(source.resolve()),
            "source_sha256": sha256_file(source),
            "frame_count": int(frame_count),
            "fps": int(fps),
        },
    )
    return target


def conform_narration_speed(
    source: Path,
    target: Path,
    words: int,
    target_wpm: float,
) -> dict[str, float]:
    source_duration = ffprobe_duration(source)
    target_duration = words / target_wpm * 60
    speed = source_duration / target_duration
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-af",
            _atempo_filter(speed),
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            str(target),
        ],
        check=True,
    )
    return {
        "source_duration": source_duration,
        "target_duration": target_duration,
        "speed_factor": speed,
    }


def render_narration(
    context: dict[str, Any],
    state: dict[str, Any],
    paths: dict[str, Path],
    args: argparse.Namespace,
) -> dict[str, Any]:
    voice = context["project"]["voice"]
    if voice["mode"] == "supplied":
        record = record_supplied_asset(context["narration_audio"], "narration")
        if not args.dry_run:
            state.setdefault("assets", {})["narration"] = record
            save_state(paths["state"], state)
        print(f"narration: supplied -> {record['output_path']}")
        return record

    text = context["script_file"].read_text(encoding="utf-8").strip()
    output_prefix = f"video_factory/{context['slug']}/narration/narration"
    values = {
        "text": text,
        "speaker": voice.get("speaker", "Ryan"),
        "instruct": voice.get("instruct", ""),
        "language": voice.get("language", "English"),
        "seed": int(voice.get("seed", context["project"].get("seed", 1000))),
        "max_new_tokens": int(voice.get("max_new_tokens", 8192)),
        "output_prefix": output_prefix,
    }

    # Migrate an early factory state that stored the direct adapter output under
    # "narration" before speed conformance became a separate cached stage.
    legacy = state_asset(state, "narration")
    if legacy and legacy.get("adapter") == voice["engine"] and not state_asset(state, "narration-raw"):
        state.setdefault("assets", {})["narration-raw"] = legacy

    raw = render_with_cache(
        state=state,
        state_path=paths["state"],
        key="narration-raw",
        adapter_name=voice["engine"],
        values=values,
        basedir=args.basedir,
        server=args.server,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        timeout_seconds=args.timeout_seconds,
    )
    target_wpm = voice.get("target_words_per_minute")
    if args.dry_run:
        return raw
    if not target_wpm:
        record = {**raw, "kind": "narration"}
        state.setdefault("assets", {})["narration"] = record
        save_state(paths["state"], state)
        print(f"narration: active raw audio -> {record['output_path']}")
        return record

    source = Path(raw["output_path"])
    conformed = paths["root"] / "narration" / "conformed.wav"
    cache_key = stable_hash(
        {
            "source_sha256": sha256_file(source),
            "target_words_per_minute": float(target_wpm),
            "words": word_count(text),
        }
    )
    existing = state_asset(state, "narration")
    if existing and existing.get("cache_key") == cache_key and not args.overwrite:
        print(f"narration: cached conformed audio -> {existing['output_path']}")
        return existing

    metrics = conform_narration_speed(
        source,
        conformed,
        word_count(text),
        float(target_wpm),
    )
    record = {
        "status": "success",
        "kind": "narration",
        "raw_output_path": str(source),
        "output_path": str(conformed.resolve()),
        "cache_key": cache_key,
        **metrics,
    }
    state["assets"]["narration"] = record
    save_state(paths["state"], state)
    print(
        f"narration: conformed {metrics['source_duration']:.1f}s to "
        f"{ffprobe_duration(conformed):.1f}s ({metrics['speed_factor']:.3f}x)"
    )
    return record


def render_presenter_master(
    context: dict[str, Any],
    state: dict[str, Any],
    paths: dict[str, Path],
    args: argparse.Namespace,
) -> dict[str, Any]:
    presenter = context["project"]["presenter"]
    if presenter["mode"] == "supplied":
        record = record_supplied_asset(context["presenter_image"], "presenter-master")
        if not args.dry_run:
            state.setdefault("assets", {})["presenter-master"] = record
            save_state(paths["state"], state)
        print(f"presenter-master: supplied -> {record['output_path']}")
        return record

    profile = context["profile"]
    dimensions = profile.get("generation", {}).get(
        "still_dimensions", {"width": 1344, "height": 768}
    )
    values = {
        "prompt": presenter["image_prompt"],
        "seed": int(presenter.get("seed", context["project"].get("seed", 1000))),
        "width": int(dimensions["width"]),
        "height": int(dimensions["height"]),
        "output_prefix": f"video_factory/{context['slug']}/presenter/master",
    }
    adapter_name = presenter.get(
        "image_engine", context["project"]["engines"]["still"]
    )
    return render_with_cache(
        state=state,
        state_path=paths["state"],
        key="presenter-master",
        adapter_name=adapter_name,
        values=values,
        basedir=args.basedir,
        server=args.server,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        timeout_seconds=args.timeout_seconds,
    )


def narration_path(context: dict[str, Any], state: dict[str, Any]) -> Path | None:
    record = state_asset(state, "narration")
    if record:
        return Path(record["output_path"])
    if context["project"]["voice"]["mode"] == "supplied":
        return context["narration_audio"]
    return None


def presenter_path(context: dict[str, Any], state: dict[str, Any]) -> Path | None:
    record = state_asset(state, "presenter-master")
    if record:
        return Path(record["output_path"])
    if context["project"]["presenter"]["mode"] == "supplied":
        return context["presenter_image"]
    return None


def compile_and_save(
    context: dict[str, Any], paths: dict[str, Path], audio: Path | None
) -> dict[str, Any]:
    manifest = compile_shot_manifest(context, audio_path=audio)
    write_json_atomic(paths["manifest"], manifest)
    write_srt(manifest, paths["captions"])
    return manifest


def _adapter_stage_values(
    adapter_name: str, candidates: dict[str, Any]
) -> dict[str, Any]:
    adapter = load_adapter_definition(adapter_name)
    missing = [
        slot
        for slot in adapter.get("required_slots", [])
        if slot not in candidates
    ]
    if missing:
        raise ValueError(
            f"Presenter stage {adapter_name} has no values for: {', '.join(missing)}"
        )
    return {
        name: value
        for name, value in candidates.items()
        if name in adapter["slots"]
    }


def render_two_pass_presenter(
    *,
    shot: dict[str, Any],
    presenter: Path | None,
    narration: Path | None,
    state: dict[str, Any],
    paths: dict[str, Path],
    args: argparse.Namespace,
    presenter_dimensions: dict[str, int],
    stage_root: Path,
    prefix: str,
) -> None:
    if presenter is None and not args.dry_run:
        raise RuntimeError("Presenter pipeline requires a master image")
    if narration is None and not args.dry_run:
        raise RuntimeError("Presenter pipeline requires narration")

    pipeline = shot["presenter_pipeline"]
    stage_fps = int(pipeline["fps"])
    render_duration = max(4.0, float(shot["duration"]))
    frame_count = max(1, math.ceil(render_duration * stage_fps))
    driving = Path(pipeline["driving_asset"])
    driving_capacity = int(pipeline.get("driving_frame_capacity", frame_count))
    needs_extended_driving = frame_count > driving_capacity
    audio_target = (
        args.basedir
        / "input"
        / stage_root
        / "presenter_audio"
        / f"{shot['shot_id']}.wav"
    )

    if args.dry_run:
        image_name = (stage_root / "presenter" / "master.png").as_posix()
        driving_filename = (
            f"{shot['shot_id']}-forward-reverse.mp4"
            if needs_extended_driving
            else "subtle.mp4"
        )
        driving_name = (
            stage_root / "presenter_driving" / driving_filename
        ).as_posix()
        audio_name = (
            stage_root / "presenter_audio" / f"{shot['shot_id']}.wav"
        ).as_posix()
        motion_inputs: list[Path] = []
    else:
        if not driving.exists():
            raise FileNotFoundError(f"Presenter driving video not found: {driving}")
        image_name = stage_input(
            presenter,
            args.basedir,
            (stage_root / "presenter" / f"master{presenter.suffix}").as_posix(),
        )
        if needs_extended_driving:
            staged_driving = conform_presenter_driving(
                driving,
                args.basedir
                / "input"
                / stage_root
                / "presenter_driving"
                / f"{shot['shot_id']}-forward-reverse.mp4",
                frame_count=frame_count,
                fps=stage_fps,
            )
            driving_name = staged_driving.relative_to(
                args.basedir / "input"
            ).as_posix()
        else:
            driving_name = stage_input(
                driving,
                args.basedir,
                (stage_root / "presenter_driving" / driving.name).as_posix(),
            )
            staged_driving = args.basedir / "input" / driving_name
        extract_audio_segment(
            narration,
            audio_target,
            shot["start"],
            shot["duration"],
            minimum_duration=render_duration,
        )
        audio_name = audio_target.relative_to(args.basedir / "input").as_posix()
        motion_inputs = [presenter, staged_driving]

    common = {
        "prompt": shot["prompt"],
        "seed": shot["seed"],
        "duration": render_duration,
        "frame_count": frame_count,
        "fps": stage_fps,
        "input_fps": stage_fps,
        "output_fps": stage_fps,
        "width": int(presenter_dimensions["width"]),
        "height": int(presenter_dimensions["height"]),
    }
    motion_engine = pipeline["motion_engine"]
    motion_values = _adapter_stage_values(
        motion_engine,
        {
            **common,
            "image": image_name,
            "driving_video": driving_name,
            "output_prefix": f"{prefix}-motion",
        },
    )
    motion = render_with_cache(
        state=state,
        state_path=paths["state"],
        key=f"shot:{shot['shot_id']}:motion",
        adapter_name=motion_engine,
        values=motion_values,
        basedir=args.basedir,
        server=args.server,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        input_paths=motion_inputs,
        timeout_seconds=args.timeout_seconds,
    )

    if args.dry_run:
        motion_name = (
            stage_root / "presenter_motion" / f"{shot['shot_id']}.mp4"
        ).as_posix()
        lip_inputs: list[Path] = []
    else:
        motion_path = Path(motion["output_path"])
        motion_name = stage_input(
            motion_path,
            args.basedir,
            (stage_root / "presenter_motion" / motion_path.name).as_posix(),
        )
        lip_inputs = [motion_path, audio_target]

    lip_engine = pipeline["lip_sync_engine"]
    lip_values = _adapter_stage_values(
        lip_engine,
        {
            **common,
            "video": motion_name,
            "audio": audio_name,
            "output_prefix": prefix,
        },
    )
    if lip_engine == LATENTSYNC_ENGINE:
        if args.dry_run:
            print(f"shot:{shot['shot_id']}: would render with {LATENTSYNC_ENGINE}")
            return
        render_latentsync_with_cache(
            state=state,
            state_path=paths["state"],
            key=f"shot:{shot['shot_id']}",
            source_video=motion_path,
            audio=audio_target,
            output=(
                paths["root"]
                / "shots"
                / f"{shot['shot_id']}-latentsync16-mouthrestore.mp4"
            ),
            seed=int(shot["seed"]),
            frame_count=frame_count,
            fps=stage_fps,
            options=pipeline.get("lip_sync_options", {}),
            args=args,
        )
        return
    render_with_cache(
        state=state,
        state_path=paths["state"],
        key=f"shot:{shot['shot_id']}",
        adapter_name=lip_engine,
        values=lip_values,
        basedir=args.basedir,
        server=args.server,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        input_paths=lip_inputs,
        timeout_seconds=args.timeout_seconds,
    )


def render_shots(
    context: dict[str, Any],
    manifest: dict[str, Any],
    state: dict[str, Any],
    paths: dict[str, Path],
    args: argparse.Namespace,
    shot_type: str | None = None,
) -> None:
    narration = narration_path(context, state)
    presenter = presenter_path(context, state)
    selected = manifest["shots"]
    if shot_type:
        selected = [shot for shot in selected if shot["type"] == shot_type]
    if args.shot_id:
        selected = [shot for shot in selected if shot["shot_id"] == args.shot_id]
    if args.limit is not None:
        selected = selected[: args.limit]

    if any(shot["type"] == "presenter" for shot in selected):
        if narration is None and not args.dry_run:
            raise RuntimeError("Presenter shots require rendered or supplied narration")
        if presenter is None and not args.dry_run:
            raise RuntimeError("Presenter shots require a rendered or supplied master image")
    needs_latentsync = any(
        shot.get("presenter_pipeline", {}).get("lip_sync_engine")
        == LATENTSYNC_ENGINE
        for shot in selected
    )
    if needs_latentsync and not args.dry_run:
        runtime = validate_latentsync_runtime(
            getattr(args, "runtime_container", default_runtime_container()),
            getattr(args, "runtime_user", default_runtime_user()),
            min(args.timeout_seconds, 300),
        )
        print(
            "latentsync16: runtime verified -> "
            f"torch {runtime['torch']}, CUDA {runtime['cuda']}"
        )

    profile = context["profile"]
    still_dimensions = profile.get("generation", {}).get(
        "still_dimensions", {"width": 1344, "height": 768}
    )
    video_dimensions = profile.get("generation", {}).get(
        "video_dimensions", {"width": 1280, "height": 736}
    )
    presenter_dimensions = profile.get("generation", {}).get(
        "presenter_dimensions", {"width": 1024, "height": 576}
    )
    stage_root = Path("video_factory") / context["slug"]

    for shot in selected:
        key = f"shot:{shot['shot_id']}"
        prefix = f"video_factory/{context['slug']}/shots/{shot['shot_id']}"
        input_paths: list[Path] = []
        if source_asset := shot.get("source_asset"):
            source_path = Path(source_asset)
            if args.dry_run:
                print(f"{key}: would use local asset -> {source_path}")
                continue
            if not source_path.exists():
                raise FileNotFoundError(f"Local shot asset not found: {source_path}")
            record = record_supplied_asset(
                source_path,
                "shot",
                {
                    "asset_ref": shot.get("asset_ref"),
                    "source_start": float(shot.get("source_start", 0)),
                    "source_license": shot.get("source_license", "unspecified"),
                    "source_url": shot.get("source_url"),
                    "source_expected_sha256": shot.get("source_expected_sha256"),
                },
            )
            state.setdefault("assets", {})[key] = record
            save_state(paths["state"], state)
            print(f"{key}: local asset -> {record['output_path']}")
            continue
        if shot["type"] == "presenter" and shot.get("presenter_pipeline"):
            render_two_pass_presenter(
                shot=shot,
                presenter=presenter,
                narration=narration,
                state=state,
                paths=paths,
                args=args,
                presenter_dimensions=presenter_dimensions,
                stage_root=stage_root,
                prefix=prefix,
            )
            continue
        if shot["type"] == "still":
            values = {
                "prompt": shot["prompt"],
                "seed": shot["seed"],
                "width": int(still_dimensions["width"]),
                "height": int(still_dimensions["height"]),
                "output_prefix": prefix,
            }
        elif shot["type"] == "broll":
            render_duration = max(3, min(10, int(math.ceil(shot["duration"]))))
            values = {
                "prompt": shot["prompt"],
                "seed": shot["seed"],
                "duration": render_duration,
                "width": int(video_dimensions["width"]),
                "height": int(video_dimensions["height"]),
                "output_prefix": prefix,
            }
        else:
            render_duration = max(4.0, float(shot["duration"]))
            if args.dry_run:
                image_name = (stage_root / "presenter" / "master.png").as_posix()
                audio_name = (stage_root / "presenter_audio" / f"{shot['shot_id']}.wav").as_posix()
            else:
                staged_image = stage_input(
                    presenter,
                    args.basedir,
                    (stage_root / "presenter" / f"master{presenter.suffix}").as_posix(),
                )
                audio_target = (
                    args.basedir
                    / "input"
                    / stage_root
                    / "presenter_audio"
                    / f"{shot['shot_id']}.wav"
                )
                extract_audio_segment(
                    narration,
                    audio_target,
                    shot["start"],
                    shot["duration"],
                    minimum_duration=render_duration,
                )
                image_name = staged_image
                audio_name = audio_target.relative_to(args.basedir / "input").as_posix()
                input_paths = [presenter, audio_target]
            values = {
                "prompt": shot["prompt"],
                "seed": shot["seed"],
                "duration": render_duration,
                "image": image_name,
                "audio": audio_name,
                "output_prefix": prefix,
            }
            adapter, _ = load_adapter(shot["engine"])
            if "width" in adapter["slots"]:
                values["width"] = int(presenter_dimensions["width"])
            if "height" in adapter["slots"]:
                values["height"] = int(presenter_dimensions["height"])

        values = _adapter_stage_values(shot["engine"], values)
        render_with_cache(
            state=state,
            state_path=paths["state"],
            key=key,
            adapter_name=shot["engine"],
            values=values,
            basedir=args.basedir,
            server=args.server,
            dry_run=args.dry_run,
            overwrite=args.overwrite,
            input_paths=input_paths,
            timeout_seconds=args.timeout_seconds,
        )


def sync_manifest_assets(manifest: dict[str, Any], state: dict[str, Any]) -> None:
    for shot in manifest["shots"]:
        record = state_asset(state, f"shot:{shot['shot_id']}")
        if record:
            shot["asset"] = record["output_path"]
            shot["status"] = record.get("status", "success")
            if record.get("qa"):
                shot["presenter_qa"] = record["qa"].get("status", "pending")


def build_presenter_qa_report(
    context: dict[str, Any], manifest: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    shots = {}
    assets = state.get("assets", {})
    for shot in manifest["shots"]:
        if shot.get("type") != "presenter":
            continue
        qa = assets.get(f"shot:{shot['shot_id']}", {}).get("qa")
        if isinstance(qa, dict):
            shots[shot["shot_id"]] = qa
    return {"version": 1, "project": context["slug"], "shots": shots}


def presenter_qa_failures(
    context: dict[str, Any], manifest: dict[str, Any], state: dict[str, Any]
) -> list[str]:
    qa_config = context.get("project", {}).get("presenter", {}).get("qa", {})
    policy = build_presenter_qa_policy(
        sample_fps=int(qa_config.get("sample_fps", 12)),
        minimum_mean=float(qa_config.get("minimum_mouth_motion_mean", 5.0)),
        minimum_p95=float(qa_config.get("minimum_mouth_motion_p95", 9.0)),
    )
    expected_policy_fingerprint = presenter_qa_policy_fingerprint(policy)
    failures = []
    for shot in manifest["shots"]:
        if shot.get("type") != "presenter":
            continue
        record = state_asset(state, f"shot:{shot['shot_id']}")
        qa = record.get("qa") if record else None
        if not qa:
            failures.append(f"{shot['shot_id']} (QA missing)")
            continue
        output_path = Path(record["output_path"])
        if (
            qa.get("source_sha256") != sha256_file(output_path)
            or qa.get("policy_fingerprint") != expected_policy_fingerprint
        ):
            failures.append(f"{shot['shot_id']} (QA stale)")
        elif qa.get("status") != "pass":
            failures.append(f"{shot['shot_id']} (QA {qa.get('status', 'unknown')})")
        else:
            automatic_status = (qa.get("automatic_visible_motion_screen") or {}).get(
                "status"
            )
            manual_review = qa.get("manual_review") or {}
            criteria = manual_review.get("criteria") or {}
            if (
                automatic_status != "pass"
                or manual_review.get("status") != "pass"
                or not all(
                    criteria.get(criterion) == "pass"
                    for criterion in MANUAL_CRITERIA
                )
            ):
                failures.append(f"{shot['shot_id']} (QA incomplete)")
    return failures


def _delivery_settings(delivery: dict[str, Any]) -> tuple[str, str, float]:
    codec_name = str(delivery.get("video_codec") or "h264").strip()
    video_codec = {
        "avc": "libx264",
        "h264": "libx264",
        "h265": "libx265",
        "hevc": "libx265",
    }.get(codec_name.lower(), codec_name)
    pixel_format = str(delivery.get("pixel_format") or "yuv420p").strip()
    configured_audio_lufs = delivery.get("audio_lufs")
    audio_lufs = float(-16 if configured_audio_lufs is None else configured_audio_lufs)
    if not math.isfinite(audio_lufs):
        raise ValueError("delivery.audio_lufs must be finite")
    return video_codec, pixel_format, audio_lufs


def _video_encoding_args(video_codec: str, pixel_format: str) -> list[str]:
    args = ["-c:v", video_codec]
    if video_codec in {"libx264", "libx265"}:
        args.extend(["-preset", "medium", "-crf", "18"])
    args.extend(["-pix_fmt", pixel_format])
    return args


def _pre_grade_filters(
    settings: dict[str, Any], width: int, height: int, fps: int
) -> list[str]:
    filters = []
    if source_crop := settings.get("source_crop"):
        filters.append(
            f"crop={int(source_crop['width'])}:{int(source_crop['height'])}:"
            f"{int(source_crop['x'])}:{int(source_crop['y'])}"
        )
    filters.extend(
        [
            f"scale={width}:{height}:force_original_aspect_ratio=increase",
            f"crop={width}:{height}",
        ]
    )
    fps_method = settings.get("fps_method", "duplicate")
    if fps_method == "blend":
        filters.append(f"minterpolate=fps={fps}:mi_mode=blend")
    elif fps_method == "duplicate":
        filters.append(f"fps={fps}")
    else:
        raise ValueError(f"Unsupported normalization fps_method: {fps_method}")

    denoise = float(settings.get("denoise", 0))
    if denoise > 0:
        filters.append(f"hqdn3d={denoise:g}:{denoise:g}:{denoise * 1.5:g}:{denoise * 1.5:g}")
    sharpen = float(settings.get("sharpen", 0))
    if sharpen:
        filters.append(f"unsharp=5:5:{sharpen:g}:5:5:0")
    contrast = float(settings.get("contrast", 1))
    brightness = float(settings.get("brightness", 0))
    saturation = float(settings.get("saturation", 1))
    gamma = float(settings.get("gamma", 1))
    if (contrast, brightness, saturation, gamma) != (1.0, 0.0, 1.0, 1.0):
        filters.append(
            f"eq=contrast={contrast:g}:brightness={brightness:g}:"
            f"saturation={saturation:g}:gamma={gamma:g}"
        )
    blue_shadows = float(settings.get("blue_shadows", 0))
    blue_midtones = float(settings.get("blue_midtones", 0))
    blue_highlights = float(settings.get("blue_highlights", 0))
    if (blue_shadows, blue_midtones, blue_highlights) != (0.0, 0.0, 0.0):
        filters.append(
            f"colorbalance=bs={blue_shadows:g}:bm={blue_midtones:g}:"
            f"bh={blue_highlights:g}"
        )
    return filters


def _final_grade_filters(settings: dict[str, Any], pixel_format: str) -> list[str]:
    filters = []
    if lut_path := settings.get("lut_path"):
        escaped = str(lut_path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        filters.append(f"lut3d=file='{escaped}'")
    warmth = float(settings.get("warmth", 0))
    if warmth:
        filters.append(f"colorbalance=rs={warmth:g}:bs={-warmth:g}")
    grain = float(settings.get("grain", 0))
    if grain > 0:
        filters.append(f"noise=alls={grain:g}:allf=t+u")
    if filters:
        filters.append(f"format={pixel_format}")
    return filters


def create_assembly_clip(
    asset: Path,
    target: Path,
    duration: float,
    width: int,
    height: int,
    fps: int,
    video_codec: str = "libx264",
    pixel_format: str = "yuv420p",
    pre_grade: dict[str, Any] | None = None,
    source_start: float = 0,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    settings = pre_grade or {}
    is_still = asset.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    if is_still:
        frames = max(1, round(duration * fps))
        pre_grade_filters = _pre_grade_filters(settings, width, height, fps)
        geometry_count = 4 if settings.get("source_crop") else 3
        grade_filters = pre_grade_filters[geometry_count:]
        source_crop_filters = pre_grade_filters[:1] if settings.get("source_crop") else []
        video_filter = ",".join(
            [
                *source_crop_filters,
                f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase",
                f"crop={width * 2}:{height * 2}",
                "zoompan=z='min(zoom+0.00035,1.05)':"
                "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"d={frames}:s={width}x{height}:fps={fps}",
                *grade_filters,
                f"format={pixel_format}",
            ]
        )
        command = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-loop",
            "1",
            "-i",
            str(asset),
            "-frames:v",
            str(frames),
            "-vf",
            video_filter,
        ]
    else:
        video_filter = ",".join(
            [
                *_pre_grade_filters(settings, width, height, fps),
                f"tpad=stop_mode=clone:stop_duration={duration:.3f}",
                f"format={pixel_format}",
            ]
        )
        command = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
        ]
        if source_start > 0:
            command.extend(["-ss", f"{source_start:.3f}"])
        command.extend(
            [
                "-i",
                str(asset),
                "-t",
                f"{duration:.3f}",
                "-an",
                "-vf",
                video_filter,
            ]
        )
    command.extend(_video_encoding_args(video_codec, pixel_format))
    command.extend(["-movflags", "+faststart", str(target)])
    subprocess.run(command, check=True)


def assemble_project(
    context: dict[str, Any],
    manifest: dict[str, Any],
    state: dict[str, Any],
    paths: dict[str, Path],
    overwrite: bool,
) -> Path:
    narration = narration_path(context, state)
    if narration is None:
        raise RuntimeError("Assembly requires rendered or supplied narration")
    sync_manifest_assets(manifest, state)
    missing = [shot["shot_id"] for shot in manifest["shots"] if not shot.get("asset")]
    if missing:
        raise RuntimeError(f"Assembly is missing {len(missing)} shot assets: {', '.join(missing[:10])}")
    qa_failures = presenter_qa_failures(context, manifest, state)
    if qa_failures:
        raise RuntimeError(
            "Assembly requires passing presenter lip-sync QA: "
            + ", ".join(qa_failures[:10])
        )

    delivery = manifest["delivery"]
    width = int(delivery["width"])
    height = int(delivery["height"])
    fps = int(delivery["fps"])
    video_codec, pixel_format, audio_lufs = _delivery_settings(delivery)
    normalization = manifest.get("normalization", {})
    global_pre_grade = normalization.get("pre_grade", {})
    final_grade = normalization.get("final_grade", {})
    paths["clips"].mkdir(parents=True, exist_ok=True)
    clips = []
    for shot in manifest["shots"]:
        clip = paths["clips"] / f"{shot['index']:04d}-{shot['shot_id']}.mp4"
        asset = Path(shot["asset"])
        source_sha256 = _verify_expected_sha256(
            asset,
            shot.get("source_expected_sha256"),
            f"Shot {shot['shot_id']} asset {asset}",
        )
        cache_record_path = clip.with_suffix(".cache.json")
        shot_pre_grade = {**global_pre_grade, **shot.get("pre_grade", {})}
        duration = (
            int(shot["frame_count"]) / fps
            if "frame_count" in shot
            else float(shot["duration"])
        )
        cache_key = stable_hash(
            {
                "source_sha256": source_sha256,
                "source_start": float(shot.get("source_start", 0)),
                "duration": duration,
                "width": width,
                "height": height,
                "fps": fps,
                "video_codec": video_codec,
                "pixel_format": pixel_format,
                "pre_grade": shot_pre_grade,
            }
        )
        cache_record = None
        if cache_record_path.exists():
            try:
                cache_record = json.loads(cache_record_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cache_record = None
        if (
            overwrite
            or not clip.exists()
            or not cache_record
            or cache_record.get("cache_key") != cache_key
        ):
            print(f"assembly: preparing {shot['shot_id']}")
            temporary_clip = clip.with_name(f".{clip.stem}.tmp{clip.suffix}")
            try:
                create_assembly_clip(
                    asset,
                    temporary_clip,
                    duration,
                    width,
                    height,
                    fps,
                    video_codec,
                    pixel_format,
                    shot_pre_grade,
                    float(shot.get("source_start", 0)),
                )
                temporary_clip.replace(clip)
            finally:
                temporary_clip.unlink(missing_ok=True)
            write_json_atomic(
                cache_record_path,
                {"version": 1, "cache_key": cache_key},
            )
        clips.append(clip)

    concat_file = paths["clips"].parent / "concat.txt"
    concat_file.write_text(
        "".join(f"file '{clip.as_posix()}'\n" for clip in clips),
        encoding="utf-8",
    )
    silent_video = paths["clips"].parent / "silent-video.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c",
            "copy",
            str(silent_video),
        ],
        check=True,
    )
    final_command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(silent_video),
        "-i",
        str(narration),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
    ]
    if grade_filters := _final_grade_filters(final_grade, pixel_format):
        final_command.extend(["-vf", ",".join(grade_filters)])
    final_command.extend(_video_encoding_args(video_codec, pixel_format))
    final_command.extend(
        [
            "-af",
            f"loudnorm=I={audio_lufs:g}:TP=-1.5:LRA=11,apad",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-frames:v",
            str(round(float(manifest["duration"]) * fps)),
            "-t",
            f"{manifest['duration']:.3f}",
            "-movflags",
            "+faststart",
            str(paths["delivery"]),
        ]
    )
    subprocess.run(final_command, check=True)
    write_json_atomic(paths["manifest"], manifest)
    print(f"Delivery: {paths['delivery']}")
    return paths["delivery"]


def command_validate(args: argparse.Namespace) -> int:
    context = load_project(args.project)
    bind_project_assets(context, args.basedir)
    missing_assets = missing_project_assets(context)
    if missing_assets:
        raise FileNotFoundError(
            "Required project assets are missing:\n- " + "\n- ".join(missing_assets)
        )
    print(f"Project: {context['slug']}")
    print(f"Profile: {context['profile']['name']}")
    print(f"Script: {context['script_file']}")
    print("Validation: OK")
    return 0


def command_compile(args: argparse.Namespace) -> int:
    context = load_project(args.project)
    bind_project_assets(context, args.basedir)
    paths = runtime_paths(context, args.basedir)
    state = load_state(paths["state"])
    audio = Path(args.audio).resolve() if args.audio else narration_path(context, state)
    manifest = compile_and_save(context, paths, audio)
    print(f"Project: {context['slug']}")
    print(f"Shots: {len(manifest['shots'])}")
    print(f"Duration: {manifest['duration']:.1f}s ({manifest['timing_source']})")
    print(f"Manifest: {paths['manifest']}")
    print(f"Captions: {paths['captions']}")
    return 0


def command_render(args: argparse.Namespace) -> int:
    context = load_project(args.project)
    bind_project_assets(context, args.basedir)
    paths = runtime_paths(context, args.basedir)
    state = load_state(paths["state"])
    paths["root"].mkdir(parents=True, exist_ok=True)

    modules = [args.module] if args.module != "all" else ["narration", "presenter-master", "shots"]
    if "narration" in modules:
        render_narration(context, state, paths, args)
    if "presenter-master" in modules:
        render_presenter_master(context, state, paths, args)

    audio = narration_path(context, state)
    if args.dry_run and audio is None:
        audio = None
    manifest = compile_and_save(context, paths, audio)

    if "shots" in modules:
        render_shots(context, manifest, state, paths, args)
    elif args.module in {"presenter", "broll", "still"}:
        render_shots(context, manifest, state, paths, args, shot_type=args.module)

    if not args.dry_run:
        sync_manifest_assets(manifest, state)
        write_json_atomic(paths["manifest"], manifest)
    return 0


def command_assemble(args: argparse.Namespace) -> int:
    context = load_project(args.project)
    bind_project_assets(context, args.basedir)
    paths = runtime_paths(context, args.basedir)
    if not paths["manifest"].exists():
        raise FileNotFoundError(f"Compile the project first: {paths['manifest']}")
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    state = load_state(paths["state"])
    assemble_project(context, manifest, state, paths, args.overwrite)
    return 0


def command_qa_presenter(args: argparse.Namespace) -> int:
    context = load_project(args.project)
    bind_project_assets(context, args.basedir)
    paths = runtime_paths(context, args.basedir)
    if not paths["manifest"].exists():
        raise FileNotFoundError(f"Compile the project first: {paths['manifest']}")
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    state = load_state(paths["state"])
    selected = [shot for shot in manifest["shots"] if shot.get("type") == "presenter"]
    if args.shot_id:
        selected = [shot for shot in selected if shot["shot_id"] == args.shot_id]
    if not selected:
        raise ValueError("No matching rendered presenter shots")

    supplied_manual = {
        "visible_articulation": args.visible_articulation,
        "lip_sync": args.lip_sync,
        "identity_stability": args.identity_stability,
        "temporal_stability": args.temporal_stability,
        "beard_teeth_stability": args.beard_teeth_stability,
        "blend_seam_free": args.blend_seam_free,
        "text_artifact_free": args.text_artifact_free,
    }
    if any(supplied_manual.values()) and len(selected) != 1:
        raise ValueError("Manual QA flags require --shot-id so one shot is reviewed at a time")

    qa_config = context["project"]["presenter"].get("qa", {})
    policy = build_presenter_qa_policy(
        sample_fps=int(qa_config.get("sample_fps", 12)),
        minimum_mean=float(qa_config.get("minimum_mouth_motion_mean", 5.0)),
        minimum_p95=float(qa_config.get("minimum_mouth_motion_p95", 9.0)),
    )
    evaluated_qa = []
    for shot in selected:
        key = f"shot:{shot['shot_id']}"
        record = state_asset(state, key)
        if not record:
            raise FileNotFoundError(f"Presenter shot is not rendered: {shot['shot_id']}")
        video = Path(record["output_path"])
        analysis = analyze_presenter_video(
            video,
            roi=policy["mouth_roi"],
            sample_fps=policy["sample_fps"],
        )
        existing_qa = record.get("qa", {})
        existing_review = (
            existing_qa.get("manual_review", {}).get("criteria", {})
            if existing_qa.get("source_sha256") == analysis["video_sha256"]
            else {}
        )
        manual_review = {
            criterion: supplied_manual.get(criterion) or existing_review.get(criterion)
            for criterion in MANUAL_CRITERIA
        }
        existing_notes = existing_qa.get("manual_review", {}).get("notes", "")
        qa = evaluate_presenter_qa(
            analysis,
            minimum_mean=policy["minimum_mouth_motion_mean"],
            minimum_p95=policy["minimum_mouth_motion_p95"],
            manual_review=manual_review,
            notes=args.notes if args.notes is not None else existing_notes,
        )
        qa["contact_sheets"] = write_contact_sheets(
            video,
            paths["presenter_qa_media"] / shot["shot_id"],
        )
        record["qa"] = qa
        evaluated_qa.append(qa)
        motion = qa["analysis"]["motion"]
        print(
            f"{shot['shot_id']}: {qa['status']} "
            f"(mouth mean={motion['mean_absolute_luma_delta']:.2f}, "
            f"p95={motion['p95_absolute_luma_delta']:.2f})"
        )

    save_state(paths["state"], state)
    report = build_presenter_qa_report(context, manifest, state)
    write_json_atomic(paths["presenter_qa"], report)
    sync_manifest_assets(manifest, state)
    write_json_atomic(paths["manifest"], manifest)
    print(f"Presenter QA: {paths['presenter_qa']}")
    if args.require_pass and any(qa["status"] != "pass" for qa in evaluated_qa):
        return 2
    return 0


def command_status(args: argparse.Namespace) -> int:
    context = load_project(args.project)
    bind_project_assets(context, args.basedir)
    paths = runtime_paths(context, args.basedir)
    state = load_state(paths["state"])
    manifest = (
        json.loads(paths["manifest"].read_text(encoding="utf-8"))
        if paths["manifest"].exists()
        else compile_shot_manifest(context)
    )
    counts: dict[str, int] = {}
    for record in state.get("assets", {}).values():
        status = record.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    rendered = sum(
        state_asset(state, f"shot:{shot['shot_id']}") is not None
        for shot in manifest["shots"]
    )
    print(f"Project: {context['slug']}")
    print(f"Runtime: {paths['root']}")
    print(f"Shots rendered: {rendered}/{len(manifest['shots'])}")
    print(f"State records: {counts or {'none': 0}}")
    if paths["delivery"].exists():
        print(f"Delivery: {paths['delivery']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compile and render modular local videos through ComfyUI workflows."
    )
    parser.add_argument(
        "--basedir", type=Path, default=DEFAULT_BASEDIR, help="ComfyUI basedir"
    )
    parser.add_argument("--server", default=DEFAULT_SERVER)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="Validate project configuration")
    validate.add_argument("project")
    validate.set_defaults(func=command_validate)

    compile_parser = subparsers.add_parser("compile", help="Compile the timed shot manifest")
    compile_parser.add_argument("project")
    compile_parser.add_argument("--audio", help="Override narration audio for exact total duration")
    compile_parser.set_defaults(func=command_compile)

    render = subparsers.add_parser("render", help="Render project modules through ComfyUI")
    render.add_argument("project")
    render.add_argument(
        "--module",
        choices=["all", "narration", "presenter-master", "shots", "presenter", "broll", "still"],
        default="all",
    )
    render.add_argument("--limit", type=int)
    render.add_argument("--shot-id")
    render.add_argument("--overwrite", action="store_true")
    render.add_argument("--dry-run", action="store_true")
    render.add_argument("--timeout-seconds", type=float, default=1800)
    render.add_argument(
        "--runtime-container",
        default=default_runtime_container(),
        help="Container for external pinned presenter stages",
    )
    render.add_argument(
        "--runtime-user",
        default=default_runtime_user(),
        help="UID:GID used for external container writes",
    )
    render.set_defaults(func=command_render)

    assemble = subparsers.add_parser("assemble", help="Assemble rendered assets with FFmpeg")
    assemble.add_argument("project")
    assemble.add_argument("--overwrite", action="store_true")
    assemble.set_defaults(func=command_assemble)

    qa_presenter = subparsers.add_parser(
        "qa-presenter", help="Screen presenter mouth motion and record human review"
    )
    qa_presenter.add_argument("project")
    qa_presenter.add_argument("--shot-id")
    qa_presenter.add_argument("--visible-articulation", choices=["pass", "fail"])
    qa_presenter.add_argument("--lip-sync", choices=["pass", "fail"])
    qa_presenter.add_argument("--identity-stability", choices=["pass", "fail"])
    qa_presenter.add_argument("--temporal-stability", choices=["pass", "fail"])
    qa_presenter.add_argument("--beard-teeth-stability", choices=["pass", "fail"])
    qa_presenter.add_argument("--blend-seam-free", choices=["pass", "fail"])
    qa_presenter.add_argument("--text-artifact-free", choices=["pass", "fail"])
    qa_presenter.add_argument("--notes")
    qa_presenter.add_argument("--require-pass", action="store_true")
    qa_presenter.set_defaults(func=command_qa_presenter)

    status = subparsers.add_parser("status", help="Show resumable project state")
    status.add_argument("project")
    status.set_defaults(func=command_status)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.basedir = args.basedir.expanduser().resolve()
    try:
        return args.func(args)
    except Exception as exc:
        parser.exit(1, f"error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
