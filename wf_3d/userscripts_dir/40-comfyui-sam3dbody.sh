#!/bin/bash

# Install ComfyUI-SAM3DBody dependencies
# Runs the install.py script from the custom node
#
# https://github.com/PozzettiAndrea/ComfyUI-SAM3DBody

set -e

error_exit() {
  echo -n "!! ERROR: "
  echo $*
  echo "!! Exiting script (ID: $$)"
  exit 1
}

source /comfy/mnt/venv/bin/activate || error_exit "Failed to activate virtualenv"

NODE_DIR="/comfy/mnt/custom_nodes/ComfyUI-SAM3DBody"

if [ ! -d "$NODE_DIR" ]; then
  echo "ComfyUI-SAM3DBody not found at $NODE_DIR, skipping installation"
  exit 0
fi

# Check if already installed by looking for a marker file or key dependency
if pip3 show sam_3d_body &>/dev/null || pip3 show pyrender &>/dev/null; then
  echo "ComfyUI-SAM3DBody dependencies appear to be already installed, skipping"
  exit 0
fi

echo "Installing ComfyUI-SAM3DBody dependencies..."

cd "$NODE_DIR"

# Run the install script
python3 install.py || error_exit "Failed to run install.py"

echo "ComfyUI-SAM3DBody installation complete"

exit 0
