#!/bin/bash

# Install ComfyUI-SAM3DObjects dependencies
# Runs the install.py script from the custom node
#
# https://github.com/PozzettiAndrea/ComfyUI-SAM3DObjects

set -e

error_exit() {
  echo -n "!! ERROR: "
  echo $*
  echo "!! Exiting script (ID: $$)"
  exit 1
}

source /comfy/mnt/venv/bin/activate || error_exit "Failed to activate virtualenv"

NODE_DIR="/basedir/custom_nodes/ComfyUI-SAM3DObjects"

if [ ! -d "$NODE_DIR" ]; then
  echo "ComfyUI-SAM3DObjects not found at $NODE_DIR, skipping installation"
  exit 0
fi

# Check if already installed by looking for key dependency (sam3d_objects)
if pip3 show sam3d-objects &>/dev/null; then
  echo "ComfyUI-SAM3DObjects dependencies appear to be already installed, skipping"
  exit 0
fi

echo "Installing ComfyUI-SAM3DObjects dependencies..."

cd "$NODE_DIR"

# Run the install script
python3 install.py || error_exit "Failed to run install.py"

echo "ComfyUI-SAM3DObjects installation complete"

exit 0
