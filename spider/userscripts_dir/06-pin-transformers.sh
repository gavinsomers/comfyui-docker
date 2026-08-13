#!/bin/bash

# Pin transformers to 4.x for VibeVoice-ComfyUI and QwenVL compatibility
# Also pins huggingface-hub to avoid upgrade/downgrade cycle on restart

set -e

error_exit() {
  echo -n "!! ERROR: "
  echo "$*"
  echo "!! Exiting script (ID: $$)"
  exit 1
}

# shellcheck source=/dev/null
source /comfy/mnt/venv/bin/activate || error_exit "Failed to activate virtualenv"

echo "== Pinning transformers and huggingface-hub =="

pip3 install "transformers>=4.57.0,<5.0.0" "huggingface-hub>=0.34.0,<1.0" || error_exit "Failed to install transformers"

echo "== transformers pinned =="

exit 0
