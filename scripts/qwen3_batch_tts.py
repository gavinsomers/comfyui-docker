#!/usr/bin/env python3
import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_BASEDIR = Path("/home/gavman/code/forks/comfy/basedir")
DEFAULT_SERVER = "http://127.0.0.1:8188"


def slugify(value):
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = value.strip("-._")
    return value or time.strftime("qwen3-%Y%m%d-%H%M%S")


def read_text_arg(text, text_file, label):
    if text and text_file:
        raise SystemExit(f"Use either --{label} or --{label}-file, not both")
    if text_file:
        return Path(text_file).read_text(encoding="utf-8").strip()
    if text:
        return text.strip()
    raise SystemExit(f"Missing --{label} or --{label}-file")


def split_sentences(text):
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def split_long_text(text, max_chars):
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
    chunks = []

    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            chunks.append(paragraph)
            continue

        current = []
        current_len = 0
        for sentence in split_sentences(paragraph):
            if len(sentence) > max_chars:
                if current:
                    chunks.append(" ".join(current).strip())
                    current = []
                    current_len = 0
                chunks.extend(split_by_words(sentence, max_chars))
                continue

            proposed_len = current_len + len(sentence) + (1 if current else 0)
            if current and proposed_len > max_chars:
                chunks.append(" ".join(current).strip())
                current = [sentence]
                current_len = len(sentence)
            else:
                current.append(sentence)
                current_len = proposed_len

        if current:
            chunks.append(" ".join(current).strip())

    return chunks


def split_by_words(text, max_chars):
    chunks = []
    current = []
    current_len = 0
    for word in text.split():
        proposed_len = current_len + len(word) + (1 if current else 0)
        if current and proposed_len > max_chars:
            chunks.append(" ".join(current).strip())
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len = proposed_len
    if current:
        chunks.append(" ".join(current).strip())
    return chunks


def ensure_reference_in_input(reference_audio, basedir):
    input_dir = basedir / "input"
    reference = Path(reference_audio).expanduser()

    if not reference.is_absolute():
        candidate = input_dir / reference
        if candidate.exists():
            return str(reference).replace("\\", "/")
        raise SystemExit(f"Reference audio not found in {input_dir}: {reference_audio}")

    if not reference.exists():
        raise SystemExit(f"Reference audio does not exist: {reference}")

    try:
        reference.relative_to(input_dir)
        return str(reference.relative_to(input_dir)).replace("\\", "/")
    except ValueError:
        target = input_dir / reference.name
        if target.exists() and target.resolve() != reference.resolve():
            stem = reference.stem
            suffix = reference.suffix
            target = input_dir / f"{stem}-{int(time.time())}{suffix}"
        shutil.copy2(reference, target)
        return str(target.relative_to(input_dir)).replace("\\", "/")


def request_json(server, method, path, payload=None, timeout=30):
    url = urllib.parse.urljoin(server.rstrip("/") + "/", path.lstrip("/"))
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def build_prompt(args, reference_audio_name, ref_text, chunk_text, filename_prefix, seed):
    return {
        "1": {
            "class_type": "LoadAudio",
            "inputs": {"audio": reference_audio_name},
        },
        "2": {
            "class_type": "Qwen3Loader",
            "inputs": {
                "repo_id": args.repo_id,
                "source": args.source,
                "precision": args.precision,
                "attention": args.attention,
                "local_model_path": args.local_model_path,
            },
        },
        "3": {
            "class_type": "Qwen3VoiceClone",
            "inputs": {
                "model": ["2", 0],
                "text": chunk_text,
                "seed": seed,
                "language": args.language,
                "ref_audio": ["1", 0],
                "ref_text": ref_text,
                "max_new_tokens": args.max_new_tokens,
                "ref_audio_max_seconds": args.ref_audio_max_seconds,
            },
        },
        "4": {
            "class_type": "SaveAudio",
            "inputs": {
                "audio": ["3", 0],
                "filename_prefix": filename_prefix,
            },
        },
    }


def queue_and_wait(server, prompt, poll_seconds, timeout_seconds):
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

    raise TimeoutError(f"Timed out waiting for prompt {prompt_id}")


def output_path_from_history(item, basedir):
    outputs = item.get("outputs", {})
    audio_entries = outputs.get("4", {}).get("audio", [])
    if not audio_entries:
        raise RuntimeError("Prompt completed but SaveAudio returned no audio output")
    entry = audio_entries[0]
    return basedir / "output" / entry.get("subfolder", "") / entry["filename"]


def load_manifest(path):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "chunks": [],
    }


def save_manifest(path, manifest):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def completed_indexes(manifest):
    done = set()
    for chunk in manifest.get("chunks", []):
        if chunk.get("status") == "success":
            done.add(chunk["index"])
    return done


def concatenate_outputs(project_dir, manifest):
    outputs = [
        Path(chunk["output_path"])
        for chunk in sorted(manifest["chunks"], key=lambda c: c["index"])
        if chunk.get("status") == "success"
    ]
    if not outputs:
        return None
    list_file = project_dir / "concat.txt"
    list_file.write_text(
        "".join(f"file '{path.as_posix()}'\n" for path in outputs),
        encoding="utf-8",
    )
    target = project_dir / "combined.flac"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(target)],
        check=True,
    )
    return target


def main():
    parser = argparse.ArgumentParser(description="Batch Qwen3-TTS voice clone chunks through a running ComfyUI server.")
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--basedir", default=str(DEFAULT_BASEDIR))
    parser.add_argument("--reference-audio", required=True, help="Filename under basedir/input, or an absolute path to copy there.")
    parser.add_argument("--reference-text")
    parser.add_argument("--reference-text-file")
    parser.add_argument("--script-file", required=True)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--max-chars", type=int, default=650)
    parser.add_argument("--start-at", type=int, default=1)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--concat", action="store_true")
    parser.add_argument("--repo-id", default="Qwen/Qwen3-TTS-12Hz-0.6B-Base")
    parser.add_argument("--source", default="HuggingFace")
    parser.add_argument("--precision", default="bf16")
    parser.add_argument("--attention", default="sdpa")
    parser.add_argument("--local-model-path", default="")
    parser.add_argument("--language", default="English")
    parser.add_argument("--max-new-tokens", type=int, default=1024)
    parser.add_argument("--ref-audio-max-seconds", type=float, default=15.0)
    parser.add_argument("--seed", type=int, default=1000)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    args = parser.parse_args()

    basedir = Path(args.basedir).expanduser().resolve()
    ref_text = read_text_arg(args.reference_text, args.reference_text_file, "reference-text")
    reference_audio_name = ensure_reference_in_input(args.reference_audio, basedir)
    script_text = Path(args.script_file).read_text(encoding="utf-8")
    chunks = split_long_text(script_text, args.max_chars)

    project = slugify(args.project_name)
    output_prefix_dir = f"audio/qwen3_projects/{project}"
    project_dir = basedir / "output" / output_prefix_dir
    manifest_path = project_dir / "manifest.json"
    manifest = load_manifest(manifest_path)
    manifest.update(
        {
            "project": project,
            "server": args.server,
            "reference_audio": reference_audio_name,
            "reference_text": ref_text,
            "script_file": str(Path(args.script_file).resolve()),
            "max_chars": args.max_chars,
            "repo_id": args.repo_id,
            "language": args.language,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
    )

    selected = [(i, text) for i, text in enumerate(chunks, start=1) if i >= args.start_at]
    if args.limit is not None:
        selected = selected[: args.limit]

    print(f"Project: {project}")
    print(f"Chunks: {len(chunks)} total, {len(selected)} selected")
    print(f"Output: {project_dir}")

    if args.dry_run:
        for i, text in selected:
            preview = text.replace("\n", " ")
            print(f"{i:03d} ({len(text)} chars): {preview[:180]}")
        return 0

    project_dir.mkdir(parents=True, exist_ok=True)
    done = completed_indexes(manifest)
    chunk_records = {chunk["index"]: chunk for chunk in manifest.get("chunks", [])}

    for index, chunk_text in selected:
        if index in done and not args.overwrite:
            print(f"{index:03d}: skipping existing success")
            continue

        filename_prefix = f"{output_prefix_dir}/{index:03d}"
        prompt = build_prompt(args, reference_audio_name, ref_text, chunk_text, filename_prefix, args.seed + index)
        print(f"{index:03d}: queueing {len(chunk_text)} chars")

        record = {
            "index": index,
            "text": chunk_text,
            "status": "queued",
            "filename_prefix": filename_prefix,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        chunk_records[index] = record
        manifest["chunks"] = [chunk_records[i] for i in sorted(chunk_records)]
        save_manifest(manifest_path, manifest)

        try:
            prompt_id, item = queue_and_wait(args.server, prompt, args.poll_seconds, args.timeout_seconds)
            output_path = output_path_from_history(item, basedir)
            record.update(
                {
                    "status": "success",
                    "prompt_id": prompt_id,
                    "output_path": str(output_path),
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                }
            )
            print(f"{index:03d}: wrote {output_path}")
        except Exception as exc:
            record.update(
                {
                    "status": "error",
                    "error": str(exc),
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                }
            )
            manifest["chunks"] = [chunk_records[i] for i in sorted(chunk_records)]
            save_manifest(manifest_path, manifest)
            raise

        manifest["chunks"] = [chunk_records[i] for i in sorted(chunk_records)]
        save_manifest(manifest_path, manifest)

    if args.concat:
        target = concatenate_outputs(project_dir, manifest)
        if target:
            manifest["combined_output"] = str(target)
            save_manifest(manifest_path, manifest)
            print(f"Combined output: {target}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
