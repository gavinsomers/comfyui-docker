#!/bin/bash

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

## requires: 20-sageattention.sh
if pip3 show sageattention &>/dev/null; then
  echo " ++ sageattention installed"
else
  error_exit " !! sageattention not installed, canceling run"
fi

## requires: 21-triton.sh
if pip3 show triton &>/dev/null; then
  echo " ++ triton installed"
else
  error_exit " !! triton not installed, canceling run"
fi

# HiDream is a custom node, no need to compile, we just needed to confirm dependencies are met

exit 0
