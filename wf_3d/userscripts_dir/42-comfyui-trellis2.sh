#!/bin/bash

# Install ComfyUI-TRELLIS2 dependencies
# Runs the install.py script from the custom node which automatically
# detects PyTorch/CUDA versions and installs pre-built wheels for CUDA extensions
# (nvdiffrast, flex_gemm, cumesh, o_voxel, nvdiffrec_render, flash_attn)
#
# https://github.com/PozzettiAndrea/ComfyUI-TRELLIS2

set -e

error_exit() {
  echo -n "!! ERROR: "
  echo $*
  echo "!! Exiting script (ID: $$)"
  exit 1
}

source /comfy/mnt/venv/bin/activate || error_exit "Failed to activate virtualenv"

# Check multiple possible locations for the node directory
NODE_DIR=""
for dir in "/basedir/custom_nodes/ComfyUI-TRELLIS2" "/comfy/mnt/custom_nodes/ComfyUI-TRELLIS2"; do
  if [ -d "$dir" ]; then
    NODE_DIR="$dir"
    break
  fi
done

if [ -z "$NODE_DIR" ]; then
  echo "ComfyUI-TRELLIS2 not found, skipping installation"
  exit 0
fi

echo "Found ComfyUI-TRELLIS2 at $NODE_DIR"

# Check if already installed by looking for key CUDA extensions
# flex_gemm is the primary CUDA extension that's required
if python3 -c "import flex_gemm" &>/dev/null && \
   python3 -c "import cumesh" &>/dev/null && \
   python3 -c "import nvdiffrast" &>/dev/null; then
  echo "ComfyUI-TRELLIS2 CUDA extensions appear to be already installed, skipping"
  exit 0
fi

echo "Installing ComfyUI-TRELLIS2 dependencies..."
echo "PyTorch version: $(python3 -c 'import torch; print(torch.__version__)')"
echo "CUDA version: $(python3 -c 'import torch; print(torch.version.cuda)')"

cd "$NODE_DIR"

# Run the install script
python3 install.py || error_exit "Failed to run install.py"

echo "ComfyUI-TRELLIS2 installation complete"

exit 0
