#!/bin/bash

# Install ComfyUI-QwenImageLoraLoader dependencies
# Provides: NunchakuQwenImageLoraLoader, NunchakuQwenImageLoraStack nodes
#
# https://github.com/ussoewwin/ComfyUI-QwenImageLoraLoader

set -e

error_exit() {
  echo -n "!! ERROR: "
  echo $*
  echo "!! Exiting script (ID: $$)"
  exit 1
}

source /comfy/mnt/venv/bin/activate || error_exit "Failed to activate virtualenv"

NODE_DIR="/comfy/mnt/custom_nodes/ComfyUI-QwenImageLoraLoader"

if [ ! -d "$NODE_DIR" ]; then
  echo "ComfyUI-QwenImageLoraLoader not found at $NODE_DIR, skipping installation"
  exit 0
fi

# Check if already installed by looking for nunchaku (key dependency)
if pip3 show nunchaku &>/dev/null; then
  echo "ComfyUI-QwenImageLoraLoader dependencies appear to be already installed, skipping"
  exit 0
fi

echo "Installing ComfyUI-QwenImageLoraLoader dependencies..."

# Install nunchaku package
pip3 install nunchaku || error_exit "Failed to install nunchaku"

echo "ComfyUI-QwenImageLoraLoader installation complete"

exit 0