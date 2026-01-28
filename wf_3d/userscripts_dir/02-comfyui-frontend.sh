#!/bin/bash

# Update ComfyUI frontend package to match latest backend
# Fixes "Frontend version X is outdated. Backend requires Y or higher"

set -e

error_exit() {
  echo -n "!! ERROR: "
  echo $*
  echo "!! Exiting script (ID: $$)"
  exit 1
}

source /comfy/mnt/venv/bin/activate || error_exit "Failed to activate virtualenv"

echo "== Upgrading ComfyUI frontend package =="

# Upgrade the frontend package to latest version
pip3 install --upgrade comfyui-frontend-package || error_exit "Failed to upgrade comfyui-frontend-package"

echo "== Frontend package updated =="
pip3 show comfyui-frontend-package | grep Version

exit 0
