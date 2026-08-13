# Gavin Talking Head Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a first 24.195 second Gavin-like realistic 16:9 YouTube talking-head validation render using the Fish S2 Pro audio clip and the existing LTX 2.3 talking portrait workflow.

**Architecture:** Keep the existing ComfyUI `spider` runtime as the video-generation host. Stop the separate `fish-s2-pro-server` container after audio generation, prepare small input assets under `basedir/input/`, clone the existing LTX 2.3 talking portrait workflow, and update only its source image, source audio, and output prefix nodes for the first validation run.

**Tech Stack:** Docker, Docker Compose, ComfyUI, LTX 2.3 talking portrait workflow JSON, FFmpeg/FFprobe, Python standard library JSON tooling.

## Global Constraints

- Project workspace: `/home/gavman/code/forks/comfy`.
- GavLife card: `CODE-205`.
- Source audio: `/home/gavman/Downloads/fish_s2_pro_recorded_ref_00009.flac`.
- Source audio properties: FLAC, 24.195193 seconds, 44.1 kHz, mono, 16-bit.
- Primary workflow seed file: `basedir/user/default/workflows/video/ltx2_3_talking_portrait_ia2v.json`.
- Working workflow file: `basedir/user/default/workflows/video/code205_gavin_youtube_talking_head_ltx2_3.json`.
- Presenter source image path: `basedir/input/code205-gavin-youtube-presenter.png`.
- Prepared audio paths: `basedir/input/code205-gavin-fish-s2-pro.flac` and `basedir/input/code205-gavin-fish-s2-pro.wav`.
- Runtime rule: stop `fish-s2-pro-server` before video generation; keep `spider` running.
- Repository data boundary: do not commit `basedir/input/`, `basedir/output/`, `basedir/models/`, `spider/run/`, or `spider/custom_nodes/`.
- First output target: 16:9 landscape validation render, 1280x720 or closest stable workflow-native size.

---

### Task 1: Runtime And Input Preparation

**Files:**
- Read: `/home/gavman/Downloads/fish_s2_pro_recorded_ref_00009.flac`
- Create: `basedir/input/code205-gavin-fish-s2-pro.flac`
- Create: `basedir/input/code205-gavin-fish-s2-pro.wav`
- No Git-tracked files change in this task.

**Interfaces:**
- Consumes: existing Fish S2 Pro FLAC at `/home/gavman/Downloads/fish_s2_pro_recorded_ref_00009.flac`.
- Produces: Comfy-loadable FLAC and WAV files named `code205-gavin-fish-s2-pro.flac` and `code205-gavin-fish-s2-pro.wav`.

- [ ] **Step 1: Confirm the active runtime containers**

Run:

```bash
docker ps --format '{{.Names}} {{.Status}} {{.Ports}}'
```

Expected output includes:

```text
fish-s2-pro-server Up
spider Up
```

- [ ] **Step 2: Stop the Fish S2 Pro server**

Run:

```bash
docker stop fish-s2-pro-server
```

Expected output:

```text
fish-s2-pro-server
```

- [ ] **Step 3: Confirm ComfyUI is still running and Fish is stopped**

Run:

```bash
docker ps --format '{{.Names}} {{.Status}}' | sort
```

Expected output includes `spider Up` and does not include `fish-s2-pro-server`.

- [ ] **Step 4: Copy the source FLAC into Comfy input**

Run:

```bash
cp /home/gavman/Downloads/fish_s2_pro_recorded_ref_00009.flac basedir/input/code205-gavin-fish-s2-pro.flac
```

Expected output: no terminal output and exit code 0.

- [ ] **Step 5: Convert the source audio to WAV for maximum node compatibility**

Run:

```bash
ffmpeg -y -i basedir/input/code205-gavin-fish-s2-pro.flac -ar 44100 -ac 1 -c:a pcm_s16le basedir/input/code205-gavin-fish-s2-pro.wav
```

Expected output includes:

```text
Output #0, wav, to 'basedir/input/code205-gavin-fish-s2-pro.wav'
```

- [ ] **Step 6: Verify prepared audio duration**

Run:

```bash
ffprobe -hide_banner -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 basedir/input/code205-gavin-fish-s2-pro.wav
```

Expected output is close to:

```text
24.195193
```

- [ ] **Step 7: Update GavLife with runtime preparation status**

Run:

```bash
cd /home/gavman/Documents/_ops && python3 scripts/kanban.py comment CODE-205 "Stopped fish-s2-pro-server and prepared Comfy audio inputs: code205-gavin-fish-s2-pro.flac and code205-gavin-fish-s2-pro.wav."
```

Expected output: a JSON object containing `"type": "localComment"`.

### Task 2: Gavin-Like Presenter Image Preparation

**Files:**
- Read: `/tmp/codex-clipboard-b6010c4d-eb29-4019-bb50-e72bd27a185b.png`
- Create: `basedir/input/code205-gavin-youtube-presenter.png`
- Read if present: `basedir/input/gavin_somers.jpg`
- No Git-tracked files change in this task.

**Interfaces:**
- Consumes: the user-provided Gavin portrait reference image.
- Produces: a 16:9 PNG source image named `code205-gavin-youtube-presenter.png` for the LTX `LoadImage` node.

- [ ] **Step 1: Confirm the reference image exists**

Run:

```bash
test -f /tmp/codex-clipboard-b6010c4d-eb29-4019-bb50-e72bd27a185b.png && file /tmp/codex-clipboard-b6010c4d-eb29-4019-bb50-e72bd27a185b.png
```

Expected output includes:

```text
PNG image data
```

- [ ] **Step 2: Create the presenter source image**

Use an image-generation or image-editing route to create:

```text
basedir/input/code205-gavin-youtube-presenter.png
```

The image must satisfy:

```text
16:9 landscape composition; realistic Gavin-like adult male presenter; waist-up or mid-torso framing; light blazer; pale shirt; bright simple indoor background; camera-facing; clear eyes; relaxed mouth; no hands visible.
```

Expected result: the file exists at `basedir/input/code205-gavin-youtube-presenter.png`.

- [ ] **Step 3: Verify the presenter image dimensions**

Run:

```bash
python3 - <<'PY'
from PIL import Image
path = "basedir/input/code205-gavin-youtube-presenter.png"
im = Image.open(path)
print(im.size)
ratio = im.size[0] / im.size[1]
print(round(ratio, 4))
PY
```

Expected output is a 16:9-ish image. For 1280x720:

```text
(1280, 720)
1.7778
```

- [ ] **Step 4: Visually inspect the presenter image**

Open the image from:

```text
/home/gavman/code/forks/comfy/basedir/input/code205-gavin-youtube-presenter.png
```

Acceptance criteria:

```text
Gavin-like likeness is plausible; no visible hand artifacts; face is front-facing; mouth is neutral or slightly relaxed; background is simple; shot feels suitable for a YouTube presenter.
```

- [ ] **Step 5: Update GavLife with image preparation status**

Run:

```bash
cd /home/gavman/Documents/_ops && python3 scripts/kanban.py comment CODE-205 "Prepared Gavin-like 16:9 presenter source image at basedir/input/code205-gavin-youtube-presenter.png."
```

Expected output: a JSON object containing `"type": "localComment"`.

### Task 3: Clone And Patch The LTX Workflow

**Files:**
- Read: `basedir/user/default/workflows/video/ltx2_3_talking_portrait_ia2v.json`
- Create: `basedir/user/default/workflows/video/code205_gavin_youtube_talking_head_ltx2_3.json`

**Interfaces:**
- Consumes: prepared input files from Tasks 1 and 2.
- Produces: a ComfyUI workflow JSON that points at `code205-gavin-youtube-presenter.png` and `code205-gavin-fish-s2-pro.wav`.

- [ ] **Step 1: Copy the baseline workflow**

Run:

```bash
cp basedir/user/default/workflows/video/ltx2_3_talking_portrait_ia2v.json basedir/user/default/workflows/video/code205_gavin_youtube_talking_head_ltx2_3.json
```

Expected output: no terminal output and exit code 0.

- [ ] **Step 2: Patch the workflow input and output widgets**

Run:

```bash
python3 - <<'PY'
import json
from pathlib import Path

path = Path("basedir/user/default/workflows/video/code205_gavin_youtube_talking_head_ltx2_3.json")
data = json.loads(path.read_text())

for node in data["nodes"]:
    if node.get("id") == 269 and node.get("type") == "LoadImage":
        node["widgets_values"][0] = "code205-gavin-youtube-presenter.png"
    if node.get("id") == 346 and node.get("type") == "LoadAudio":
        node["widgets_values"][0] = "code205-gavin-fish-s2-pro.wav"
    if node.get("id") == 341 and node.get("type") == "SaveVideo":
        node["widgets_values"][0] = "video/code205_gavin_youtube_talking_head_ltx2_3"

data.setdefault("extra", {})
data["extra"]["info"] = (
    "CODE-205 Gavin-like YouTube talking-head validation workflow. "
    "Inputs: code205-gavin-youtube-presenter.png and code205-gavin-fish-s2-pro.wav."
)

path.write_text(json.dumps(data, indent=2) + "\n")
PY
```

Expected output: no terminal output and exit code 0.

- [ ] **Step 3: Verify the workflow node values**

Run:

```bash
jq -r '.nodes[] | select(.id==269 or .id==346 or .id==341) | [.id,.type,(.widgets_values|tostring)] | @tsv' basedir/user/default/workflows/video/code205_gavin_youtube_talking_head_ltx2_3.json
```

Expected output:

```text
269	LoadImage	["code205-gavin-youtube-presenter.png","image"]
341	SaveVideo	["video/code205_gavin_youtube_talking_head_ltx2_3","auto","auto"]
346	LoadAudio	["code205-gavin-fish-s2-pro.wav",null,""]
```

- [ ] **Step 4: Validate JSON syntax**

Run:

```bash
jq empty basedir/user/default/workflows/video/code205_gavin_youtube_talking_head_ltx2_3.json
```

Expected output: no terminal output and exit code 0.

- [ ] **Step 5: Commit the workflow JSON**

Run:

```bash
git add basedir/user/default/workflows/video/code205_gavin_youtube_talking_head_ltx2_3.json
git commit -m "feat: add gavin youtube talking head workflow"
```

Expected output includes:

```text
feat: add gavin youtube talking head workflow
```

### Task 4: Run The First Validation Render

**Files:**
- Read: `basedir/user/default/workflows/video/code205_gavin_youtube_talking_head_ltx2_3.json`
- Read: `basedir/input/code205-gavin-youtube-presenter.png`
- Read: `basedir/input/code205-gavin-fish-s2-pro.wav`
- Create: `basedir/output/video/code205_gavin_youtube_talking_head_ltx2_3*.mp4` or the format emitted by the `SaveVideo` node.

**Interfaces:**
- Consumes: patched workflow JSON and prepared input assets.
- Produces: first validation video render for review.

- [ ] **Step 1: Confirm ComfyUI is reachable**

Run:

```bash
curl -fsS http://127.0.0.1:8188/system_stats >/tmp/code205-comfy-system-stats.json && python3 -m json.tool /tmp/code205-comfy-system-stats.json >/dev/null
```

Expected output: no terminal output and exit code 0.

- [ ] **Step 2: Load the workflow in ComfyUI**

Open:

```text
http://127.0.0.1:8188
```

Then load:

```text
/home/gavman/code/forks/comfy/basedir/user/default/workflows/video/code205_gavin_youtube_talking_head_ltx2_3.json
```

Expected result: the workflow opens with no missing-node errors.

- [ ] **Step 3: Confirm the visible input nodes**

In ComfyUI, confirm:

```text
LoadImage: code205-gavin-youtube-presenter.png
LoadAudio: code205-gavin-fish-s2-pro.wav
SaveVideo prefix: video/code205_gavin_youtube_talking_head_ltx2_3
```

Expected result: all three values match exactly.

- [ ] **Step 4: Queue the workflow**

In ComfyUI, click queue/run once.

Expected result:

```text
The run starts without immediate missing-file, missing-model, or missing-node errors.
```

- [ ] **Step 5: Monitor container logs for fatal errors**

Run while the workflow is active:

```bash
docker logs --tail 120 -f spider
```

Expected result:

```text
Progress logs continue until the SaveVideo node writes output. No CUDA out-of-memory traceback, missing model error, or missing input file error appears.
```

Stop following logs with `Ctrl-C` after the run completes or fails.

- [ ] **Step 6: Locate the output video**

Run:

```bash
find basedir/output -type f -path '*video*' -iname 'code205_gavin_youtube_talking_head_ltx2_3*' -printf '%TY-%Tm-%Td %TH:%TM %p\n' | sort | tail -5
```

Expected output includes at least one newly created video file.

- [ ] **Step 7: Probe the output video duration**

Run:

```bash
output_path="$(find basedir/output -type f -path '*video*' -iname 'code205_gavin_youtube_talking_head_ltx2_3*' -printf '%T@ %p\n' | sort -n | tail -1 | cut -d' ' -f2-)"
printf '%s\n' "$output_path"
ffprobe -hide_banner -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$output_path"
```

Expected output is close to:

```text
24.2
```

- [ ] **Step 8: Update GavLife with render status**

Run:

```bash
output_path="$(find /home/gavman/code/forks/comfy/basedir/output -type f -path '*video*' -iname 'code205_gavin_youtube_talking_head_ltx2_3*' -printf '%T@ %p\n' | sort -n | tail -1 | cut -d' ' -f2-)"
cd /home/gavman/Documents/_ops && python3 scripts/kanban.py comment CODE-205 "First Gavin-like LTX 2.3 talking-head validation render completed. Output path: ${output_path}."
```

Expected output: a JSON object containing `"type": "localComment"`.

### Task 5: Review And Decide The Next Iteration

**Files:**
- Read: generated video from Task 4.
- Create if written review is useful: `docs/superpowers/specs/2026-07-06-gavin-talking-head-workflow-review.md`

**Interfaces:**
- Consumes: validation render from Task 4.
- Produces: decision on whether to iterate image, tune workflow, or switch lip-sync path.

- [ ] **Step 1: Review the video against the four criteria**

Watch the output video and score each criterion as `pass`, `minor issue`, or `major issue`:

```text
Likeness: presenter is recognizably Gavin-like without uncanny distortion.
Lip-sync: mouth timing follows the Fish S2 Pro audio closely.
Stability: face, teeth, eyes, blazer, and background do not drift or flicker badly.
YouTube fit: framing feels like a credible presenter shot in landscape format.
```

- [ ] **Step 2: Capture the review decision**

Choose exact labels from `pass`, `minor issue`, and `major issue`, then run this command after editing only the four shell variable values:

```bash
likeness="pass"
lipsync="pass"
stability="pass"
youtube_fit="pass"
next_action="accept first pass"
cd /home/gavman/Documents/_ops && python3 scripts/kanban.py comment CODE-205 "Review decision for first Gavin-like talking-head render: Likeness=${likeness}; Lip-sync=${lipsync}; Stability=${stability}; YouTube fit=${youtube_fit}; Next action=${next_action}."
```

Expected output: a JSON object containing `"type": "localComment"`.

- [ ] **Step 3: Choose the next action**

Use this decision table:

```text
If likeness is a major issue: regenerate or improve basedir/input/code205-gavin-youtube-presenter.png before changing the video workflow.
If lip-sync is a major issue and likeness is acceptable: try a dedicated lip-sync workflow using the same presenter image and WAV.
If stability is a major issue: simplify the presenter image background and reduce workflow motion before raising resolution.
If all criteria pass or have only minor issues: keep this workflow as the reusable CODE-205 baseline.
```

- [ ] **Step 4: Commit any tracked review documentation**

If a review markdown file was created, run:

```bash
git add docs/superpowers/specs/2026-07-06-gavin-talking-head-workflow-review.md
git commit -m "docs: record gavin talking head render review"
```

Expected output includes:

```text
docs: record gavin talking head render review
```
