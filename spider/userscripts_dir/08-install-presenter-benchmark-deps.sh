#!/bin/bash

# Install dependencies used by the optional LongCat Avatar benchmark workflow.
# LTX 2.5 presenter generation uses ComfyUI core nodes and does not need these.

set -e

error_exit() {
  echo -n "!! ERROR: "
  echo "$*"
  echo "!! Exiting script (ID: $$)"
  exit 1
}

# shellcheck source=/dev/null
source /comfy/mnt/venv/bin/activate || error_exit "Failed to activate virtualenv"

if python3 -c "import cv2, diffusers, ftfy, gguf, imageio_ffmpeg, matplotlib, mss, peft, pyloudnorm, rotary_embedding_torch" >/dev/null 2>&1; then
  echo "== Presenter benchmark dependencies already installed =="
  exit 0
fi

echo "== Installing presenter benchmark dependencies =="
pip3 install \
  "ftfy" \
  "diffusers>=0.33.0" \
  "peft>=0.17.0" \
  "pyloudnorm" \
  "gguf>=0.17.1" \
  "opencv-python-headless" \
  "rotary_embedding_torch" \
  "imageio-ffmpeg" \
  "color-matcher" \
  "matplotlib" \
  "mss" || error_exit "Failed to install presenter benchmark dependencies"

python3 -c "import cv2, diffusers, ftfy, gguf, imageio_ffmpeg, matplotlib, mss, peft, pyloudnorm, rotary_embedding_torch"
echo "== Presenter benchmark dependencies installed =="

exit 0
