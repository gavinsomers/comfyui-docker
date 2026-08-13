#!/bin/bash

# Install dependencies used by the optional LongCat Avatar benchmark workflow.
# LTX 2.5 presenter generation uses ComfyUI core nodes and does not need these.

set -e

error_exit() {
  echo -n "!! ERROR: "
  echo "$*"
  echo "!! Exiting script (ID: $$)"
  exit 1
}

# shellcheck source=/dev/null
source /comfy/mnt/venv/bin/activate || error_exit "Failed to activate virtualenv"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
requirements_output="$(python3 "$SCRIPT_DIR/presenter_benchmark_deps.py" requirements)" \
  || error_exit "Failed to read presenter benchmark requirements"
mapfile -t benchmark_requirements <<<"$requirements_output"

if python3 "$SCRIPT_DIR/presenter_benchmark_deps.py" check >/dev/null 2>&1; then
  echo "== Presenter benchmark dependencies already installed =="
  exit 0
fi

echo "== Installing presenter benchmark dependencies =="
pip3 install "${benchmark_requirements[@]}" \
  || error_exit "Failed to install presenter benchmark dependencies"

python3 "$SCRIPT_DIR/presenter_benchmark_deps.py" check \
  || error_exit "Presenter benchmark dependency verification failed"
echo "== Presenter benchmark dependencies installed =="

exit 0
