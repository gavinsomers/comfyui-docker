#!/bin/bash

# Install ComfyUI-nunchaku custom node
# https://nunchaku.tech/docs/ComfyUI-nunchaku/get_started/installation.html
# https://github.com/nunchaku-tech/ComfyUI-nunchaku

set -e

error_exit() {
  echo -n "!! ERROR: "
  echo $*
  echo "!! Exiting script (ID: $$)"
  exit 1
}

source /comfy/mnt/venv/bin/activate || error_exit "Failed to activate virtualenv"

## requires: 13-nunchaku.sh (nunchaku library must be installed first)
echo "Checking if nunchaku library is installed"
if ! pip3 show nunchaku &>/dev/null; then
  error_exit " !! nunchaku library not installed, please enable 13-nunchaku.sh first"
fi

# Determine the custom_nodes directory location
# Check for BASE_DIRECTORY first (priority), then fall back to ComfyUI directory
if [ ! -z "${BASE_DIRECTORY}" ] && [ "${BASE_DIRECTORY}" != "VALUE_TO_IGNORE" ]; then
  CUSTOM_NODES_DIR="${BASE_DIRECTORY}/custom_nodes"
  echo "Using BASE_DIRECTORY custom_nodes: ${CUSTOM_NODES_DIR}"
else
  CUSTOM_NODES_DIR="/comfy/mnt/ComfyUI/custom_nodes"
  echo "Using ComfyUI custom_nodes: ${CUSTOM_NODES_DIR}"
fi

# Create custom_nodes directory if it doesn't exist
if [ ! -d "${CUSTOM_NODES_DIR}" ]; then
  echo "Creating custom_nodes directory at ${CUSTOM_NODES_DIR}"
  mkdir -p "${CUSTOM_NODES_DIR}" || error_exit "Failed to create custom_nodes directory at ${CUSTOM_NODES_DIR}"
fi

cd "${CUSTOM_NODES_DIR}"

TARGET_DIR="ComfyUI-nunchaku"

if [ -d "${TARGET_DIR}" ]; then
  echo "ComfyUI-nunchaku already present at ${CUSTOM_NODES_DIR}/${TARGET_DIR}"
  echo "Delete it to force reinstallation: rm -rf ${CUSTOM_NODES_DIR}/${TARGET_DIR}"
  exit 0
fi

echo "Installing ComfyUI-nunchaku custom node"

# Clone the repository
git clone https://github.com/nunchaku-tech/ComfyUI-nunchaku.git || error_exit "Failed to clone ComfyUI-nunchaku"

cd "${TARGET_DIR}"

# Install requirements if requirements.txt exists
if [ -f requirements.txt ]; then
  echo "Installing ComfyUI-nunchaku requirements"
  pip3 install -r requirements.txt || error_exit "Failed to install ComfyUI-nunchaku requirements"
else
  echo "No requirements.txt found, skipping pip install"
fi

echo "ComfyUI-nunchaku custom node installed successfully"

exit 0