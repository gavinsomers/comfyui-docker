#!/bin/bash

# Install nunchaku
# compile from source (if src/${BUILD_BASE}/nunchaku is NOT already present)
#
# https://github.com/nunchaku-tech/nunchaku

set -e

error_exit() {
  echo -n "!! ERROR: "
  echo $*
  echo "!! Exiting script (ID: $$)"
  exit 1
}

source /comfy/mnt/venv/bin/activate || error_exit "Failed to activate virtualenv"

## requires: 00-nvidiaDev,sh
echo "Checking if nvcc is available"
if ! command -v nvcc &> /dev/null; then
    error_exit " !! nvcc not found, canceling run"
fi

## requires: 10-pip3Dev.sh
if pip3 show setuptools &>/dev/null; then
  echo " ++ setuptools installed"
else
  error_exit " !! setuptools not installed, canceling run"
fi
if pip3 show ninja &>/dev/null; then
  echo " ++ ninja installed"
else
  error_exit " !! ninja not installed, canceling run"
fi

cd /comfy/mnt
bb="venv/.build_base.txt"
if [ ! -f $bb ]; then error_exit "${bb} not found"; fi
BUILD_BASE=$(cat $bb)


if [ ! -d src ]; then mkdir src; fi
cd src

mkdir -p ${BUILD_BASE}
if [ ! -d ${BUILD_BASE} ]; then error_exit "${BUILD_BASE} not found"; fi
cd ${BUILD_BASE}

dd="/comfy/mnt/src/${BUILD_BASE}/nunchaku"
if [ -d $dd ]; then
  echo "Nunchaku source already present, you must delete it at $dd to force reinstallation"
  exit 0
fi

echo "Compiling Nunchaku"

git clone --recurse-submodules https://github.com/nunchaku-tech/nunchaku.git
cd nunchaku
NUMPROC=$(nproc --all)
# Install only the base nunchaku package without dev/docs extras for faster installation
EXT_PARALLEL=4 NVCC_APPEND_FLAGS="--threads 8" MAX_JOBS=$NUMPROC pip3 install -e . || error_exit "Failed to install Nunchaku"

exit 0
