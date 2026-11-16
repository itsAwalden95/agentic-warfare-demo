#!/usr/bin/env bash
#
# Deploy the Agentic Cyber Range to IBM Cloud Code Engine (fastest path:
# fully managed, serverless containers, scale-to-zero, HTTPS + WebSocket out of
# the box). Good for the demo itself and for CPU-only swarm simulation.
#
# Prereqs:
#   * ibmcloud CLI  (https://cloud.ibm.com/docs/cli)
#   * plugins:      ibmcloud plugin install code-engine container-registry
#   * logged in:    ibmcloud login --sso   (or with an API key)
#
# Usage:
#   ./code-engine-deploy.sh            # build from source + deploy
#
set -euo pipefail

# ---- configuration (override via environment) -----------------------------
RESOURCE_GROUP="${RESOURCE_GROUP:-Default}"
REGION="${REGION:-us-south}"
PROJECT="${CE_PROJECT:-cyber-range}"
APP="${CE_APP:-cyber-range}"
ICR_NAMESPACE="${ICR_NAMESPACE:-cyber-range-ns}"     # container registry namespace
IMAGE="${IMAGE:-${REGION}.icr.io/${ICR_NAMESPACE}/cyber-range:latest}"

# Optional watsonx.ai LLM commander (leave blank to run heuristic-only).
# Supply EITHER an API key (durable, recommended) OR a bearer token (~1h, not
# refreshed -- for a quick demo only; see watsonx-setup.md).
WATSONX_API_KEY="${WATSONX_API_KEY:-}"
WATSONX_TOKEN="${WATSONX_TOKEN:-}"
WATSONX_URL="${WATSONX_URL:-https://${REGION}.ml.cloud.ibm.com}"
WATSONX_PROJECT_ID="${WATSONX_PROJECT_ID:-}"
WATSONX_MODEL_ID="${WATSONX_MODEL_ID:-meta-llama/llama-3-3-70b-instruct}"

echo "==> Target: rg=${RESOURCE_GROUP} region=${REGION} project=${PROJECT} app=${APP}"

ibmcloud target -g "${RESOURCE_GROUP}" -r "${REGION}"

# ---- 1. container registry namespace --------------------------------------
echo "==> Ensuring ICR namespace ${ICR_NAMESPACE}"
ibmcloud cr region-set "${REGION}" >/dev/null 2>&1 || true
ibmcloud cr namespace-add "${ICR_NAMESPACE}" 2>/dev/null || true

# ---- 2. Code Engine project ------------------------------------------------
echo "==> Selecting / creating Code Engine project ${PROJECT}"
ibmcloud ce project select --name "${PROJECT}" 2>/dev/null \
  || ibmcloud ce project create --name "${PROJECT}"

# ---- 3. build image from source (Code Engine build) -----------------------
# Builds this repo's Dockerfile and pushes to ICR. Run from repo root.
echo "==> Building image ${IMAGE} from source"
ibmcloud ce build create --name cyber-range-build \
  --source . --image "${IMAGE}" \
  --strategy dockerfile --size large 2>/dev/null \
  || ibmcloud ce build update --name cyber-range-build \
       --source . --image "${IMAGE}" --strategy dockerfile --size large
ibmcloud ce buildrun submit --build cyber-range-build --wait

# ---- 4. watsonx secret (optional) -----------------------------------------
# Enabled by an API key OR a bearer token, as long as a project id is set.
ENV_ARGS=()
if [[ -n "${WATSONX_PROJECT_ID}" && ( -n "${WATSONX_API_KEY}" || -n "${WATSONX_TOKEN}" ) ]]; then
  echo "==> Creating watsonx secret"
  SECRET_ARGS=(--from-literal "WATSONX_PROJECT_ID=${WATSONX_PROJECT_ID}")
  [[ -n "${WATSONX_API_KEY}" ]] && SECRET_ARGS+=(--from-literal "WATSONX_API_KEY=${WATSONX_API_KEY}")
  [[ -n "${WATSONX_TOKEN}"   ]] && SECRET_ARGS+=(--from-literal "WATSONX_TOKEN=${WATSONX_TOKEN}")
  ibmcloud ce secret create --name watsonx-creds "${SECRET_ARGS[@]}" 2>/dev/null \
    || ibmcloud ce secret update --name watsonx-creds "${SECRET_ARGS[@]}"
  ENV_ARGS=(--env-from-secret watsonx-creds
            --env "WATSONX_URL=${WATSONX_URL}"
            --env "WATSONX_MODEL_ID=${WATSONX_MODEL_ID}")
  [[ -n "${WATSONX_TOKEN}" ]] && echo "   NOTE: a bearer token is ~1h and not refreshed; prefer an API key for a persistent deploy."
fi

# ---- 5. deploy / update the app -------------------------------------------
# Single instance (min=max=1) because the demo holds one shared in-memory
# simulation; scale the sim out via separate sessions, not replicas.
echo "==> Deploying app ${APP}"
if ibmcloud ce app get --name "${APP}" >/dev/null 2>&1; then
  ibmcloud ce app update --name "${APP}" --image "${IMAGE}" \
    --port 8080 --cpu 1 --memory 2G --min-scale 1 --max-scale 1 \
    "${ENV_ARGS[@]}"
else
  ibmcloud ce app create --name "${APP}" --image "${IMAGE}" \
    --port 8080 --cpu 1 --memory 2G --min-scale 1 --max-scale 1 \
    --registry-secret "" "${ENV_ARGS[@]}"
fi

echo
echo "==> Done. Dashboard URL:"
ibmcloud ce app get --name "${APP}" --output url
