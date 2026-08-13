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
- `ltx23_presenter` — legacy image-and-audio presenter baseline
- `ltx25_presenter` — selected fully local LTX 2.5 image-and-supplied-audio presenter
- `longcat_avatar_presenter` — experimental four-second LongCat Avatar benchmark adapter
- `ltx25_t2v` — distilled LTX 2.5 text-to-video B-roll

`ltx25_presenter` uses the local LTX 2.5 transformer, video VAE, audio VAE,
text encoder, and latent upscaler. It does not use the paid/cloud
`LtxApi25AudioToVideo` node.

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

## Local pilot and presenter proofs

The mosquito-control pilot was rendered and assembled locally on an NVIDIA
GeForce RTX 5090. The tracked [pilot render proof](pilot-render-proof.json)
records the render environment, shot mix, probed delivery properties, byte size,
SHA-256 checksum, and the two validated seed overrides without committing
generated media. The final 88-second 1080p delivery passed full-video review
without visible text artifacts.

The first pilot failed presenter acceptance: LTX 2.3 preserved identity but
showed almost no useful mouth articulation behind the heavy beard. A controlled
benchmark using the same image, audio, prompt, seed, and exact 16:9 centre crop
then compared LTX 2.3, fully local LTX 2.5, and LongCat Avatar. The tracked
[presenter benchmark proof](presenter-benchmark-proof.json) records render time,
peak observed VRAM, objective lower-face motion diagnostics, and human visual
review. LTX 2.5 won because it produced clear varied mouth shapes at 1024x576 in
48 seconds. LongCat also articulated but took 264 seconds at 768x432 and was
more exaggerated; LTX 2.3 remained a failed baseline.

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

Screen each presenter shot and record the required visual review:

```bash
python3 scripts/video_factory.py qa-presenter "$PROJECT" --shot-id s0003
python3 scripts/video_factory.py qa-presenter "$PROJECT" --shot-id s0003 \
  --visible-articulation pass \
  --identity-stability pass \
  --temporal-stability pass \
  --text-artifact-free pass \
  --notes "Clear phoneme shapes, stable identity, and no visible text."
```

The automatic lower-face motion check catches nearly frozen mouths. It is not a
phoneme-level synchronisation model, so it can never replace the manual visible
articulation and text-artifact review. For every project containing presenter
shots, assembly rejects missing, stale, incomplete, pending, or failed presenter
QA records. QA records are also invalidated when sampling, motion thresholds,
the mouth ROI, manual criteria, or the screening algorithm changes. Contact
sheets and the complete state-derived `presenter-qa.json` report are written
under the project runtime folder.

Assemble once every shot has an asset and every required presenter review passes:

```bash
python3 scripts/video_factory.py assemble "$PROJECT"
```

Re-run the controlled three-engine benchmark with authorized input assets:

```bash
python3 scripts/presenter_benchmark.py \
  --image /path/to/portrait.png \
  --audio /path/to/four-second.wav
```

The bundled LongCat graph is intentionally a one-window experimental adapter
for approximately four-second proofs. On the RTX 5090, 1024x576 exhausted 32 GB
VRAM; 768x432 with 35 swapped transformer blocks completed in 264 seconds with
25,235 MiB peak observed total GPU memory. Its optional Python dependencies and
custom-node provider sources are verified by
`spider/userscripts_dir/08-install-presenter-benchmark-deps.sh`; the benchmark
also checks ComfyUI's `/object_info` registry before submitting a LongCat graph.
The setup script requires each provider to have the expected GitHub origin,
exact immutable commit, clean checkout, and complete node-class set. It reports
the mismatch and exact installation commands without modifying the ignored
runtime custom-node tree.

The pins below are the commits matching the four provider versions used by the
proven local benchmark. Install them under `spider/custom_nodes/`, then restart
ComfyUI:

```bash
git clone --no-checkout https://github.com/kijai/ComfyUI-WanVideoWrapper.git spider/custom_nodes/ComfyUI-WanVideoWrapper
git -C spider/custom_nodes/ComfyUI-WanVideoWrapper checkout --detach e091c4a77425d6a4a7f90ab30c513d24f8cb91cf
git clone --no-checkout https://github.com/kijai/ComfyUI-KJNodes.git spider/custom_nodes/comfyui-kjnodes
git -C spider/custom_nodes/comfyui-kjnodes checkout --detach f710f2635dbadbaf1ccf7d25572daa7dfec80bfd
git clone --no-checkout https://github.com/kijai/ComfyUI-MelBandRoFormer.git spider/custom_nodes/ComfyUI-MelBandRoFormer
git -C spider/custom_nodes/ComfyUI-MelBandRoFormer checkout --detach 92c86854e6654f4aacc97484471af95c98ea16d4
git clone --no-checkout https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git spider/custom_nodes/comfyui-videohelpersuite
git -C spider/custom_nodes/comfyui-videohelpersuite checkout --detach 3234937ff5f3ca19068aaba5042771514de2429d
```

Registry-installed providers do not retain Git metadata and therefore fail this
strict reproducibility check. Move an existing provider directory aside before
cloning its pinned checkout; do not clone over an existing directory or discard
local changes.

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
