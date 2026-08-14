#!/usr/bin/env python3
"""Core primitives for the manifest-driven ComfyUI video factory."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover - dependency is present on the target host
    raise RuntimeError("PyYAML is required to use video_factory") from exc

try:
    import jsonschema
except ImportError:  # pragma: no cover - validation has a clear manual fallback
    jsonschema = None


FACTORY_ROOT = Path(__file__).resolve().parent
REPO_ROOT = FACTORY_ROOT.parent
DEFAULT_BASEDIR = REPO_ROOT / "basedir"
DEFAULT_SERVER = "http://127.0.0.1:8188"
VALID_SHOT_TYPES = {"presenter", "broll", "still"}
VALID_PRE_GRADE_KEYS = {
    "fps_method",
    "denoise",
    "sharpen",
    "contrast",
    "brightness",
    "saturation",
    "gamma",
    "blue_shadows",
    "blue_midtones",
    "blue_highlights",
    "source_crop",
}
VALID_LIP_SYNC_OPTION_KEYS = {
    "mask",
    "inference_steps",
    "guidance_scale",
    "enable_deepcache",
    "pad_x",
    "pad_y",
    "minimum_radius_x",
    "minimum_radius_y",
    "feather_sigma",
}


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = value.strip("-._")
    return value or time.strftime("video-%Y%m%d-%H%M%S")


def load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _resolve_profile(project_dir: Path, profile_value: str) -> Path:
    candidate = Path(profile_value)
    if candidate.suffix or candidate.parent != Path("."):
        candidate = candidate if candidate.is_absolute() else project_dir / candidate
    else:
        candidate = FACTORY_ROOT / "profiles" / f"{profile_value}.yaml"
    return candidate.resolve()


def resolve_project_path(context: dict[str, Any], value: str | None) -> Path | None:
    if not value:
        return None
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = context["project_dir"] / candidate
    return candidate.resolve()


def resolve_runtime_path(
    context: dict[str, Any], value: str | None, basedir: Path = DEFAULT_BASEDIR
) -> Path | None:
    if not value:
        return None
    if value.startswith("basedir:"):
        relative = value.removeprefix("basedir:").lstrip("/")
        return (basedir / relative).resolve()
    return resolve_project_path(context, value)


def bind_project_assets(context: dict[str, Any], basedir: Path) -> None:
    """Rebind basedir: asset URIs to the CLI-selected ComfyUI basedir."""
    for asset in context.get("asset_registry", {}).values():
        asset["path"] = resolve_runtime_path(context, asset["path_value"], basedir)
    presenter = context["project"]["presenter"]
    if presenter["mode"] == "supplied":
        context["presenter_image"] = resolve_runtime_path(
            context, presenter["image"], basedir
        )
    voice = context["project"]["voice"]
    if voice["mode"] == "supplied":
        context["narration_audio"] = resolve_runtime_path(
            context, voice["audio"], basedir
        )


def _validate_schema(payload: dict[str, Any], schema_name: str) -> None:
    schema_path = FACTORY_ROOT / "schemas" / schema_name
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    if jsonschema is not None:
        jsonschema.validate(payload, schema)


def load_project(project_file: str | Path) -> dict[str, Any]:
    project_path = Path(project_file).expanduser().resolve()
    if not project_path.exists():
        raise FileNotFoundError(f"Project file not found: {project_path}")

    project = load_yaml(project_path)
    if not isinstance(project, dict):
        raise ValueError("Project file must contain a mapping")
    _validate_schema(project, "project.schema.json")

    project_dir = project_path.parent
    profile_path = _resolve_profile(project_dir, project["profile"])
    if not profile_path.exists():
        raise FileNotFoundError(f"Format profile not found: {profile_path}")
    profile = load_yaml(profile_path)
    if not isinstance(profile, dict):
        raise ValueError("Format profile must contain a mapping")

    context = {
        "project": project,
        "profile": profile,
        "project_file": project_path,
        "project_dir": project_dir,
        "profile_file": profile_path,
        "slug": slugify(project["project"]["slug"]),
    }

    content = project["content"]
    script_path = resolve_project_path(context, content.get("script_file"))
    storyboard_path = resolve_project_path(context, content.get("storyboard_file"))
    timing_path = resolve_project_path(context, content.get("timing_file"))
    if script_path is None or not script_path.exists():
        raise FileNotFoundError(f"Script file not found: {script_path}")
    if storyboard_path is not None and not storyboard_path.exists():
        raise FileNotFoundError(f"Storyboard file not found: {storyboard_path}")
    if timing_path is not None and not timing_path.exists():
        raise FileNotFoundError(f"Locked timing file not found: {timing_path}")
    context["script_file"] = script_path
    context["storyboard_file"] = storyboard_path
    context["timing_file"] = timing_path

    registry = project.get("assets", {}).get("registry", {})
    context["asset_registry"] = {
        name: {
            **entry,
            "path_value": entry["path"],
            "path": resolve_runtime_path(context, entry["path"]),
        }
        for name, entry in registry.items()
    }

    presenter = project["presenter"]
    if presenter["mode"] == "supplied":
        image_path = resolve_runtime_path(context, presenter.get("image"))
        if image_path is None:
            raise ValueError("Supplied presenter mode requires an image path")
        context["presenter_image"] = image_path

    voice = project["voice"]
    if voice["mode"] == "supplied":
        audio_path = resolve_runtime_path(context, voice.get("audio"))
        if audio_path is None:
            raise ValueError("Supplied voice mode requires an audio path")
        context["narration_audio"] = audio_path

    return context


def project_asset(
    context: dict[str, Any], name: str, expected_kind: str | None = None
) -> dict[str, Any]:
    try:
        asset = context.get("asset_registry", {})[name]
    except KeyError as exc:
        raise ValueError(f"Unknown project asset reference: {name}") from exc
    if expected_kind and asset.get("kind") != expected_kind:
        raise ValueError(
            f"Project asset {name} must be {expected_kind}, not {asset.get('kind')}"
        )
    return asset


def missing_project_assets(context: dict[str, Any]) -> list[str]:
    missing = []
    if (
        context["project"]["presenter"]["mode"] == "supplied"
        and not context["presenter_image"].exists()
    ):
        missing.append(f"presenter image: {context['presenter_image']}")
    if (
        context["project"]["voice"]["mode"] == "supplied"
        and not context["narration_audio"].exists()
    ):
        missing.append(f"narration audio: {context['narration_audio']}")
    for name, asset in context.get("asset_registry", {}).items():
        path = Path(asset["path"])
        if asset.get("required", True) and not path.exists():
            missing.append(f"{name}: {path}")
        elif expected := asset.get("sha256"):
            actual = sha256_file(path)
            if actual != expected:
                missing.append(
                    f"{name}: SHA-256 mismatch ({actual} != {expected})"
                )
    return missing


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))


def _split_long_sentence(sentence: str, target_words: int) -> list[str]:
    words = sentence.split()
    return [" ".join(words[i : i + target_words]) for i in range(0, len(words), target_words)]


def split_script_by_target_words(text: str, target_words: int) -> list[str]:
    sentences = [
        normalize_text(part)
        for part in re.split(r"(?<=[.!?])\s+", normalize_text(text))
        if normalize_text(part)
    ]
    units: list[str] = []
    for sentence in sentences:
        if word_count(sentence) > max(target_words * 2, target_words + 4):
            units.extend(_split_long_sentence(sentence, target_words))
        else:
            units.append(sentence)

    chunks: list[str] = []
    current: list[str] = []
    current_words = 0
    for unit in units:
        count = word_count(unit)
        if current and current_words + count > max(target_words + 3, math.ceil(target_words * 1.4)):
            chunks.append(" ".join(current))
            current = [unit]
            current_words = count
        else:
            current.append(unit)
            current_words += count
    if current:
        chunks.append(" ".join(current))
    return chunks


def _smooth_type_schedule(count: int, mix: dict[str, float]) -> list[str]:
    normalized = {kind: float(mix.get(kind, 0)) for kind in VALID_SHOT_TYPES}
    total = sum(normalized.values())
    if total <= 0:
        normalized = {"presenter": 0.24, "broll": 0.40, "still": 0.36}
        total = 1.0
    normalized = {kind: value / total for kind, value in normalized.items()}
    actual = {kind: 0 for kind in normalized}
    schedule: list[str] = []
    preference = {"broll": 2, "still": 1, "presenter": 0}
    for index in range(count):
        progress = index + 1
        kind = max(
            normalized,
            key=lambda name: (
                normalized[name] * progress - actual[name],
                preference.get(name, 0),
            ),
        )
        schedule.append(kind)
        actual[kind] += 1
    return schedule


def _storyboard_entries(context: dict[str, Any], script_text: str) -> list[dict[str, Any]]:
    storyboard_path = context.get("storyboard_file")
    if storyboard_path:
        payload = load_yaml(storyboard_path)
        entries = payload.get("shots") if isinstance(payload, dict) else payload
        if not isinstance(entries, list) or not entries:
            raise ValueError("Storyboard must contain a non-empty shots list")
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("narration"):
                raise ValueError("Every storyboard shot requires narration text")
            if entry.get("type", "broll") not in VALID_SHOT_TYPES:
                raise ValueError(f"Unsupported storyboard shot type: {entry.get('type')}")
            if asset_ref := entry.get("asset_ref"):
                asset = project_asset(context, asset_ref)
                if asset.get("kind") not in {"image", "video"}:
                    raise ValueError(
                        f"Shot asset {asset_ref} must be image or video"
                    )
            if pre_grade := entry.get("pre_grade"):
                if not isinstance(pre_grade, dict):
                    raise ValueError("Shot pre_grade must be an object")
                unknown = set(pre_grade) - VALID_PRE_GRADE_KEYS
                if unknown:
                    raise ValueError(
                        "Unsupported shot pre_grade settings: "
                        + ", ".join(sorted(unknown))
                    )
                if source_crop := pre_grade.get("source_crop"):
                    if not isinstance(source_crop, dict) or set(source_crop) != {
                        "x",
                        "y",
                        "width",
                        "height",
                    }:
                        raise ValueError(
                            "Shot source_crop requires x, y, width, and height"
                        )
            if lip_sync_options := entry.get("lip_sync_options"):
                if entry.get("type", "broll") != "presenter":
                    raise ValueError(
                        "Shot lip_sync_options are only valid for presenter shots"
                    )
                if not isinstance(lip_sync_options, dict):
                    raise ValueError("Shot lip_sync_options must be an object")
                unknown = set(lip_sync_options) - VALID_LIP_SYNC_OPTION_KEYS
                if unknown:
                    raise ValueError(
                        "Unsupported shot lip_sync_options: "
                        + ", ".join(sorted(unknown))
                    )
            for continuity_key in entry.get("continuity", []):
                if continuity_key not in context["project"].get("continuity", {}).get(
                    "prompt_tokens", {}
                ):
                    raise ValueError(
                        f"Unknown continuity prompt token: {continuity_key}"
                    )
        storyboard_text = normalize_text(" ".join(entry["narration"] for entry in entries))
        if storyboard_text != normalize_text(script_text):
            raise ValueError(
                "Storyboard narration must exactly cover script.md in the same order "
                "(whitespace differences are ignored)"
            )
        return entries

    profile = context["profile"]
    target_words = max(
        4,
        round(
            float(profile["words_per_minute"])
            * float(profile["average_shot_seconds"])
            / 60
        ),
    )
    chunks = split_script_by_target_words(script_text, target_words)
    schedule = _smooth_type_schedule(len(chunks), profile["visual_mix"])
    return [
        {
            "narration": narration,
            "type": kind,
            "visual": f"Illustrate this narration clearly: {narration}",
        }
        for narration, kind in zip(chunks, schedule)
    ]


def ffprobe_duration(path: str | Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def _continuity_prompt(context: dict[str, Any], entry: dict[str, Any]) -> str:
    prompt_tokens = context["project"].get("continuity", {}).get("prompt_tokens", {})
    return " ".join(
        str(prompt_tokens[key]).strip()
        for key in entry.get("continuity", [])
        if prompt_tokens.get(key)
    )


def _build_visual_prompt(
    context: dict[str, Any], entry: dict[str, Any], shot_type: str
) -> str:
    project = context["project"]
    continuity = _continuity_prompt(context, entry)
    if shot_type == "presenter":
        return normalize_text(
            f"{project['presenter']['animation_prompt']} {continuity}"
        )

    subject = project["subject"]
    style = project.get("style", {})
    explicit_visual = entry.get("visual")
    visual = explicit_visual or f"Illustrate: {entry['narration']}"
    style_value = style.get(shot_type, style.get("global", "realistic documentary"))
    constraints = style.get(
        "constraints",
        "No text, no subtitles, no logos, no watermarks, and no visible brand names.",
    )
    subject_context = f"Environment: {subject['environment']}."
    if not explicit_visual:
        subject_context += (
            f" Topic: {subject['topic']}. Product or method: {subject['product']}."
        )
    return normalize_text(
        f"{visual} {subject_context} Visual style: {style_value}. "
        f"{continuity} {constraints}"
    )


def _locked_timing(
    context: dict[str, Any], entries: list[dict[str, Any]], audio_duration: float | None
) -> tuple[list[dict[str, int | float]], float, str] | None:
    timing_path = context.get("timing_file")
    if timing_path is None:
        return None
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    fps = int(timing["fps"])
    if fps <= 0:
        raise ValueError("Locked timeline fps must be positive")
    delivery_fps = int(context["profile"]["delivery"]["fps"])
    if fps != delivery_fps:
        raise ValueError(
            f"Locked timeline fps ({fps}) must match delivery fps ({delivery_fps})"
        )
    locked_shots = timing.get("shots", [])
    if len(locked_shots) != len(entries):
        raise ValueError("Locked timeline must contain exactly one entry per storyboard shot")

    resolved = []
    previous_end = 0
    for index, (entry, locked) in enumerate(zip(entries, locked_shots), start=1):
        expected_id = entry.get("shot_id", f"s{index:04d}")
        if locked.get("shot_id") != expected_id:
            raise ValueError(
                f"Locked timeline shot {index} must be {expected_id}, "
                f"not {locked.get('shot_id')}"
            )
        start_frame = int(locked["start_frame"])
        end_frame = int(locked["end_frame"])
        if start_frame != previous_end or end_frame <= start_frame:
            raise ValueError("Locked timeline frames must be positive and contiguous")
        resolved.append(
            {
                "start_frame": start_frame,
                "end_frame": end_frame,
                "frame_count": end_frame - start_frame,
                "start": start_frame / fps,
                "end": end_frame / fps,
            }
        )
        previous_end = end_frame

    total_frames = int(timing["total_frames"])
    if previous_end != total_frames:
        raise ValueError("Locked timeline total_frames must match its final shot")
    total_duration = total_frames / fps
    if audio_duration is not None and abs(audio_duration - total_duration) > 1 / fps:
        raise ValueError(
            "Narration differs from the locked timeline by more than one frame: "
            f"audio={audio_duration:.3f}s timeline={total_duration:.3f}s at {fps}fps"
        )
    return resolved, total_duration, f"locked timeline ({fps} fps)"


def compile_shot_manifest(
    context: dict[str, Any], audio_path: str | Path | None = None
) -> dict[str, Any]:
    script_text = context["script_file"].read_text(encoding="utf-8").strip()
    entries = _storyboard_entries(context, script_text)
    profile = context["profile"]
    project = context["project"]

    audio_duration = ffprobe_duration(audio_path) if audio_path else None
    locked_timing = _locked_timing(context, entries, audio_duration)
    if locked_timing:
        locked_shot_times, total_duration, timing_source = locked_timing
    elif audio_duration is not None:
        locked_shot_times = None
        total_duration = audio_duration
        timing_source = "audio"
    else:
        locked_shot_times = None
        total_duration = word_count(script_text) / float(profile["words_per_minute"]) * 60
        timing_source = "word-rate estimate"

    weights = []
    for entry in entries:
        narration = entry["narration"]
        punctuation_pause = 0.12 * narration.count(",") + 0.25 * len(
            re.findall(r"[.!?]", narration)
        )
        weights.append(max(1.0, word_count(narration) + punctuation_pause))
    total_weight = sum(weights)

    if locked_shot_times is None:
        shot_times: list[dict[str, int | float]] = []
        cursor = 0.0
        for index, weight in enumerate(weights):
            duration = total_duration * weight / total_weight
            end = total_duration if index == len(weights) - 1 else cursor + duration
            shot_times.append({"start": cursor, "end": end})
            cursor = end
    else:
        shot_times = locked_shot_times

    engines = project["engines"]
    presenter_pipeline = project["presenter"].get("pipeline")
    shots = []
    for index, (entry, timing) in enumerate(zip(entries, shot_times), start=1):
        shot_type = entry.get("type", "broll")
        start = float(timing["start"])
        end = float(timing["end"])
        asset_ref = entry.get("asset_ref")
        source_asset = project_asset(context, asset_ref) if asset_ref else None
        engine = entry.get("engine", engines[shot_type])
        if source_asset:
            engine = "local_asset"
        elif shot_type == "presenter" and presenter_pipeline:
            engine = presenter_pipeline["lip_sync_engine"]
        shot = {
            "shot_id": entry.get("shot_id", f"s{index:04d}"),
            "index": index,
            "type": shot_type,
            "engine": engine,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
            "narration": normalize_text(entry["narration"]),
            "prompt": _build_visual_prompt(context, entry, shot_type),
            "seed": int(entry.get("seed", int(project.get("seed", 1000)) + index)),
            "continuity": list(entry.get("continuity", [])),
            "asset": None,
            "status": "planned",
        }
        if "start_frame" in timing:
            shot.update(
                {
                    "start_frame": int(timing["start_frame"]),
                    "end_frame": int(timing["end_frame"]),
                    "frame_count": int(timing["frame_count"]),
                }
            )
        if entry.get("pre_grade"):
            shot["pre_grade"] = dict(entry["pre_grade"])
        if source_asset:
            shot.update(
                {
                    "asset_ref": asset_ref,
                    "source": "local_asset",
                    "source_asset": str(source_asset["path"]),
                    "source_start": float(entry.get("source_start", 0)),
                    "source_license": source_asset.get("license", "unspecified"),
                    "source_url": source_asset.get("source_url"),
                    "source_expected_sha256": source_asset.get("sha256"),
                }
            )
        elif shot_type == "presenter" and presenter_pipeline:
            driving = project_asset(
                context, presenter_pipeline["driving_asset"], expected_kind="video"
            )
            shot["presenter_pipeline"] = {
                "motion_engine": presenter_pipeline["motion_engine"],
                "lip_sync_engine": presenter_pipeline["lip_sync_engine"],
                "driving_asset": str(driving["path"]),
                "driving_frame_capacity": int(
                    presenter_pipeline.get("driving_frame_capacity", 100)
                ),
                "fps": int(presenter_pipeline.get("fps", 25)),
                "lip_sync_options": {
                    **presenter_pipeline.get("lip_sync_options", {}),
                    **entry.get("lip_sync_options", {}),
                },
            }
        shots.append(shot)

    manifest = {
        "version": 1,
        "project": context["slug"],
        "title": project["project"]["title"],
        "format_profile": profile["name"],
        "project_file": str(context["project_file"]),
        "script_file": str(context["script_file"]),
        "timing_file": str(context["timing_file"]) if context.get("timing_file") else None,
        "timing_source": timing_source,
        "narration_audio": str(Path(audio_path).resolve()) if audio_path else None,
        "duration": round(total_duration, 3),
        "word_count": word_count(script_text),
        "words_per_minute": float(profile["words_per_minute"]),
        "delivery": profile["delivery"],
        "normalization": project.get("normalization", {}),
        "shots": shots,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    _validate_schema(manifest, "shot-manifest.schema.json")
    return manifest


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "assets": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    write_json_atomic(path, state)


def load_adapter_definition(name: str) -> dict[str, Any]:
    adapter_path = FACTORY_ROOT / "adapters" / f"{name}.json"
    if not adapter_path.exists():
        raise FileNotFoundError(f"Unknown workflow adapter: {name}")
    return json.loads(adapter_path.read_text(encoding="utf-8"))


def load_adapter(name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    adapter = load_adapter_definition(name)
    if adapter.get("kind", "comfyui") != "comfyui" or not adapter.get("template"):
        raise ValueError(f"Adapter {name} is not a ComfyUI workflow adapter")
    template_path = FACTORY_ROOT / adapter["template"]
    prompt = json.loads(template_path.read_text(encoding="utf-8"))
    return adapter, prompt


def build_adapter_prompt(name: str, values: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    adapter, prompt = load_adapter(name)
    unknown = set(values) - set(adapter["slots"])
    if unknown:
        raise ValueError(f"Unsupported values for {name}: {', '.join(sorted(unknown))}")
    missing = [slot for slot in adapter.get("required_slots", []) if slot not in values]
    if missing:
        raise ValueError(f"Missing values for {name}: {', '.join(missing)}")

    for slot_name, value in values.items():
        slot = adapter["slots"][slot_name]
        if slot.get("prefix") and isinstance(value, str):
            value = slot["prefix"] + value
        node = prompt[slot["node"]]
        node.setdefault("inputs", {})[slot["input"]] = value
    return adapter, prompt


def request_json(
    server: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 30,
) -> dict[str, Any]:
    url = urllib.parse.urljoin(server.rstrip("/") + "/", path.lstrip("/"))
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ComfyUI HTTP {exc.code} for {path}: {body}") from exc


def queue_and_wait(
    server: str,
    prompt: dict[str, Any],
    poll_seconds: float = 2,
    timeout_seconds: float = 1800,
) -> tuple[str, dict[str, Any]]:
    queued = request_json(server, "POST", "/prompt", {"prompt": prompt})
    prompt_id = queued["prompt_id"]
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        history = request_json(server, "GET", f"/history/{prompt_id}")
        if prompt_id in history:
            item = history[prompt_id]
            status = item.get("status", {})
            if status.get("completed"):
                return prompt_id, item
            if status.get("status_str") == "error":
                raise RuntimeError(json.dumps(item, indent=2))
        time.sleep(poll_seconds)
    raise TimeoutError(f"Timed out waiting for ComfyUI prompt {prompt_id}")


def _find_output_entry(item: dict[str, Any], output_node: str) -> dict[str, Any]:
    outputs = item.get("outputs", {})
    candidates = []
    if output_node in outputs:
        candidates.append(outputs[output_node])
    candidates.extend(value for key, value in outputs.items() if key != output_node)
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for value in candidate.values():
            if isinstance(value, list):
                for entry in value:
                    if isinstance(entry, dict) and entry.get("filename"):
                        return entry
            elif isinstance(value, dict) and value.get("filename"):
                return value
    raise RuntimeError("ComfyUI completed but returned no file output")


def render_adapter(
    name: str,
    values: dict[str, Any],
    *,
    basedir: Path = DEFAULT_BASEDIR,
    server: str = DEFAULT_SERVER,
    dry_run: bool = False,
    poll_seconds: float = 2,
    timeout_seconds: float = 1800,
) -> dict[str, Any]:
    adapter, prompt = build_adapter_prompt(name, values)
    cache_key = stable_hash({"adapter": name, "prompt": prompt})
    if dry_run:
        return {
            "adapter": name,
            "cache_key": cache_key,
            "prompt": prompt,
            "output_path": None,
            "prompt_id": None,
        }

    prompt_id, item = queue_and_wait(
        server,
        prompt,
        poll_seconds=poll_seconds,
        timeout_seconds=timeout_seconds,
    )
    entry = _find_output_entry(item, adapter["output_node"])
    output_path = basedir / "output" / entry.get("subfolder", "") / entry["filename"]
    return {
        "adapter": name,
        "cache_key": cache_key,
        "output_path": str(output_path.resolve()),
        "prompt_id": prompt_id,
    }


def stage_input(source: str | Path, basedir: Path, relative_name: str) -> str:
    source_path = Path(source).expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"Input asset not found: {source_path}")
    target = basedir / "input" / relative_name
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or sha256_file(source_path) != sha256_file(target):
        shutil.copy2(source_path, target)
    return target.relative_to(basedir / "input").as_posix()


def extract_audio_segment(
    source: str | Path,
    target: str | Path,
    start: float,
    duration: float,
    minimum_duration: float = 0,
) -> Path:
    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    render_duration = max(duration, minimum_duration)
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
            "-af",
            f"apad=pad_dur={max(0, render_duration - duration):.3f}",
            "-t",
            f"{render_duration:.3f}",
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            str(target_path),
        ],
        check=True,
    )
    return target_path


def seconds_to_srt(value: float) -> str:
    milliseconds = max(0, round(value * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def write_srt(manifest: dict[str, Any], path: Path) -> None:
    blocks = []
    for index, shot in enumerate(manifest["shots"], start=1):
        blocks.append(
            f"{index}\n{seconds_to_srt(shot['start'])} --> {seconds_to_srt(shot['end'])}\n"
            f"{shot['narration']}\n"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(blocks), encoding="utf-8")
