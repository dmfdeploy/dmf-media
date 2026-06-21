#!/usr/bin/env bash
# publish-nmos-crosspoint.sh — build + (optionally) publish the nmos-crosspoint
# image. Thin wrapper: builds the ARM64 image at a pinned upstream ref via Colima,
# then delegates the GHCR push to the umbrella's bin/publish-image-to-ghcr.sh
# (single source of truth for auth: token-via-stdin, isolated DOCKER_CONFIG).
#
# Tag policy mirrors nmos-cpp: a published public DMF image must come from a
# pinned ref (never floating master). The Dockerfile pins NMOS_CROSSPOINT_REF;
# this wrapper defaults the public tag to `0.1.0-dev` and warns on a
# non-prerelease tag.
#
# Usage (operator runs in their terminal):
#
#   # Build only (local, this round's deliverable):
#   BUILD_ONLY=1 dmf-media/bin/publish-nmos-crosspoint.sh
#
#   # Build + push (token piped from a password manager):
#   security find-generic-password -s "ghcr.io" -a "$USER" -w \
#     | GHCR_USER="<github-username>" dmf-media/bin/publish-nmos-crosspoint.sh
#
# Env knobs:
#   NMOS_CROSSPOINT_REF  upstream commit/tag to build (default: pinned in Dockerfile)
#   IMAGE_TAG            tag suffix (default: 0.1.0-dev)
#   GHCR_NAMESPACE       GHCR namespace (default: dmfdeploy)
#   SOURCE_REGISTRY      local registry prefix (default: registry.dmf.example.com/dmf)
#   BUILD_ONLY           if set to 1, build + print digest, skip the push
#   DOCKER_HOST          defaults to the Colima docker-build socket

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# bin/ → dmf-media/ ; umbrella is a sibling clone of dmf-media.
DMF_MEDIA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DOCKERFILE="$DMF_MEDIA_DIR/docker/nmos-crosspoint/Dockerfile"

IMAGE_TAG="${IMAGE_TAG:-0.1.0-dev}"
GHCR_NAMESPACE="${GHCR_NAMESPACE:-dmfdeploy}"
SOURCE_REGISTRY="${SOURCE_REGISTRY:-registry.dmf.example.com/dmf}"
NMOS_CROSSPOINT_REF="${NMOS_CROSSPOINT_REF:-}"
LOCAL_IMAGE="${SOURCE_REGISTRY}/nmos-crosspoint:${IMAGE_TAG}"

# Default to the Colima docker-build socket if the operator hasn't set DOCKER_HOST.
export DOCKER_HOST="${DOCKER_HOST:-unix://$HOME/.colima/docker-build/docker.sock}"

# Resolve the umbrella publish helper (sibling clone of dmf-media).
UMBRELLA_DIR="$(cd "$DMF_MEDIA_DIR/.." && pwd)/dmfdeploy"
PUBLISH_HELPER="$UMBRELLA_DIR/bin/publish-image-to-ghcr.sh"

build_args=(--build-arg "IMAGE_VERSION=${IMAGE_TAG}")
if [[ -n "$NMOS_CROSSPOINT_REF" ]]; then
  build_args+=(--build-arg "NMOS_CROSSPOINT_REF=${NMOS_CROSSPOINT_REF}")
  build_args+=(--build-arg "VCS_REF=${NMOS_CROSSPOINT_REF}")
fi

echo "▶ building ${LOCAL_IMAGE} (arch: linux/arm64) via DOCKER_HOST=${DOCKER_HOST}" >&2
docker build \
  --platform linux/arm64 \
  -t "$LOCAL_IMAGE" \
  -f "$DOCKERFILE" \
  "${build_args[@]}" \
  "$DMF_MEDIA_DIR/docker/nmos-crosspoint"

# Smoke-assert the built UI made it into the image (codex P1.2: without
# server/public the server starts but renders nothing).
echo "▶ verifying server/public/index.html is present in the image" >&2
docker run --rm --entrypoint test "$LOCAL_IMAGE" -f /app/server/public/index.html \
  && echo "  ✓ server/public/index.html present" >&2

if [[ "${BUILD_ONLY:-0}" == "1" ]]; then
  echo "BUILD_ONLY=1 — skipping push. Local image: ${LOCAL_IMAGE}" >&2
  exit 0
fi

# Pre-release tag policy warning ------------------------------------------
if [[ "${IMAGE_TAG}" != *"-dev"* && "${IMAGE_TAG}" != *"-pre"* && "${IMAGE_TAG}" != *"-rc"* ]]; then
  if [[ "${CROSSPOINT_FORCE_PUBLISH:-0}" == "1" ]]; then
    echo "ℹ️  IMAGE_TAG=\"${IMAGE_TAG}\" (non-prerelease); CROSSPOINT_FORCE_PUBLISH=1 set, proceeding." >&2
  else
    echo "⚠️  IMAGE_TAG=\"${IMAGE_TAG}\" is not a pre-release tag. Confirm the image came from a pinned ref. Set CROSSPOINT_FORCE_PUBLISH=1 to bypass." >&2
    if [[ -r /dev/tty ]]; then
      read -r -p "Continue? [y/N] " ANSWER < /dev/tty
      [[ "${ANSWER}" == "y" || "${ANSWER}" == "Y" ]] || { echo "Aborted." >&2; exit 1; }
    else
      echo "Aborted (no /dev/tty; set CROSSPOINT_FORCE_PUBLISH=1 to skip)." >&2
      exit 1
    fi
  fi
fi

exec "$PUBLISH_HELPER" \
  "$LOCAL_IMAGE" \
  "ghcr.io/${GHCR_NAMESPACE}/nmos-crosspoint:${IMAGE_TAG}"
