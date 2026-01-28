#!/bin/bash

# Install onnxruntime-gpu from PyPI
#
# https://onnxruntime.ai/
# https://github.com/microsoft/onnxruntime

set -e

error_exit() {
  echo -n "!! ERROR: "
  echo $*
  echo "!! Exiting script (ID: $$)"
  exit 1
}

source /comfy/mnt/venv/bin/activate || error_exit "Failed to activate virtualenv"

pip3 install onnxruntime-gpu || error_exit "Failed to install onnxruntime-gpu"

exit 0
