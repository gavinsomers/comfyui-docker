#!/bin/bash

set -e

error_exit() {
  echo -n "!! ERROR: "
  echo $*
  echo "!! Exiting script (ID: $$)"
  exit 1
}

source /comfy/mnt/venv/bin/activate || error_exit "Failed to activate virtualenv"

python3 -m ensurepip --upgrade || error_exit "Failed to upgrade pip"
python3 -m pip install --upgrade setuptools || error_exit "Failed to upgrade setuptools"

pip3 install ninja cmake wheel pybind11 packaging || error_exit "Failed to install build dependencies"

exit 0
