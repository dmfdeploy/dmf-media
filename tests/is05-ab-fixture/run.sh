#!/usr/bin/env bash
# One command for the IS-05 A/B MXL fixture (dmfdeploy/dmfdeploy#624).
#
#   tests/is05-ab-fixture/run.sh [OUT_DIR]
#
# Creates a single-node kind cluster on the local Docker host (ARM64 is the
# target; kind uses the host's architecture), mounts one tmpfs MXL domain on
# the node, runs run.py, and deletes the cluster again. Observations land in
# OUT_DIR (default: tests/is05-ab-fixture/out/<UTC timestamp>).
#
# The script refuses to start when a cluster with the chosen name already
# exists, and it deletes only a cluster that it created itself. It keeps the
# cluster's credentials in a temporary kubeconfig, so your own kubeconfig and
# current context stay untouched.
#
# Needs: docker, kind, kubectl, python3 (3.11 or later), network access to
# pull the pinned images and Pillow.
#
# Env:
#   KIND_CLUSTER       cluster name (default is05-ab); must not exist yet
#   KEEP_CLUSTER=1     leave the cluster running afterwards (its kubeconfig
#                      is removed; fetch one with `kind export kubeconfig`)
#   SKIP_NMOS_TESTING=1  skip the warn-mode nmos-testing gate
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cluster="${KIND_CLUSTER:-is05-ab}"
out="${1:-${here}/out/$(date -u +%Y%m%dT%H%M%SZ)}"
domain=/var/lib/dmf-mxl/is05-ab
mkdir -p "${out}"
out="$(cd "${out}" && pwd)"

for tool in docker kind kubectl python3; do
  command -v "${tool}" >/dev/null || { echo "missing tool: ${tool}" >&2; exit 2; }
done

if kind get clusters 2>/dev/null | grep -qx "${cluster}"; then
  echo "kind cluster '${cluster}' already exists; this script only deletes clusters it creates." >&2
  echo "Delete it or set KIND_CLUSTER to an unused name." >&2
  exit 2
fi

# Credentials go to a private kubeconfig so the caller's kubeconfig and
# current context are never read or changed.
kubeconfig_dir="$(mktemp -d)"
export KUBECONFIG="${kubeconfig_dir}/kubeconfig"
created=0

# shellcheck disable=SC2329  # called by the EXIT trap below
cleanup() {
  if [ "${created}" = "1" ] && [ "${KEEP_CLUSTER:-0}" != "1" ]; then
    kind delete cluster --name "${cluster}" --kubeconfig "${KUBECONFIG}" >/dev/null 2>&1 || true
  fi
  rm -rf "${kubeconfig_dir}"
}
trap cleanup EXIT

kind create cluster --name "${cluster}" --kubeconfig "${KUBECONFIG}" --wait 120s
created=1

# One MXL domain for the whole node: tmpfs, owned by the non-root media user.
docker exec "${cluster}-control-plane" sh -c "
  mkdir -p ${domain}
  mountpoint -q ${domain} || mount -t tmpfs -o size=256m,uid=1000,gid=1000,mode=0750 tmpfs ${domain}"

venv="${out}/.venv"
python3 -m venv "${venv}"
"${venv}/bin/pip" install --quiet --disable-pip-version-check -r "${here}/requirements.txt"

args=(--out "${out}")
[ "${SKIP_NMOS_TESTING:-0}" = "1" ] && args+=(--skip-nmos-testing)

status=0
"${venv}/bin/python" "${here}/run.py" "${args[@]}" 2>&1 | tee "${out}/run.log" || status=$?
rm -rf "${venv}"
echo "observations: ${out}/observations.json"
exit "${status}"
