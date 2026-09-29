#!/usr/bin/env bash
# Shared config for build/push/deploy scripts.
# Values come from the repo's .env (see .env.example) when present; anything
# already exported in your shell wins over the file. E.g.
#   IMAGE_TAG=v2 ./deployment/deploy.sh

_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "${_REPO_ROOT}/.env" ]]; then
  while IFS= read -r _line || [[ -n "$_line" ]]; do
    [[ "$_line" =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || continue
    _k="${BASH_REMATCH[2]}"; _v="${BASH_REMATCH[3]}"
    _v="${_v%\"}"; _v="${_v#\"}"; _v="${_v%\'}"; _v="${_v#\'}"
    [[ "$_v" == "~/"* ]] && _v="${HOME}/${_v#\~/}"
    if [[ -z "${!_k+x}" ]]; then export "$_k=$_v"; fi   # shell env wins
  done < "${_REPO_ROOT}/.env"
fi

# --- Image / registry ---
: "${IMAGE_NAME:=palette}"
: "${IMAGE_TAG:=latest}"
: "${ICR_REGION:=icr.io}"           # e.g. us.icr.io, de.icr.io
: "${ICR_NAMESPACE:?ICR_NAMESPACE must be set (your IBM Container Registry namespace)}"

# Code Engine targets linux/amd64
: "${TARGET_PLATFORM:=linux/amd64}"

IMAGE_REF="${ICR_REGION}/${ICR_NAMESPACE}/${IMAGE_NAME}:${IMAGE_TAG}"

# --- Code Engine ---
: "${CE_PROJECT:?CE_PROJECT must be set (your Code Engine project name)}"
: "${CE_APP_NAME:=palette}"
: "${CE_REGION:=}"                    # e.g. us-east; with CE_RESOURCE_GROUP, deploy.sh targets them first
: "${CE_RESOURCE_GROUP:=}"            # e.g. routing
# Secret holding RITS_API_KEY. Set it to empty (CE_SECRET_NAME=) for a
# deployment with no RITS at all — designer/coder on the CE fleet and
# gpt-oss-120b on watsonx; deploy.sh then skips it and detaches it if attached.
: "${CE_SECRET_NAME=rits-api-key}"
: "${CE_REGISTRY_SECRET:=icr-secret-1}" # registry secret to pull IMAGE_REF from ICR
: "${CE_CPU:=2}"
: "${CE_MEMORY:=4G}"
: "${CE_MIN_SCALE:=1}"
: "${CE_MAX_SCALE:=1}"
: "${CE_PORT:=8080}"

# --- designer/coder backend (optional) ---
# Serve designer+coder from the self-hosted Code Engine fleet endpoint
# (palette-model-fleet repo) instead of RITS. deploy.sh stores both values in
# this secret and attaches it to the app:
#   PALETTE_CE_BASE_URL=http://<palette-lb-hostname>/v1 PALETTE_CE_API_KEY=... make ce-deploy
#   PALETTE_BACKEND=rits make ce-deploy    # detach -> back to RITS
# Neither set -> the app's current backend is left as is.
: "${CE_FLEET_SECRET_NAME:=palette-ce-fleet}"

# --- gpt-oss-120b backend (optional) ---
# Crafter + editor on watsonx.ai instead of RITS (us-south hosts gpt-oss-120b):
#   GPT_OSS_120B_PROVIDER=watsonx WATSONX_APIKEY=... WATSONX_PROJECT_ID=... make ce-deploy
#   GPT_OSS_120B_PROVIDER=rits make ce-deploy    # detach -> back to RITS
: "${CE_WATSONX_SECRET_NAME:=palette-watsonx}"

export IMAGE_NAME IMAGE_TAG ICR_REGION ICR_NAMESPACE IMAGE_REF TARGET_PLATFORM \
       CE_PROJECT CE_APP_NAME CE_SECRET_NAME CE_REGION CE_RESOURCE_GROUP CE_CPU CE_MEMORY \
       CE_MIN_SCALE CE_MAX_SCALE CE_PORT CE_FLEET_SECRET_NAME CE_REGISTRY_SECRET CE_WATSONX_SECRET_NAME
