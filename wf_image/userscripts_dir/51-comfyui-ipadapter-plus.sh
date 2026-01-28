#!/bin/bash

# Install ComfyUI_IPAdapter_plus dependencies
# Provides: IPAdapterApply, IPAdapterModelLoader, and other IP-Adapter nodes
#
# https://github.com/cubiq/ComfyUI_IPAdapter_plus

set -e

error_exit() {
  echo -n "!! ERROR: "
  echo $*
  echo "!! Exiting script (ID: $$)"
  exit 1
}

source /comfy/mnt/venv/bin/activate || error_exit "Failed to activate virtualenv"

NODE_DIR="/comfy/mnt/custom_nodes/ComfyUI_IPAdapter_plus"

if [ ! -d "$NODE_DIR" ]; then
  echo "ComfyUI_IPAdapter_plus not found at $NODE_DIR, skipping installation"
  exit 0
fi

# Check if already installed by looking for insightface (key dependency)
if pip3 show insightface &>/dev/null; then
  echo "ComfyUI_IPAdapter_plus dependencies appear to be already installed, skipping"
  exit 0
fi

echo "Installing ComfyUI_IPAdapter_plus dependencies..."

cd "$NODE_DIR"

# Install requirements if they exist
if [ -f "requirements.txt" ]; then
  pip3 install -r requirements.txt || error_exit "Failed to install requirements.txt"
fi

# IPAdapter_plus commonly needs insightface for FaceID features
pip3 install insightface onnxruntime-gpu || echo "Note: insightface/onnxruntime-gpu may already be installed"

echo "ComfyUI_IPAdapter_plus installation complete"

exit 0
