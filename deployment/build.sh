#!/usr/bin/env bash
# Build the Palette container image for Code Engine (linux/amd64).
#
# Works on both Docker Desktop (uses buildx under the hood when the
# default builder supports it) and Podman (uses qemu for cross-arch
# natively). We deliberately avoid `docker buildx` subcommands because
# Podman doesn't implement them.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=./config.sh
source "${SCRIPT_DIR}/config.sh"

: "${CONTAINER_CMD:=docker}"

echo ">> Building ${IMAGE_REF} for ${TARGET_PLATFORM} via ${CONTAINER_CMD}"
cd "${ROOT_DIR}"

"${CONTAINER_CMD}" build \
  --platform "${TARGET_PLATFORM}" \
  --tag "${IMAGE_REF}" \
  .

echo ">> Built ${IMAGE_REF}"
