#!/bin/bash

# Switch ComfyUI to nightly (master) branch on startup
# This script will either:
# 1. Update existing git repo to latest master
# 2. Or clone fresh from GitHub if not a git repo
#
# NOTE: If running as a different user than the file owner,
# git operations may fail. In that case, run updates from the host.

set -e

error_exit() {
  echo -n "!! ERROR: "
  echo $*
  echo "!! Exiting script (ID: $$)"
  exit 1
}

warn_skip() {
  echo "!! WARNING: $*"
  echo "!! Skipping git update - run from host if needed"
}

COMFYUI_DIR="/comfy/mnt/ComfyUI"
COMFYUI_REPO="https://github.com/comfyanonymous/ComfyUI.git"

echo "== ComfyUI Nightly Updater =="

# Add safe.directory config to avoid ownership warnings
git config --global --add safe.directory "$COMFYUI_DIR" 2>/dev/null || true

if [ -d "$COMFYUI_DIR/.git" ]; then
  # Git repo exists - check if we can write to it
  echo "Git repo found, checking permissions..."

  cd "$COMFYUI_DIR" || error_exit "Failed to cd to ComfyUI directory"

  # Test if we can write to .git directory
  if ! touch "$COMFYUI_DIR/.git/test_write" 2>/dev/null; then
    warn_skip "Cannot write to .git directory (permission denied)"
    echo "Current version:"
    git log -1 --oneline 2>/dev/null || echo "unknown"
    exit 0
  fi
  rm -f "$COMFYUI_DIR/.git/test_write"

  echo "Updating to latest master..."

  # Fetch all branches
  git fetch origin || error_exit "Failed to fetch from origin"

  # Get current branch
  CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
  echo "Current branch: $CURRENT_BRANCH"

  # Switch to master if not already on it
  if [ "$CURRENT_BRANCH" != "master" ]; then
    echo "Switching from $CURRENT_BRANCH to master..."
    git checkout master || error_exit "Failed to checkout master branch"
  fi

  # Pull latest changes
  echo "Pulling latest nightly changes..."
  git pull origin master || error_exit "Failed to pull latest changes"

elif [ -d "$COMFYUI_DIR" ]; then
  # Directory exists but no .git - need to convert to git repo
  echo "ComfyUI exists but not a git repo. Converting to git repo..."

  cd "$COMFYUI_DIR" || error_exit "Failed to cd to ComfyUI directory"

  # Initialize git repo
  git init || error_exit "Failed to initialize git repo"
  git remote add origin "$COMFYUI_REPO" || error_exit "Failed to add remote"

  # Fetch master branch
  echo "Fetching from GitHub..."
  git fetch origin master || error_exit "Failed to fetch master"

  # Hard reset to latest master (overwrites all files with latest)
  echo "Hard resetting to latest master (this will update all files)..."
  git reset --hard origin/master || error_exit "Failed to reset to master"

  # Set tracking branch
  git branch --set-upstream-to=origin/master master 2>/dev/null || true

else
  # No ComfyUI directory at all - fresh clone
  echo "ComfyUI not found, cloning from GitHub..."

  cd /comfy/mnt || error_exit "Failed to cd to /comfy/mnt"
  git clone "$COMFYUI_REPO" || error_exit "Failed to clone ComfyUI"
fi

echo "== ComfyUI is now on nightly (master) branch =="
cd "$COMFYUI_DIR"
git log -1 --oneline
echo "Version: $(git describe --tags --always 2>/dev/null || echo 'unknown')"

exit 0
