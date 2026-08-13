#!/bin/bash

# Install SageAttention for optimized attention in video generation nodes

set -e

error_exit() {
  echo -n "!! ERROR: "
  echo "$*"
  echo "!! Exiting script (ID: $$)"
  exit 1
}

# shellcheck source=/dev/null
source /comfy/mnt/venv/bin/activate || error_exit "Failed to activate virtualenv"

echo "== Installing SageAttention =="

pip3 install sageattention || error_exit "Failed to install sageattention"

echo "== SageAttention installed =="

exit 0
