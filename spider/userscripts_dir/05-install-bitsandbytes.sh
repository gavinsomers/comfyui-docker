#!/bin/bash

# Install bitsandbytes for VibeVoice 4-bit quantization support

set -e

error_exit() {
  echo -n "!! ERROR: "
  echo $*
  echo "!! Exiting script (ID: $$)"
  exit 1
}

source /comfy/mnt/venv/bin/activate || error_exit "Failed to activate virtualenv"

echo "== Installing bitsandbytes =="

pip3 install -U "bitsandbytes>=0.46.1" || error_exit "Failed to install bitsandbytes"

echo "== bitsandbytes installed =="

exit 0
