#!/bin/bash

# Install xformers
# - if a pip version is available, install it
# - else, compile from source (if src/${BUILD_BASE}/xformers is NOT already present)
#
#
# https://github.com/facebookresearch/xformers

echo "** Installing xformers**"

set -e

error_exit() {
  echo -n "!! ERROR: "
  echo $*
  echo "!! Exiting script (ID: $$)"
  exit 1
}

source /comfy/mnt/venv/bin/activate || error_exit "Failed to activate virtualenv"

cd /comfy/mnt
bb="venv/.build_base.txt"
if [ ! -f $bb ]; then error_exit "${bb} not found"; fi
BUILD_BASE=$(cat $bb)
# ubuntu24_cuda12.9
# extract CUDA version from build base
CUDA_VERSION=$(echo $BUILD_BASE | grep -oP 'cuda\d+\.\d+')
if [ -z "$CUDA_VERSION" ]; then error_exit "CUDA version not found in build base"; fi

echo "CUDA version: $CUDA_VERSION"
url=""
if [ "$CUDA_VERSION" == "cuda12.6" ]; then url="--index-url https://download.pytorch.org/whl/cu126"; fi
if [ "$CUDA_VERSION" == "cuda12.8" ]; then url="--index-url https://download.pytorch.org/whl/cu128"; fi
if [ "$CUDA_VERSION" == "cuda12.9" ]; then url="--index-url https://download.pytorch.org/whl/cu129"; fi

if [ ! -z "$url" ]; then 
  echo "Installing xformers from PyPI"
  pip3 install -U xformers $url || error_exit "Failed to install xformers"
  exit 0
fi

echo "CUDA version $CUDA_VERSION not supported, must compile from source" 

cd /comfy/mnt
bb="venv/.build_base.txt"
if [ ! -f $bb ]; then error_exit "${bb} not found"; fi
BUILD_BASE=$(cat $bb)


if [ ! -d src ]; then mkdir src; fi
cd src

mkdir -p ${BUILD_BASE}
if [ ! -d ${BUILD_BASE} ]; then error_exit "${BUILD_BASE} not found"; fi
cd ${BUILD_BASE}

dd="/comfy/mnt/src/${BUILD_BASE}/xformers"
if [ -d $dd ]; then
  echo "xformers source already present, you must delete it at $dd to force reinstallation"
  exit 0
fi

echo "Compiling xformers"

mkdir -p xformers
cd xformers
NUMPROC=$(nproc --all)
EXT_PARALLEL=4 NVCC_APPEND_FLAGS="--threads 8" MAX_JOBS=$NUMPROC pip3 install -v --no-build-isolation -U git+https://github.com/facebookresearch/xformers.git@main#egg=xformers || error_exit "Failed to install xformers"

exit 0
