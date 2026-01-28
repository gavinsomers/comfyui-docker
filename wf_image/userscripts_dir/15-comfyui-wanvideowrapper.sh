#!/bin/bash

# Install/update ComfyUI-WanVideoWrapper custom node
# https://github.com/kijai/ComfyUI-WanVideoWrapper

set -e

error_exit() {
  echo -n "!! ERROR: "
  echo $*
  echo "!! Exiting script (ID: $$)"
  exit 1
}

source /comfy/mnt/venv/bin/activate || error_exit "Failed to activate virtualenv"

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

TARGET_DIR="ComfyUI-WanVideoWrapper"

if [ -d "${TARGET_DIR}" ]; then
  echo "ComfyUI-WanVideoWrapper already present at ${CUSTOM_NODES_DIR}/${TARGET_DIR}"
  cd "${TARGET_DIR}"
  
  # Always install requirements to ensure dependencies are up to date
  if [ -f requirements.txt ]; then
    echo "Installing/updating ComfyUI-WanVideoWrapper requirements"
    pip3 install -r requirements.txt || error_exit "Failed to install ComfyUI-WanVideoWrapper requirements"
  fi
  
  echo "ComfyUI-WanVideoWrapper dependencies installed/updated"
  exit 0
fi

echo "Installing ComfyUI-WanVideoWrapper custom node"

# Clone the repository
git clone https://github.com/kijai/ComfyUI-WanVideoWrapper.git || error_exit "Failed to clone ComfyUI-WanVideoWrapper"

cd "${TARGET_DIR}"

# Install requirements if requirements.txt exists
if [ -f requirements.txt ]; then
  echo "Installing ComfyUI-WanVideoWrapper requirements"
  pip3 install -r requirements.txt || error_exit "Failed to install ComfyUI-WanVideoWrapper requirements"
else
  echo "No requirements.txt found, skipping pip install"
fi

echo "ComfyUI-WanVideoWrapper custom node installed successfully"

exit 0
