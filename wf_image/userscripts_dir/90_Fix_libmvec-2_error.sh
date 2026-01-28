#!/bin/bash

# ==============================================================================
#           Fix simsimd ImportError for Ubuntu 24 Docker Containers
# ==============================================================================
#
# This script resolves the "ImportError: libmvec-2-06864e43.28.so: cannot open
# shared object file" that can occur when trying to install e.g. Nunchaku and 
# PulID nodes.
# This error occurs because a pre-compiled Python package, simsimd, is 
# incompatible with the system libraries inside the Ubuntu 24 Docker container.
# It forces pip to reinstall the 'simsimd' package from its source code. 
# This ensures it's compiled against the correct system libraries within the 
# container. This script is built to be easily tweaked to force any package to
# be built from source, see PACKAGE_TO_FIX in Configuration.
#
# This script is designed to be run inside your ComfyUI Docker container.
# 
# WARNING: RUN THIS SCRIPT AT YOUR OWN RISK. NO WARRANTY GIVEN. NO SUPPORT.
#
# ==============================================================================

# --- Configuration ---
# Set the path to your virtual environment's activation script.
# This should match the venv used by your ComfyUI instance.
VENV_ACTIVATE_PATH="/comfy/mnt/venv/bin/activate"

# The package that needs to be recompiled.
PACKAGE_TO_FIX="simsimd"

# --- Main Script ---

echo "--- Starting simsimd Re-compilation Fix ---"

# 1. Check if the virtual environment activation script exists
if [ ! -f "$VENV_ACTIVATE_PATH" ]; then
  echo "!! CRITICAL ERROR: Virtual environment not found at '$VENV_ACTIVATE_PATH'"
  echo "!! Please check the path and ensure it's correct."
  echo "!! Exiting script."
  exit 1
fi

# 2. Activate the virtual environment
# shellcheck source=/dev/null
source "$VENV_ACTIVATE_PATH"
if [ $? -ne 0 ]; then
    echo "!! CRITICAL ERROR: Failed to activate virtualenv at '$VENV_ACTIVATE_PATH'"
    echo "!! Exiting script."
    exit 1
fi
echo "++ Virtual environment activated successfully."

# 3. Re-install the package from source
echo "-- Attempting to reinstall '$PACKAGE_TO_FIX' from source..."
echo "   This may take a few moments as it needs to be compiled."

# The command to force reinstallation without using pre-built binaries.
pip3 install --force-reinstall --no-binary :all: "$PACKAGE_TO_FIX"

# 4. Check the result of the installation
if [ $? -eq 0 ]; then
    echo "++ Successfully re-installed '$PACKAGE_TO_FIX'."
    echo "++ The ImportError should now be resolved."
    echo "++ Please restart your ComfyUI service for the changes to take effect."
else
    echo "!! FAILED to reinstall '$PACKAGE_TO_FIX'."
    echo "!! An error occurred during the compilation/installation process."
    echo "!! You may be missing build tools like 'build-essential' or 'python3-dev'."
    echo "!! Try running: apt-get update && apt-get install -y build-essential python3-dev"
    echo "!! And then run this script again."
    exit 1
fi

echo "--- Fix script finished ---"
exit 0
