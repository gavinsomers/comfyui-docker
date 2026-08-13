#!/bin/bash

# Install runtime dependencies for ComfyUI-Qwen3-TTS.

set -e

error_exit() {
  echo -n "!! ERROR: "
  echo "$*"
  echo "!! Exiting script (ID: $$)"
  exit 1
}

# shellcheck source=/dev/null
source /comfy/mnt/venv/bin/activate || error_exit "Failed to activate virtualenv"

echo "== Checking Qwen3-TTS dependencies =="

QWEN3_TTS_DIR="/basedir/custom_nodes/ComfyUI-Qwen3-TTS"
QWEN3_TTS_REPO="https://github.com/DarioFT/ComfyUI-Qwen3-TTS.git"

if [ ! -d "$QWEN3_TTS_DIR" ]; then
  echo "Installing ComfyUI-Qwen3-TTS custom node..."
  git clone "$QWEN3_TTS_REPO" "$QWEN3_TTS_DIR" || error_exit "Failed to clone ComfyUI-Qwen3-TTS"
else
  echo "ComfyUI-Qwen3-TTS custom node already present: $QWEN3_TTS_DIR"
fi

if ! command -v sox >/dev/null 2>&1; then
  echo "Installing system sox binary..."
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    apt-get install -y sox libsox-fmt-all
  else
    error_exit "sox is missing and apt-get is unavailable"
  fi
else
  echo "sox binary already installed: $(sox --version)"
fi

if python3 -c "import qwen_tts" >/dev/null 2>&1; then
  echo "qwen_tts already importable."
else
  echo "Installing ComfyUI-Qwen3-TTS Python requirements..."
  pip3 install -r /basedir/custom_nodes/ComfyUI-Qwen3-TTS/requirements.txt || error_exit "Failed to install Qwen3-TTS requirements"
fi

python3 -c "import torch, torchaudio, transformers, qwen_tts; print(f'Qwen3-TTS deps ok: torch={torch.__version__}, torchaudio={torchaudio.__version__}, transformers={transformers.__version__}')"

exit 0
