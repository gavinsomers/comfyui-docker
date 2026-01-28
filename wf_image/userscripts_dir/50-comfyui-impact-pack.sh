#!/bin/bash

# Install ComfyUI-Impact-Pack dependencies
# Provides: Integer, ImpactInt, and many other utility nodes
#
# https://github.com/ltdrdata/ComfyUI-Impact-Pack

set -e

error_exit() {
  echo -n "!! ERROR: "
  echo $*
  echo "!! Exiting script (ID: $$)"
  exit 1
}

source /comfy/mnt/venv/bin/activate || error_exit "Failed to activate virtualenv"

NODE_DIR="/comfy/mnt/custom_nodes/ComfyUI-Impact-Pack"

if [ ! -d "$NODE_DIR" ]; then
  echo "ComfyUI-Impact-Pack not found at $NODE_DIR, skipping installation"
  exit 0
fi

# Check if already installed by looking for a key dependency
if pip3 show segment-anything &>/dev/null && pip3 show ultralytics &>/dev/null; then
  echo "ComfyUI-Impact-Pack dependencies appear to be already installed, skipping"
  exit 0
fi

echo "Installing ComfyUI-Impact-Pack dependencies..."

cd "$NODE_DIR"

# Run the install script if it exists
if [ -f "install.py" ]; then
  python3 install.py || error_exit "Failed to run install.py"
fi

# Install requirements if they exist
if [ -f "requirements.txt" ]; then
  pip3 install -r requirements.txt || error_exit "Failed to install requirements.txt"
fi

echo "ComfyUI-Impact-Pack installation complete"

exit 0
