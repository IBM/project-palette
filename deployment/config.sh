#!/usr/bin/env bash
# Shared config for build/push/deploy scripts.
# Override any of these in your shell before running the scripts, e.g.
#   export ICR_NAMESPACE=my-namespace
#   ./deployment/deploy.sh

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
: "${CE_SECRET_NAME:=rits-api-key}"   # secret holding RITS_API_KEY
: "${CE_CPU:=2}"
: "${CE_MEMORY:=4G}"
: "${CE_MIN_SCALE:=1}"
: "${CE_MAX_SCALE:=1}"
: "${CE_PORT:=8080}"

export IMAGE_NAME IMAGE_TAG ICR_REGION ICR_NAMESPACE IMAGE_REF TARGET_PLATFORM \
       CE_PROJECT CE_APP_NAME CE_SECRET_NAME CE_CPU CE_MEMORY \
       CE_MIN_SCALE CE_MAX_SCALE CE_PORT
