#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  printf 'Usage: %s <wheel> <artifact-directory>\n' "$0" >&2
  exit 2
fi

wheel_path="$(realpath "$1")"
artifact_dir="$(realpath -m "$2")"
script_path="$(realpath "$(dirname "$0")/release_smoke.py")"
image="${AGENTGRAPH_PLAYWRIGHT_IMAGE:-mcr.microsoft.com/playwright/python:v1.62.0-noble}"
wheel_name="$(basename "$wheel_path")"

test -f "$wheel_path"
mkdir -p "$artifact_dir"

docker run --rm --init --ipc=host \
  --volume "$wheel_path:/dist/$wheel_name:ro" \
  --volume "$artifact_dir:/artifacts" \
  --volume "$script_path:/opt/release_smoke.py:ro" \
  "$image" \
  sh -c 'python -m pip install --disable-pip-version-check --no-cache-dir "playwright==1.62.0" && exec python /opt/release_smoke.py "$@"' \
  -- \
    --wheel "/dist/$wheel_name" \
    --artifacts /artifacts
