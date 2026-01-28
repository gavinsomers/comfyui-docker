#!/bin/bash

# Fix NumPy version for numba compatibility
# This script runs LAST (99-) to ensure numpy is pinned after all other installs
# numba requires NumPy 2.3 or less

set -e

error_exit() {
  echo -n "!! ERROR: "
  echo $*
  echo "!! Exiting script (ID: $$)"
  exit 1
}

source /comfy/mnt/venv/bin/activate || error_exit "Failed to activate virtualenv"

# Check current numpy version
NUMPY_VERSION=$(pip3 show numpy 2>/dev/null | grep "^Version:" | awk '{print $2}')
echo "Current NumPy version: ${NUMPY_VERSION}"

# Extract major and minor version
MAJOR=$(echo "${NUMPY_VERSION}" | cut -d. -f1)
MINOR=$(echo "${NUMPY_VERSION}" | cut -d. -f2)

# Check if version is 2.4 or higher (MAJOR >= 3, or MAJOR = 2 and MINOR >= 4)
if [ "${MAJOR}" -gt 2 ] || ([ "${MAJOR}" -eq 2 ] && [ "${MINOR}" -ge 4 ]); then
  echo "NumPy ${NUMPY_VERSION} is incompatible with numba. Downgrading to 2.3.x..."
  pip3 install --force-reinstall "numpy==2.3.0" || error_exit "Failed to downgrade numpy"
  echo "NumPy downgraded successfully"
else
  echo "NumPy ${NUMPY_VERSION} is compatible with numba. No action needed."
fi
