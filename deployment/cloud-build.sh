#!/usr/bin/env bash
# Build the image on Code Engine (cloud-side, linux/amd64) and push it to ICR.
# No local Docker or cross-compilation — the right choice on an arm64 Mac.
# .ceignore keeps .env and other local state out of the uploaded source.
#
#   ./deployment/cloud-build.sh            # IMAGE_REF from .env / config.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./config.sh
source "${SCRIPT_DIR}/config.sh"

if [[ -n "${CE_REGION}" || -n "${CE_RESOURCE_GROUP}" ]]; then
  ibmcloud target ${CE_REGION:+-r "$CE_REGION"} ${CE_RESOURCE_GROUP:+-g "$CE_RESOURCE_GROUP"} -q >/dev/null
fi
ibmcloud ce project select --name "${CE_PROJECT}" -q

BUILD_NAME="${IMAGE_NAME}-build-$(date +%Y%m%d-%H%M%S)"
echo ">> Cloud build ${BUILD_NAME} -> ${IMAGE_REF}"
ibmcloud ce buildrun submit --name "${BUILD_NAME}" \
  --source "${SCRIPT_DIR}/.." \
  --strategy dockerfile \
  --image "${IMAGE_REF}" \
  --registry-secret "${CE_REGISTRY_SECRET}" \
  --size large \
  --timeout 3600
ibmcloud ce buildrun logs -f -n "${BUILD_NAME}" | grep -E "^.*step-build-and-push: #[0-9]+ (DONE|ERROR)|error|Build run completed" || true
status=$(ibmcloud ce buildrun get -n "${BUILD_NAME}" -o json | jq -r '.status')
[[ "$status" == "succeeded" ]] || { echo "!! build ${status}: ibmcloud ce buildrun logs -n ${BUILD_NAME}"; exit 1; }
echo ">> Pushed ${IMAGE_REF}"
