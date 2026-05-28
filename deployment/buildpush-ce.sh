#!/usr/bin/env bash
# Convenience: build for linux/amd64 then push to ICR.
#
# Podman doesn't support docker-buildx's `--push` one-shot, so we keep
# this as a plain build + push pair. Still saves typing.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

"${SCRIPT_DIR}/build.sh"
"${SCRIPT_DIR}/push.sh"
