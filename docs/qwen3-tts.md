# Qwen3-TTS ComfyUI Setup

This repo tracks the Docker configuration, startup scripts, reusable workflows,
and API driver needed to recreate the Qwen3-TTS work.

It intentionally does not track large runtime data:

- `basedir/models/`
- `basedir/input/`
- `basedir/output/`
- `spider/run/`
- `spider/custom_nodes/`

Those folders contain downloaded models, generated media, local ComfyUI runtime
state, venvs, and cloned custom nodes. They can be recreated or redownloaded.

## Architecture

```mermaid
flowchart LR
    A["1. Project control\nGavLife card + AGENTS.md"]
    B["2. Tracked setup\ncompose.yaml, startup scripts,\nworkflow JSONs, batch driver, docs"]
    C["3. Running Comfy service\nDocker spider service\nComfy API at 127.0.0.1:8188\nQwen3-TTS custom node"]
    D["4. Generation modes\nUI file reference\nUI microphone reference\nPython long-form batch driver"]
    E["5. Local outputs\nchunk audio, project folders,\nmanifest.json for restartability"]
    F["Ignored local data\nmodels, input clips, generated audio,\ncloned custom nodes, runtime state"]

    A --> B --> C --> D --> E
    B -. "recreates / configures" .-> F
    C -. "uses but does not commit" .-> F
    E -. "saved locally" .-> F
```

The main seam is the Comfy HTTP API. Interactive work can happen in the
browser with tracked workflow JSONs, while long-form generation is driven by
`scripts/qwen3_batch_tts.py` without embedding looping logic into a visual
workflow. The repo tracks only the reproducibility layer; model weights,
reference media, generated outputs, cloned custom nodes, and venv/runtime state
remain local runtime data.

## Runtime

From the repository root, start the active Docker service in `spider/`:

```bash
cd spider
docker compose up -d
```

ComfyUI serves:

```text
http://127.0.0.1:8188
```

The container mounts:

- `../basedir` at `/basedir`
- `./custom_nodes` at `/basedir/custom_nodes`
- `./userscripts_dir` at `/userscripts_dir`

## Qwen3-TTS

Startup script `spider/userscripts_dir/07-install-qwen3-tts.sh` ensures:

- `DarioFT/ComfyUI-Qwen3-TTS` is cloned into `/basedir/custom_nodes/ComfyUI-Qwen3-TTS`
- `sox` is installed
- Qwen3-TTS Python dependencies are installed in the active Comfy venv

The Qwen model weights are not tracked. They download into:

```text
basedir/models/Qwen3-TTS/
```

## Workflows

Tracked reusable workflows:

- `basedir/user/default/workflows/audio/qwen3_tts_voice_clone.json`
- `basedir/user/default/workflows/audio/qwen3_tts_record_voice_clone.json`

The file-based workflow uses `LoadAudio`. The recording workflow uses
`RecordAudio` for browser microphone input.

For voice clone, `Qwen3VoiceClone.ref_text` must match the reference audio
exactly.

## Batch Driver

Long text should be generated with the external API driver:

```mermaid
flowchart LR
    A["1. Start Comfy\ncd spider\ndocker compose up -d"]
    B["2. Record reference\nUse the exact text you will pass as reference text\nKeep it clean and under the ref max seconds"]
    C["3. Prepare files\nReference audio: basedir/input or absolute path\nLong script: plain UTF-8 text file"]
    D["4. Dry run\nCheck chunking before spending GPU time\n--dry-run"]
    E["5. Run batch\nQueue each chunk through Comfy HTTP API\nSave audio plus manifest"]
    F["6. Review or resume\nUse manifest.json\n--start-at, --limit, --overwrite"]
    G["7. Optional combine\n--concat writes combined.flac\nDefault pause: 500 ms"]

    A --> B --> C --> D --> E --> F --> G
```

Rendered runbook: `docs/qwen3-tts-long-script-runbook.png`.

```bash
python3 scripts/qwen3_batch_tts.py \
  --basedir basedir \
  --reference-audio my_voice_ref.wav \
  --reference-text "exact words read in the reference recording" \
  --script-file /path/to/long_script.txt \
  --project-name my-reading
```

Outputs are saved under:

```text
basedir/output/audio/qwen3_projects/<project-name>/
```

Each project folder includes `manifest.json` for restartability.

Useful batch options:

- `--dry-run` previews the generated chunks without queueing Comfy work.
- `--max-chars` controls the target chunk size. The default is `650`.
- `--start-at` resumes from a numbered chunk.
- `--limit` runs only a small number of chunks for a test pass.
- `--overwrite` reruns chunks that already succeeded.
- `--concat` writes `combined.flac` after successful chunks finish.
- `--pause-ms` controls silence inserted between chunks when using `--concat`.
  The default is `500`; use `--pause-ms 0` for direct concatenation.
