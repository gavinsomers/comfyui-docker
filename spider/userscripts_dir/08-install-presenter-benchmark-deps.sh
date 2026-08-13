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

PRESENTER_BENCHMARK_VENV_ACTIVATE="${PRESENTER_BENCHMARK_VENV_ACTIVATE:-/comfy/mnt/venv/bin/activate}"
PRESENTER_BENCHMARK_CUSTOM_NODES_DIR="${PRESENTER_BENCHMARK_CUSTOM_NODES_DIR:-/basedir/custom_nodes}"
# shellcheck source=/dev/null
source "$PRESENTER_BENCHMARK_VENV_ACTIVATE" || error_exit "Failed to activate virtualenv"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/presenter_benchmark_deps.py" \
  check-nodes "$PRESENTER_BENCHMARK_CUSTOM_NODES_DIR" \
  || error_exit "LongCat custom-node provider verification failed"
echo "== Presenter benchmark custom-node providers verified =="

requirements_output="$(python3 "$SCRIPT_DIR/presenter_benchmark_deps.py" requirements)" \
  || error_exit "Failed to read presenter benchmark requirements"
mapfile -t benchmark_requirements <<<"$requirements_output"

if python3 "$SCRIPT_DIR/presenter_benchmark_deps.py" check >/dev/null 2>&1; then
  echo "== Presenter benchmark dependencies already installed =="
else
  echo "== Installing presenter benchmark dependencies =="
  pip3 install "${benchmark_requirements[@]}" \
    || error_exit "Failed to install presenter benchmark dependencies"

  python3 "$SCRIPT_DIR/presenter_benchmark_deps.py" check \
    || error_exit "Presenter benchmark dependency verification failed"
  echo "== Presenter benchmark dependencies installed =="
fi

exit 0
