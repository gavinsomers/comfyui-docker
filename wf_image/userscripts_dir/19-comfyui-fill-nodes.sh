#!/bin/bash

# Install/update ComfyUI_Fill-Nodes custom node dependencies
# https://github.com/filliptm/ComfyUI_Fill-Nodes

set -e

error_exit() {
  echo -n "!! ERROR: "
  echo $*
  echo "!! Exiting script (ID: $$)"
  exit 1
}

source /comfy/mnt/venv/bin/activate || error_exit "Failed to activate virtualenv"

# Debug: show BASE_DIRECTORY value
echo "DEBUG: BASE_DIRECTORY='${BASE_DIRECTORY}'"

# Determine the custom_nodes directory location
# Check for BASE_DIRECTORY first (priority), then fall back to ComfyUI directory
if [ -n "${BASE_DIRECTORY}" ] && [ "${BASE_DIRECTORY}" != "VALUE_TO_IGNORE" ]; then
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

TARGET_DIR="ComfyUI_Fill-Nodes"

if [ -d "${TARGET_DIR}" ]; then
  echo "ComfyUI_Fill-Nodes already present at ${CUSTOM_NODES_DIR}/${TARGET_DIR}"
  cd "${TARGET_DIR}"
  
  # Always install requirements to ensure dependencies are up to date
  if [ -f requirements.txt ]; then
    echo "Installing/updating ComfyUI_Fill-Nodes requirements"
    pip3 install -r requirements.txt || error_exit "Failed to install ComfyUI_Fill-Nodes requirements"
  fi
  
  echo "ComfyUI_Fill-Nodes dependencies installed/updated"
  exit 0
else
  echo "ComfyUI_Fill-Nodes not found at ${CUSTOM_NODES_DIR}/${TARGET_DIR}"
  echo "Skipping - ComfyUI_Fill-Nodes not installed"
  # Exit successfully to allow container to continue
  exit 0
fi