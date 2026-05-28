#!/usr/bin/env bash
# Deploy (or update) the Palette app on IBM Code Engine.
#
# Prereqs:
#   - `ibmcloud` CLI with the `code-engine` plugin installed
#   - `ibmcloud login` completed
#   - `ibmcloud ce project select --name $CE_PROJECT` (this script will
#     run it for you)
#   - A secret named $CE_SECRET_NAME holding RITS_API_KEY. Create with:
#       ibmcloud ce secret create --name $CE_SECRET_NAME \
#         --from-literal RITS_API_KEY=<your key>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./config.sh
source "${SCRIPT_DIR}/config.sh"

echo ">> Selecting Code Engine project: ${CE_PROJECT}"
ibmcloud ce project select --name "${CE_PROJECT}"

# Verify the secret exists; bail loudly if not — RITS_API_KEY is required
if ! ibmcloud ce secret get --name "${CE_SECRET_NAME}" >/dev/null 2>&1; then
  echo "!! Secret '${CE_SECRET_NAME}' not found in project '${CE_PROJECT}'."
  echo "   Create it with:"
  echo "     ibmcloud ce secret create --name ${CE_SECRET_NAME} \\"
  echo "       --from-literal RITS_API_KEY=<your key>"
  exit 1
fi

if ibmcloud ce application get --name "${CE_APP_NAME}" >/dev/null 2>&1; then
  echo ">> Updating existing app ${CE_APP_NAME}"
  ibmcloud ce application update \
    --name "${CE_APP_NAME}" \
    --image "${IMAGE_REF}" \
    --port "${CE_PORT}" \
    --cpu "${CE_CPU}" \
    --memory "${CE_MEMORY}" \
    --min-scale "${CE_MIN_SCALE}" \
    --max-scale "${CE_MAX_SCALE}" \
    --env-from-secret "${CE_SECRET_NAME}"
else
  echo ">> Creating new app ${CE_APP_NAME}"
  ibmcloud ce application create \
    --name "${CE_APP_NAME}" \
    --image "${IMAGE_REF}" \
    --port "${CE_PORT}" \
    --cpu "${CE_CPU}" \
    --memory "${CE_MEMORY}" \
    --min-scale "${CE_MIN_SCALE}" \
    --max-scale "${CE_MAX_SCALE}" \
    --env-from-secret "${CE_SECRET_NAME}"
fi

echo ">> App URL:"
ibmcloud ce application get --name "${CE_APP_NAME}" --output url
