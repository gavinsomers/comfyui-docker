# Modular ComfyUI Video Factory

This directory contains the reusable production layer for authority-led explainer
videos. The mosquito project is an example input, not a hard-coded workflow.
Changing the project configuration, script, storyboard, presenter, voice,
product, or environment does not require editing a ComfyUI graph.

Large inputs, model weights, generated media, and runtime state stay under the
ignored `basedir/` tree.

## Architecture

The factory separates three concerns:

1. **Format profile** — pacing, presenter share, visual mix, and delivery format.
2. **Project package** — subject, product, environment, presenter, voice, script,
   visual style, and optional explicit storyboard.
3. **Workflow adapters** — generic model-specific ComfyUI API templates with
   named input slots.

The compiler writes a timed shot manifest. The renderer queues independent,
cacheable assets. FFmpeg assembles normalized clips using the narration as the
master timeline.

```text
project.yaml + script.md + optional storyboard.yaml
                         |
                         v
                 timed shot manifest
                         |
          +--------------+---------------+
          |              |               |
      presenter        B-roll          stills
          +--------------+---------------+
                         |
                         v
                FFmpeg hard-cut assembly
```

## Included local adapters

- `qwen3_custom_voice` — Qwen3-TTS preset-voice narration
- `z_image_turbo` — fast presenter-master and still generation
- `ltx23_presenter` — image-and-audio conditioned talking presenter
- `ltx25_t2v` — distilled LTX 2.5 text-to-video B-roll

The API templates came from successful local workflows and expose only generic
slots such as `prompt`, `seed`, `duration`, `image`, `audio`, and
`output_prefix`.

## Example project

```text
video_factory/example_projects/mosquito_control/
├── project.yaml
├── script.md
└── storyboard.yaml
```

The example is approximately 88 seconds at the profile's target speaking rate.
Its 20-shot plan uses the same approximate visual ratio as the reference format:
25% presenter, 40% generated B-roll, and 35% animated stills.

## Local pilot proof

The mosquito-control pilot was rendered and assembled locally on an NVIDIA
GeForce RTX 5090. The tracked [pilot render proof](pilot-render-proof.json)
records the render environment, shot mix, probed delivery properties, byte size,
and SHA-256 checksum without committing generated media.

## Commands

Run from the repository root:

```bash
PROJECT=video_factory/example_projects/mosquito_control/project.yaml

python3 scripts/video_factory.py validate "$PROJECT"
python3 scripts/video_factory.py compile "$PROJECT"
python3 scripts/video_factory.py render "$PROJECT" --module all --dry-run
```

Start ComfyUI before real rendering:

```bash
cd spider
docker compose up -d
cd ..
```

Render the preparation modules independently:

```bash
python3 scripts/video_factory.py render "$PROJECT" --module narration
python3 scripts/video_factory.py render "$PROJECT" --module presenter-master
```

Render, resume, or selectively replace shots:

```bash
python3 scripts/video_factory.py render "$PROJECT" --module shots
python3 scripts/video_factory.py render "$PROJECT" --module broll --limit 2
python3 scripts/video_factory.py render "$PROJECT" --module still --shot-id s0005 --overwrite
python3 scripts/video_factory.py status "$PROJECT"
```

Assemble once every shot has an asset:

```bash
python3 scripts/video_factory.py assemble "$PROJECT"
```

Runtime output is written to:

```text
basedir/output/video_factory/<project-slug>/
```

That folder contains `shot-manifest.json`, `captions.srt`, resumable
`state.json`, normalized assembly clips, and the 1080p delivery MP4.

## Reusing the factory

Copy the example project directory and change `project.yaml`, `script.md`, and
`storyboard.yaml`. An explicit storyboard gives editorial control. If
`storyboard_file` is omitted, the compiler divides the script according to the
format profile and creates generic narration-led visual prompts.

Supported input modes:

- `presenter.mode: generated` generates a master portrait.
- `presenter.mode: supplied` stages an existing portrait.
- `voice.mode: custom_voice` generates local Qwen3-TTS narration.
- `voice.mode: supplied` uses an existing audio file.

Cache keys include the fully patched workflow prompt and staged input hashes.
Normalized assembly clips additionally track the source asset hash, shot
duration, and delivery settings. Changing a voice invalidates narration and
presenter-dependent work without forcing unrelated stills or B-roll to be
regenerated.

## Boundaries

- ComfyUI is the generation backend, not the long-form editor.
- FFmpeg performs deterministic normalization and assembly.
- Generated B-roll audio is discarded; the project narration remains the audio
  master.
- Health, safety, product, and legal claims still require human review.
- Use original or authorized presenter images and voices.
