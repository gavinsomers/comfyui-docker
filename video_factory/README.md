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
master timeline. V2 projects can lock that timeline to exact delivery frames,
resolve rights-recorded local assets, and run presenter motion before final
lip-sync.

```text
project.yaml + script.md + optional storyboard.yaml
                         |
                         v
                 timed shot manifest
                         |
          +--------------+----------------+
          |              |                |
      presenter      generated media   local stock
          |                                |
   LivePortrait motion                     |
          |                                |
   LatentSync 1.6 mask3                    |
          |                                |
   tracked mouth restore                   |
          +--------------+-----------------+
                         |
                         v
          pre-grade normalization -> final grade
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
- `liveportrait_motion` — restrained head and upper-face motion pass
- `latentsync16_mouthrestore` — external pinned LatentSync 1.6 mask3 pass plus tracked mouth-only source restoration
- `ltx25_t2v` — distilled LTX 2.5 text-to-video B-roll

`ltx25_presenter` uses the local LTX 2.5 transformer, video VAE, audio VAE,
text encoder, and latent upscaler. It does not use the paid/cloud
`LtxApi25AudioToVideo` node.

The ComfyUI API templates came from successful local workflows and expose only
generic slots such as `prompt`, `seed`, `duration`, `image`, `audio`, and
`output_prefix`. The external LatentSync adapter exposes the same slot contract
while its runner owns container preflight, inference, mouth restoration, and
output validation.

## Example project

```text
video_factory/example_projects/mosquito_control/
├── project.yaml
├── script.md
├── storyboard.yaml
└── timing-v1-locked-30fps.json
```

The V2 example is exactly 2,640 frames (88 seconds) at 30 fps. Its tracked
`timing-v1-locked-30fps.json` rounds every V1 cut to the nearest V2 frame, so
each cut remains within one frame of the pilot. Four insect shots resolve to
prepared real footage instead of generative engines. Two repeatedly rejected
bucket generations resolve to checksum-pinned, locally approved corrective
images. Presenter action B-roll keeps the face outside frame and uses canonical
wardrobe and bucket prompt constraints.

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
[presenter benchmark proof](presenter-benchmark-proof.json) records that earlier
baseline.

V2 supersedes that baseline with the approved LivePortrait + LatentSync 1.6
`mask3` + tracked mouth-only restore. The exact s0003 proof passed lower-face
motion screening, source-preservation diagnostics, still review, and Gavin's
mandatory playback checks for timing, jitter, beard/teeth behaviour, and seams.
The tracked [V2 presenter selection proof](presenter-v2-selection-proof.json)
records the selected parameters, hashes, CUDA 12.8 runtime contract, and license
review without committing generated media.

The tracked [final V2 delivery proof](final-v2-delivery-proof.json) owns the
assembled output checksum and records the required presenter, corrected B-roll,
and technical QA approvals.

## Commands

Run from the repository root:

```bash
PROJECT=video_factory/example_projects/mosquito_control/project.yaml

python3 scripts/video_factory.py validate "$PROJECT"
python3 scripts/video_factory.py compile "$PROJECT"
python3 scripts/video_factory.py render "$PROJECT" --module all --dry-run
```

Prepare the ignored V2 stock and authorized presenter inputs first. The helper
downloads checksum-pinned public-domain/CC0 sources, extracts text-free 1080p
clips, copies the V1 canonical presenter and subtle driving clip, adopts any
available checksum-matching approved corrective images, and writes runtime
source credits:

```bash
python3 scripts/prepare_mosquito_v2_assets.py
```

The optional V2 presenter stack requires the exact pinned LivePortrait provider
and a pinned LatentSync 1.6 runtime. `09-install-video-factory-v2-deps.sh`
verifies the clean LivePortrait checkout, installs its bounded dependencies,
checks out LatentSync commit `a229c3948406bc2cf6eaf4873e662e70c6a04746`,
rebuilds its isolated Python dependencies as the exact approved distribution
set, applies the tracked Face Alignment + BlazeFace inference patch, and
downloads checksum-verified model files from immutable Hugging Face revisions.
The LivePortrait checkout intentionally uses the import-safe directory name
`ComfyUI_LivePortraitKJ`. LatentSync dependencies are isolated under
`spider/run/latentsync-1.6/pydeps`; the production `spider` environment remains
on PyTorch 2.11.0+cu128 with CUDA 12.8. CUDA 13.0 remains an isolated canary and
is not promoted to production.

The model checkpoint is tagged `openrail++`. The tracked
[license record](licenses/latentsync-1.6.md) records the exact model revision,
terms relevant to ordinary channel use, and use restrictions. Model weights and
generated media remain outside Git.

Start or restart ComfyUI after preparation:

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

Render, resume, or selectively replace shots. Local stock shot IDs are recorded
without submitting a generative workflow. Presenter shots cache LivePortrait
motion and the external LatentSync/mouth-restore output independently under
`shot:<id>:motion` and `shot:<id>`. Before production inference, the runner
verifies the container's Torch/CUDA versions plus source, patch, config, and
model hashes. Presenter shots longer than the approved 100-frame driver use an
exact-length forward/reverse driving loop, avoiding a hard jump while leaving
the approved four-second path unchanged:

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
  --lip-sync pass \
  --identity-stability pass \
  --temporal-stability pass \
  --beard-teeth-stability pass \
  --blend-seam-free pass \
  --text-artifact-free pass \
  --notes "Clear sync and articulation, stable face/beard/teeth, no seams or text."
```

The automatic lower-face motion check catches nearly frozen mouths. It is not a
phoneme-level synchronisation model, so it can never replace the manual visible
articulation and text-artifact review. A rendered V2 clip is not accepted merely
because both stages completed; obvious blur, beard changes, seams, teeth
artifacts, or lip drift must be marked as a manual failure. For every project containing presenter
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
for approximately four-second proofs. The presenter benchmark proof owns its
tested resolution, performance, and memory constraints. Its optional Python
dependencies and custom-node provider sources are verified by
`spider/userscripts_dir/08-install-presenter-benchmark-deps.sh`; the benchmark
also checks ComfyUI's `/object_info` registry before submitting a LongCat graph.
The setup script requires each provider to have the expected GitHub origin,
exact immutable commit, clean checkout, and complete node-class set. It reports
the mismatch and exact installation commands without modifying the ignored
runtime custom-node tree.

The same preflight derives its bounded Python install list and import checks
from the pinned providers'
[direct dependency contract](../spider/userscripts_dir/presenter_benchmark_deps.py).
The two providers that declare desktop OpenCV and KJNodes' headless declaration
resolve to the proven headless distribution, which supplies their shared `cv2`
module without installing conflicting OpenCV wheels.

The dependency contract owns the provider origins and immutable commit pins. To
check `spider/custom_nodes/` and print exact installation commands for any
missing or mismatched provider, run:

```bash
python3 spider/userscripts_dir/presenter_benchmark_deps.py \
  check-nodes spider/custom_nodes
```

Registry-installed providers do not retain Git metadata and therefore fail this
strict reproducibility check. Move an existing provider directory aside before
running the printed installation commands, then restart ComfyUI. Do not clone
over an existing directory or discard local changes.

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
- `presenter.pipeline` runs a configured motion adapter first and lip-sync adapter last.
- `presenter.pipeline.lip_sync_options` pins LatentSync steps, guidance, mask, and mouth-restoration geometry.
- Presenter storyboard entries may override `lip_sync_options` for a human-approved shot-specific restoration geometry.
- `assets.registry` records local media paths, kinds, rights, source URLs, and optional SHA-256 pins.
- `basedir:` asset URIs follow the CLI-selected ComfyUI basedir across worktrees.
- `content.timing_file` locks cuts and duration to delivery frames.
- `voice.mode: custom_voice` generates local Qwen3-TTS narration.
- `voice.mode: supplied` uses an existing audio file.

Cache keys include the fully patched workflow prompt and staged input hashes.
Normalized assembly clips additionally track the source asset hash, source
in-point, exact frame duration, delivery settings, and pre-grade configuration.
V2 normalization supports deterministic duplicate-frame or blended FPS
conformance, denoise, sharpening, common exposure/colour controls, per-shot
colour overrides and source crops, and an optional final LUT followed by warmth
and film grain. The final render is capped
to the manifest's exact frame count. Changing a voice invalidates narration and
presenter-dependent work without forcing unrelated stills or B-roll to be
regenerated.

## Boundaries

- ComfyUI is the generation backend, not the long-form editor.
- FFmpeg performs deterministic normalization and assembly.
- Generated B-roll audio is discarded; the project narration remains the audio
  master.
- Health, safety, product, and legal claims still require human review.
- Use original or authorized presenter images and voices.
