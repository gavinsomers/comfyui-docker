#!/usr/bin/env python3
"""CLI for the reusable, manifest-driven local ComfyUI video factory."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from video_factory.core import (  # noqa: E402
    DEFAULT_BASEDIR,
    DEFAULT_SERVER,
    compile_shot_manifest,
    extract_audio_segment,
    ffprobe_duration,
    load_project,
    load_state,
    render_adapter,
    save_state,
    sha256_file,
    stable_hash,
    stage_input,
    word_count,
    write_json_atomic,
    write_srt,
)


def runtime_paths(context: dict[str, Any], basedir: Path) -> dict[str, Path]:
    root = basedir / "output" / "video_factory" / context["slug"]
    return {
        "root": root,
        "manifest": root / "shot-manifest.json",
        "state": root / "state.json",
        "captions": root / "captions.srt",
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


def record_supplied_asset(path: Path, kind: str) -> dict[str, Any]:
    return {
        "status": "supplied",
        "kind": kind,
        "output_path": str(path.resolve()),
        "cache_key": stable_hash({"kind": kind, "sha256": sha256_file(path)}),
    }


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
    if not target_wpm or args.dry_run:
        return raw

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

    profile = context["profile"]
    still_dimensions = profile.get("generation", {}).get(
        "still_dimensions", {"width": 1344, "height": 768}
    )
    video_dimensions = profile.get("generation", {}).get(
        "video_dimensions", {"width": 1280, "height": 736}
    )
    stage_root = Path("video_factory") / context["slug"]

    for shot in selected:
        key = f"shot:{shot['shot_id']}"
        prefix = f"video_factory/{context['slug']}/shots/{shot['shot_id']}"
        input_paths: list[Path] = []
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


def create_assembly_clip(
    asset: Path,
    target: Path,
    duration: float,
    width: int,
    height: int,
    fps: int,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    common_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},fps={fps},"
        f"tpad=stop_mode=clone:stop_duration={duration:.3f},format=yuv420p"
    )
    if asset.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        frames = max(1, math.ceil(duration * fps))
        video_filter = (
            f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
            f"crop={width * 2}:{height * 2},"
            f"zoompan=z='min(zoom+0.00035,1.05)':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={width}x{height}:fps={fps},format=yuv420p"
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
            "-t",
            f"{duration:.3f}",
            "-vf",
            video_filter,
        ]
    else:
        command = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(asset),
            "-t",
            f"{duration:.3f}",
            "-an",
            "-vf",
            common_filter,
        ]
    command.extend(
        [
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
        ]
    )
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

    delivery = manifest["delivery"]
    width = int(delivery["width"])
    height = int(delivery["height"])
    fps = int(delivery["fps"])
    paths["clips"].mkdir(parents=True, exist_ok=True)
    clips = []
    for shot in manifest["shots"]:
        clip = paths["clips"] / f"{shot['index']:04d}-{shot['shot_id']}.mp4"
        if overwrite or not clip.exists():
            print(f"assembly: preparing {shot['shot_id']}")
            create_assembly_clip(
                Path(shot["asset"]),
                clip,
                float(shot["duration"]),
                width,
                height,
                fps,
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
    subprocess.run(
        [
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
            "-c:v",
            "copy",
            "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=11",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-t",
            f"{manifest['duration']:.3f}",
            "-movflags",
            "+faststart",
            str(paths["delivery"]),
        ],
        check=True,
    )
    write_json_atomic(paths["manifest"], manifest)
    print(f"Delivery: {paths['delivery']}")
    return paths["delivery"]


def command_validate(args: argparse.Namespace) -> int:
    context = load_project(args.project)
    print(f"Project: {context['slug']}")
    print(f"Profile: {context['profile']['name']}")
    print(f"Script: {context['script_file']}")
    print("Validation: OK")
    return 0


def command_compile(args: argparse.Namespace) -> int:
    context = load_project(args.project)
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
    paths = runtime_paths(context, args.basedir)
    if not paths["manifest"].exists():
        raise FileNotFoundError(f"Compile the project first: {paths['manifest']}")
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    state = load_state(paths["state"])
    assemble_project(context, manifest, state, paths, args.overwrite)
    return 0


def command_status(args: argparse.Namespace) -> int:
    context = load_project(args.project)
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
    render.set_defaults(func=command_render)

    assemble = subparsers.add_parser("assemble", help="Assemble rendered assets with FFmpeg")
    assemble.add_argument("project")
    assemble.add_argument("--overwrite", action="store_true")
    assemble.set_defaults(func=command_assemble)

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
