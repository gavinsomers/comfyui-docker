#!/bin/bash

set -e 

error_exit() {
  echo -n "!! ERROR: "
  echo $*
  echo "!! Exiting script (ID: $$)"
  exit 1
}

source /comfy/mnt/venv/bin/activate || error_exit "Failed to activate virtualenv"

min_torch_version="2.7.0"
cuda_wheel="cu128"

# bad example: python3 -c 'import torch; print(f"{torch.__version__}")'
# returns: 2.7.0+cu126
# we want cu128
# -> check against both the version and the cuda version

must_install=0 # increment if torch needs to be installed
if pip3 show torch &>/dev/null; then
    # if torch is installed, check the version
    full_torch_version=$(python3 -c 'import torch; print(f"{torch.__version__}")')

    # Split version and cuda
    torch_version=$(echo "$full_torch_version" | awk -F'+' '{print $1}')
    cuda_version=$(echo "$full_torch_version" | awk -F'+' '{print $2}')

    echo "PyTorch is installed with version $full_torch_version -- Torch: $torch_version, CUDA: $cuda_version"
    if [ "A$cuda_version" != "A$cuda_wheel" ]; then
        echo "Torch CUDA version $cuda_version does not match required version $cuda_wheel, need to install"
        must_install=$((must_install+1))
    fi

    # Check if the version matches the minimum version
    if [ "$(printf '%s\n' "$torch_version" "$min_torch_version" | sort -V | head -n1)" != "$min_torch_version" ]; then
        echo "Torch version $torch_version is below minimum version $min_torch_version, need to install"
        must_install=$((must_install+1))
    fi
else
    echo "PyTorch is not installed, need to install"
    must_install=$((must_install+1))
fi

if [ $must_install -eq 0 ]; then
  echo "Torch is already installed or the version is up to date (version $torch_version), skipping installation"
  exit 0
fi

pip3 install -U --trusted-host pypi.org --trusted-host files.pythonhosted.org "torch>=${min_torch_version}" torchvision torchaudio --index-url https://download.pytorch.org/whl/${cuda_wheel}

exit 0
