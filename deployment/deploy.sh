#!/usr/bin/env bash
# Deploy (or update) the Palette app on IBM Code Engine.
#
# Prereqs:
#   - `ibmcloud` CLI with the `code-engine` plugin installed
#   - `ibmcloud login` completed
#   - `ibmcloud ce project select --name $CE_PROJECT` (this script will
#     run it for you)
#   - Settings in the repo's .env (see .env.example), or exported.
#   - Model backends, each optional (see config.sh):
#       RITS:     a secret $CE_SECRET_NAME holding RITS_API_KEY
#                 (CE_SECRET_NAME= empty -> no RITS at all)
#       CE fleet: PALETTE_CE_BASE_URL + PALETTE_CE_API_KEY  -> designer/coder
#       watsonx:  GPT_OSS_120B_PROVIDER=watsonx + WATSONX_*  -> crafter/editor
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./config.sh
source "${SCRIPT_DIR}/config.sh"

# `ce project select` fails with "No resource group targeted" otherwise.
if [[ -n "${CE_REGION}" || -n "${CE_RESOURCE_GROUP}" ]]; then
  echo ">> Targeting region=${CE_REGION:-<current>} resource-group=${CE_RESOURCE_GROUP:-<current>}"
  ibmcloud target ${CE_REGION:+-r "$CE_REGION"} ${CE_RESOURCE_GROUP:+-g "$CE_RESOURCE_GROUP"} -q >/dev/null
fi
echo ">> Selecting Code Engine project: ${CE_PROJECT}"
ibmcloud ce project select --name "${CE_PROJECT}"

APP_EXISTS=false
ibmcloud ce application get --name "${CE_APP_NAME}" >/dev/null 2>&1 && APP_EXISTS=true

# Secrets the app's CURRENT spec pulls env from. (Grepping the whole app JSON
# is wrong: old revisions still mention secrets that were detached, and
# detaching a non-attached secret fails the update.)
app_uses_secret() {
  $APP_EXISTS && ibmcloud ce application get --name "${CE_APP_NAME}" --output json \
    | jq -r '.spec.template.spec.containers[0].envFrom[]?.secretRef.name // empty' \
    | grep -qx "$1"
}

# RITS backend — optional. Empty CE_SECRET_NAME = no RITS: skip the secret and
# detach rits-api-key if an earlier deploy attached it.
RITS_ARGS=()
if [[ -n "${CE_SECRET_NAME}" ]]; then
  if ! ibmcloud ce secret get --name "${CE_SECRET_NAME}" >/dev/null 2>&1; then
    echo "!! Secret '${CE_SECRET_NAME}' not found in project '${CE_PROJECT}'."
    echo "   Create it:  ibmcloud ce secret create --name ${CE_SECRET_NAME} --from-literal RITS_API_KEY=<key>"
    echo "   Or run with no RITS: set CE_SECRET_NAME= (empty) and configure the CE fleet + watsonx."
    exit 1
  fi
  RITS_ARGS=(--env-from-secret "${CE_SECRET_NAME}")
else
  if [[ -z "${PALETTE_CE_BASE_URL:-}" || "${GPT_OSS_120B_PROVIDER:-}" != "watsonx" ]]; then
    echo "!! CE_SECRET_NAME is empty (no RITS), so designer/coder need PALETTE_CE_BASE_URL"
    echo "   and crafter/editor need GPT_OSS_120B_PROVIDER=watsonx — otherwise builds would fail."
    exit 1
  fi
  echo ">> No RITS: designer/coder -> CE fleet, gpt-oss-120b -> watsonx"
  if app_uses_secret rits-api-key; then
    RITS_ARGS=(--env-from-secret-rm rits-api-key)
  fi
fi

# Designer/coder backend — see CE_FLEET_SECRET_NAME in config.sh.
FLEET_ARGS=()
if [[ -n "${PALETTE_CE_BASE_URL:-}" ]]; then
  if [[ -z "${PALETTE_CE_API_KEY:-}" ]]; then
    echo "!! PALETTE_CE_BASE_URL is set but PALETTE_CE_API_KEY is not."
    echo "   It's the fleet endpoint's bearer key: palette-model-fleet/.serve_api_key"
    exit 1
  fi
  echo ">> designer/coder -> CE fleet ${PALETTE_CE_BASE_URL} (secret ${CE_FLEET_SECRET_NAME})"
  if ibmcloud ce secret get --name "${CE_FLEET_SECRET_NAME}" >/dev/null 2>&1; then
    action=update
  else
    action=create
  fi
  ibmcloud ce secret "${action}" --name "${CE_FLEET_SECRET_NAME}" \
    --from-literal "PALETTE_CE_BASE_URL=${PALETTE_CE_BASE_URL}" \
    --from-literal "PALETTE_CE_API_KEY=${PALETTE_CE_API_KEY}"
  FLEET_ARGS=(--env-from-secret "${CE_FLEET_SECRET_NAME}")
elif [[ "${PALETTE_BACKEND:-}" == "rits" ]]; then
  if app_uses_secret "${CE_FLEET_SECRET_NAME}"; then
    echo ">> designer/coder -> RITS (detaching ${CE_FLEET_SECRET_NAME})"
    FLEET_ARGS=(--env-from-secret-rm "${CE_FLEET_SECRET_NAME}")
  fi
fi

# gpt-oss-120b backend — see CE_WATSONX_SECRET_NAME in config.sh.
if [[ "${GPT_OSS_120B_PROVIDER:-}" == "watsonx" ]]; then
  if [[ -z "${WATSONX_APIKEY:-}" || -z "${WATSONX_PROJECT_ID:-}${WATSONX_SPACE_ID:-}" ]]; then
    echo "!! GPT_OSS_120B_PROVIDER=watsonx needs WATSONX_APIKEY and WATSONX_PROJECT_ID (or WATSONX_SPACE_ID)."
    exit 1
  fi
  echo ">> gpt-oss-120b -> watsonx.ai ${WATSONX_URL:-https://us-south.ml.cloud.ibm.com} (secret ${CE_WATSONX_SECRET_NAME})"
  WX_LITERALS=(--from-literal "GPT_OSS_120B_PROVIDER=watsonx"
               --from-literal "WATSONX_APIKEY=${WATSONX_APIKEY}"
               --from-literal "WATSONX_URL=${WATSONX_URL:-https://us-south.ml.cloud.ibm.com}")
  [[ -n "${WATSONX_PROJECT_ID:-}" ]] && WX_LITERALS+=(--from-literal "WATSONX_PROJECT_ID=${WATSONX_PROJECT_ID}")
  [[ -n "${WATSONX_SPACE_ID:-}" ]] && WX_LITERALS+=(--from-literal "WATSONX_SPACE_ID=${WATSONX_SPACE_ID}")
  if ibmcloud ce secret get --name "${CE_WATSONX_SECRET_NAME}" >/dev/null 2>&1; then
    action=update
  else
    action=create
  fi
  ibmcloud ce secret "${action}" --name "${CE_WATSONX_SECRET_NAME}" "${WX_LITERALS[@]}"
  FLEET_ARGS+=(--env-from-secret "${CE_WATSONX_SECRET_NAME}")
elif [[ "${GPT_OSS_120B_PROVIDER:-}" == "rits" ]]; then
  if app_uses_secret "${CE_WATSONX_SECRET_NAME}"; then
    echo ">> gpt-oss-120b -> RITS (detaching ${CE_WATSONX_SECRET_NAME})"
    FLEET_ARGS+=(--env-from-secret-rm "${CE_WATSONX_SECRET_NAME}")
  fi
fi

if $APP_EXISTS; then
  echo ">> Updating existing app ${CE_APP_NAME}"
  ibmcloud ce application update \
    --name "${CE_APP_NAME}" \
    --image "${IMAGE_REF}" \
    --registry-secret "${CE_REGISTRY_SECRET}" \
    --port "${CE_PORT}" \
    --cpu "${CE_CPU}" \
    --memory "${CE_MEMORY}" \
    --min-scale "${CE_MIN_SCALE}" \
    --max-scale "${CE_MAX_SCALE}" \
    ${RITS_ARGS[@]+"${RITS_ARGS[@]}"} \
    ${FLEET_ARGS[@]+"${FLEET_ARGS[@]}"}
else
  echo ">> Creating new app ${CE_APP_NAME}"
  ibmcloud ce application create \
    --name "${CE_APP_NAME}" \
    --image "${IMAGE_REF}" \
    --registry-secret "${CE_REGISTRY_SECRET}" \
    --port "${CE_PORT}" \
    --cpu "${CE_CPU}" \
    --memory "${CE_MEMORY}" \
    --min-scale "${CE_MIN_SCALE}" \
    --max-scale "${CE_MAX_SCALE}" \
    ${RITS_ARGS[@]+"${RITS_ARGS[@]}"} \
    ${FLEET_ARGS[@]+"${FLEET_ARGS[@]}"}
fi

echo ">> App URL:"
ibmcloud ce application get --name "${CE_APP_NAME}" --output url
