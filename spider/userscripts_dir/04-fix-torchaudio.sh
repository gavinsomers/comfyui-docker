#!/bin/bash

# Pin torchaudio to match torch version to avoid ABI mismatch
# torch 2.10 + torchaudio 2.11 causes: undefined symbol: torch_dtype_float4_e2m1fn_x2

set -e

source /comfy/mnt/venv/bin/activate || exit 1

install_matching_torch_stack() {
  echo "Pinning torch, torchvision, and torchaudio to a matching CUDA 12.8 set."
  pip3 install --force-reinstall \
    "torch==2.11.0+cu128" \
    "torchvision==0.26.0+cu128" \
    "torchaudio==2.11.0+cu128" \
    --index-url https://download.pytorch.org/whl/cu128
}

TORCH_VERSION=$(python3 -c "import importlib.metadata; print(importlib.metadata.version('torch').split('+')[0])")
TORCHAUDIO_VERSION=$(python3 -c "import importlib.metadata; print(importlib.metadata.version('torchaudio').split('+')[0])")

echo "== Checking torch/torchaudio version match =="
echo "torch: $TORCH_VERSION, torchaudio: $TORCHAUDIO_VERSION"

if [ "$TORCH_VERSION" != "$TORCHAUDIO_VERSION" ]; then
  echo "Version mismatch detected. Installing torchaudio==$TORCH_VERSION..."
  if pip3 install "torchaudio==${TORCH_VERSION}+cu128" --index-url https://download.pytorch.org/whl/cu128; then
    echo "torchaudio pinned to $TORCH_VERSION"
  else
    echo "torchaudio ${TORCH_VERSION}+cu128 is not available."
    install_matching_torch_stack
  fi
else
  echo "Versions match, no action needed."
fi

if ! python3 -c "import torch; import torchaudio; print(f'torch import ok: {torch.__version__}, torchaudio: {torchaudio.__version__}')" ; then
  echo "Torch import failed after version check."
  install_matching_torch_stack
  python3 -c "import torch; import torchaudio; print(f'torch import ok: {torch.__version__}, torchaudio: {torchaudio.__version__}')"
fi

exit 0
