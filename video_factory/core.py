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
    if script_path is None or not script_path.exists():
        raise FileNotFoundError(f"Script file not found: {script_path}")
    if storyboard_path is not None and not storyboard_path.exists():
        raise FileNotFoundError(f"Storyboard file not found: {storyboard_path}")
    context["script_file"] = script_path
    context["storyboard_file"] = storyboard_path

    presenter = project["presenter"]
    if presenter["mode"] == "supplied":
        image_path = resolve_project_path(context, presenter.get("image"))
        if image_path is None or not image_path.exists():
            raise FileNotFoundError(f"Supplied presenter image not found: {image_path}")
        context["presenter_image"] = image_path

    voice = project["voice"]
    if voice["mode"] == "supplied":
        audio_path = resolve_project_path(context, voice.get("audio"))
        if audio_path is None or not audio_path.exists():
            raise FileNotFoundError(f"Supplied narration audio not found: {audio_path}")
        context["narration_audio"] = audio_path

    return context


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


def _build_visual_prompt(
    context: dict[str, Any], entry: dict[str, Any], shot_type: str
) -> str:
    project = context["project"]
    if shot_type == "presenter":
        return normalize_text(project["presenter"]["animation_prompt"])

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
        f"{visual} {subject_context} Visual style: {style_value}. {constraints}"
    )


def compile_shot_manifest(
    context: dict[str, Any], audio_path: str | Path | None = None
) -> dict[str, Any]:
    script_text = context["script_file"].read_text(encoding="utf-8").strip()
    entries = _storyboard_entries(context, script_text)
    profile = context["profile"]
    project = context["project"]

    if audio_path:
        total_duration = ffprobe_duration(audio_path)
        timing_source = "audio"
    else:
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

    shot_times: list[tuple[float, float]] = []
    cursor = 0.0
    for index, weight in enumerate(weights):
        duration = total_duration * weight / total_weight
        end = total_duration if index == len(weights) - 1 else cursor + duration
        shot_times.append((cursor, end))
        cursor = end

    engines = project["engines"]
    shots = []
    for index, (entry, (start, end)) in enumerate(zip(entries, shot_times), start=1):
        shot_type = entry.get("type", "broll")
        shot = {
            "shot_id": entry.get("shot_id", f"s{index:04d}"),
            "index": index,
            "type": shot_type,
            "engine": entry.get("engine", engines[shot_type]),
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
            "narration": normalize_text(entry["narration"]),
            "prompt": _build_visual_prompt(context, entry, shot_type),
            "seed": int(entry.get("seed", int(project.get("seed", 1000)) + index)),
            "asset": None,
            "status": "planned",
        }
        shots.append(shot)

    manifest = {
        "version": 1,
        "project": context["slug"],
        "title": project["project"]["title"],
        "format_profile": profile["name"],
        "project_file": str(context["project_file"]),
        "script_file": str(context["script_file"]),
        "timing_source": timing_source,
        "narration_audio": str(Path(audio_path).resolve()) if audio_path else None,
        "duration": round(total_duration, 3),
        "word_count": word_count(script_text),
        "words_per_minute": float(profile["words_per_minute"]),
        "delivery": profile["delivery"],
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


def load_adapter(name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    adapter_path = FACTORY_ROOT / "adapters" / f"{name}.json"
    if not adapter_path.exists():
        raise FileNotFoundError(f"Unknown workflow adapter: {name}")
    adapter = json.loads(adapter_path.read_text(encoding="utf-8"))
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
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


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
