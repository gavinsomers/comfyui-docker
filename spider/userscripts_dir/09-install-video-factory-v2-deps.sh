#!/bin/bash

# Verify and install the approved LivePortrait -> LatentSync 1.6 V2 presenter stack.
# Face Alignment/BlazeFace replaces InsightFace in the pinned LatentSync patch.

set -e

error_exit() {
  echo -n "!! ERROR: "
  echo "$*"
  echo "!! Exiting script (ID: $$)"
  exit 1
}

VENV_ACTIVATE="${VIDEO_FACTORY_V2_VENV_ACTIVATE:-/comfy/mnt/venv/bin/activate}"
CUSTOM_NODES_DIR="${VIDEO_FACTORY_V2_CUSTOM_NODES_DIR:-/basedir/custom_nodes}"
MODELS_DIR="${VIDEO_FACTORY_V2_MODELS_DIR:-/basedir/models}"
LATENTSYNC_RUNTIME="${VIDEO_FACTORY_LATENTSYNC_RUNTIME:-/comfy/mnt/latentsync-1.6}"
# shellcheck source=/dev/null
source "$VENV_ACTIVATE" || error_exit "Failed to activate virtualenv"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/video_factory_v2_deps.py" check-nodes "$CUSTOM_NODES_DIR" \
  || error_exit "V2 LivePortrait provider verification failed"

echo "== V2 LivePortrait provider verified =="
# LivePortrait's bundled FaceAlignment dynamically imports from the provider's
# top-level package name, so the external custom-node root must be importable.
CUSTOM_NODES_DIR="$CUSTOM_NODES_DIR" python3 - <<'PY'
import os
import site
from pathlib import Path

site_packages = Path(site.getsitepackages()[0])
pth = site_packages / "video_factory_v2_custom_nodes.pth"
pth.write_text(str(Path(os.environ["CUSTOM_NODES_DIR"]).resolve()) + "\n", encoding="utf-8")
PY
mapfile -t requirements < <(
  python3 "$SCRIPT_DIR/video_factory_v2_deps.py" requirements
)
if python3 "$SCRIPT_DIR/video_factory_v2_deps.py" check >/dev/null 2>&1; then
  echo "== V2 LivePortrait Python dependencies already installed =="
else
  pip3 install "${requirements[@]}" \
    || error_exit "Failed to install V2 LivePortrait Python dependencies"
  python3 "$SCRIPT_DIR/video_factory_v2_deps.py" check \
    || error_exit "V2 LivePortrait dependency verification failed"
fi

python3 "$SCRIPT_DIR/video_factory_v2_deps.py" ensure-landmark "$MODELS_DIR" \
  || error_exit "LivePortrait landmark model verification failed"

if python3 "$SCRIPT_DIR/latentsync16_deps.py" check "$LATENTSYNC_RUNTIME" >/dev/null 2>&1; then
  echo "== Pinned LatentSync 1.6 runtime already installed =="
else
  echo "== Installing pinned LatentSync 1.6 runtime =="
  python3 "$SCRIPT_DIR/latentsync16_deps.py" install "$LATENTSYNC_RUNTIME" \
    || error_exit "LatentSync 1.6 runtime installation failed"
fi
python3 "$SCRIPT_DIR/latentsync16_deps.py" check "$LATENTSYNC_RUNTIME" \
  || error_exit "LatentSync 1.6 runtime verification failed"
echo "== V2 presenter dependencies ready; restart ComfyUI to register LivePortrait nodes =="
