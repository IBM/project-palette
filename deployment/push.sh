#!/usr/bin/env bash
# Push the built image to IBM Container Registry.
#
# Assumes you've already run `ibmcloud login` and `ibmcloud cr login`.
# `ibmcloud cr login` writes credentials that both Docker and Podman
# can use.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./config.sh
source "${SCRIPT_DIR}/config.sh"

: "${CONTAINER_CMD:=docker}"

echo ">> Pushing ${IMAGE_REF} via ${CONTAINER_CMD}"
"${CONTAINER_CMD}" push "${IMAGE_REF}"
echo ">> Pushed ${IMAGE_REF}"
