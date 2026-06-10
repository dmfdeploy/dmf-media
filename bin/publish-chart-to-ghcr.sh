#!/usr/bin/env bash
# publish-chart-to-ghcr.sh - publish the dmf-media NMOS chart to GHCR.
#
# Thin wrapper around the umbrella's bin/publish-chart-to-ghcr.sh. The umbrella
# script handles secrets (token via stdin, isolated HELM_REGISTRY_CONFIG with
# cleanup trap, never argv).
#
# Usage:
#
#   security find-generic-password -s "ghcr.io" -a "<github-username>" -w \
#     | GHCR_USER="<github-username>" \
#       ~/repos/dmfdeploy/dmf-media/bin/publish-chart-to-ghcr.sh
#
# Env knobs:
#   GHCR_USER       GitHub username (default: prompt)
#   GHCR_NAMESPACE  GHCR namespace (default: dmfdeploy)
#   CHART_REF       Final GHCR chart ref without version
#                   (default: ghcr.io/<namespace>/charts/nmos-cpp)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
UMBRELLA_DIR="$(cd "${REPO_ROOT}/.." && pwd)"

GHCR_NAMESPACE="${GHCR_NAMESPACE:-dmfdeploy}"
CHART_REF="${CHART_REF:-ghcr.io/${GHCR_NAMESPACE}/charts/nmos-cpp}"

exec "${UMBRELLA_DIR}/bin/publish-chart-to-ghcr.sh" \
  "${REPO_ROOT}/charts/nmos-cpp" \
  "${CHART_REF}"
