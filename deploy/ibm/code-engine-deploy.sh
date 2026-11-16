#!/usr/bin/env bash
#
# Deploy the Agentic Cyber Range to IBM Cloud Code Engine in ONE command,
# building straight from this repo's source (no local Docker, no manual
# container-registry wiring -- Code Engine's build service handles it and
# auto-configures registry access the first time).
#
# Heuristic mode (default) needs NO credentials. The watsonx.ai LLM commander
# is optional: set WATSONX_API_KEY *or* WATSONX_TOKEN plus WATSONX_PROJECT_ID.
#
# Prereqs:
#   * ibmcloud CLI   (https://cloud.ibm.com/docs/cli)
#   * plugin:        ibmcloud plugin install code-engine
#   * logged in:     ibmcloud login --sso     (or --apikey <key>)
#
# Usage (from anywhere; the script finds the repo root itself):
#   ./deploy/ibm/code-engine-deploy.sh
#
set -euo pipefail

# ---- configuration (override via environment) -----------------------------
RESOURCE_GROUP="${RESOURCE_GROUP:-Default}"
REGION="${REGION:-us-south}"
PROJECT="${CE_PROJECT:-cyber-range}"
APP="${CE_APP:-cyber-range}"

# Optional watsonx.ai LLM commander (leave blank to run heuristic-only).
# Supply EITHER an API key (durable) OR a bearer token (~1h, not refreshed).
WATSONX_API_KEY="${WATSONX_API_KEY:-}"
WATSONX_TOKEN="${WATSONX_TOKEN:-}"
WATSONX_URL="${WATSONX_URL:-https://${REGION}.ml.cloud.ibm.com}"
WATSONX_PROJECT_ID="${WATSONX_PROJECT_ID:-}"
WATSONX_MODEL_ID="${WATSONX_MODEL_ID:-meta-llama/llama-3-3-70b-instruct}"

# Build from the repo root (where the Dockerfile lives), wherever we're called.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "==> Target: rg=${RESOURCE_GROUP} region=${REGION} project=${PROJECT} app=${APP}"
echo "==> Source: ${REPO_ROOT}"

ibmcloud target -g "${RESOURCE_GROUP}" -r "${REGION}"

# ---- 1. Code Engine project -----------------------------------------------
echo "==> Selecting / creating Code Engine project ${PROJECT}"
ibmcloud ce project select --name "${PROJECT}" 2>/dev/null \
  || ibmcloud ce project create --name "${PROJECT}"

# ---- 2. watsonx secret (optional) -----------------------------------------
# Only created when a project id AND a credential (key or token) are supplied.
ENV_ARGS=()
if [[ -n "${WATSONX_PROJECT_ID}" && ( -n "${WATSONX_API_KEY}" || -n "${WATSONX_TOKEN}" ) ]]; then
  echo "==> Creating watsonx secret (LLM commander enabled)"
  SECRET_ARGS=(--from-literal "WATSONX_PROJECT_ID=${WATSONX_PROJECT_ID}")
  [[ -n "${WATSONX_API_KEY}" ]] && SECRET_ARGS+=(--from-literal "WATSONX_API_KEY=${WATSONX_API_KEY}")
  [[ -n "${WATSONX_TOKEN}"   ]] && SECRET_ARGS+=(--from-literal "WATSONX_TOKEN=${WATSONX_TOKEN}")
  ibmcloud ce secret create --name watsonx-creds "${SECRET_ARGS[@]}" 2>/dev/null \
    || ibmcloud ce secret update --name watsonx-creds "${SECRET_ARGS[@]}"
  ENV_ARGS=(--env-from-secret watsonx-creds
            --env "WATSONX_URL=${WATSONX_URL}"
            --env "WATSONX_MODEL_ID=${WATSONX_MODEL_ID}")
  [[ -n "${WATSONX_TOKEN}" ]] && echo "   NOTE: a bearer token is ~1h and not refreshed; prefer an API key for a persistent deploy."
else
  echo "==> No watsonx credentials set -> running with the heuristic commander (no secrets needed)."
fi

# ---- 3. build-from-source + deploy in one step ----------------------------
# --build-source uploads REPO_ROOT, builds the Dockerfile in Code Engine, and
# deploys. min=max=1 because the demo holds one shared in-memory simulation:
#   min-scale 1  keeps the battle warm (scale-to-zero would reset it)
#   max-scale 1  sends every viewer to the same instance (one shared battle)
COMMON_ARGS=(--build-source "${REPO_ROOT}" --port 8080
             --cpu 1 --memory 2G --min-scale 1 --max-scale 1
             "${ENV_ARGS[@]}")

echo "==> Building from source and deploying app ${APP}"
if ibmcloud ce app get --name "${APP}" >/dev/null 2>&1; then
  ibmcloud ce app update --name "${APP}" "${COMMON_ARGS[@]}"
else
  # On the first local source build Code Engine may prompt to set up registry
  # access -- choose the automatic option so it provisions ICR for you.
  ibmcloud ce app create --name "${APP}" "${COMMON_ARGS[@]}"
fi

echo
echo "==> Done. Dashboard URL (open this; press Play):"
ibmcloud ce app get --name "${APP}" --output url
