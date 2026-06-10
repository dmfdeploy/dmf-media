#!/usr/bin/env bash
# bootstrap-k3s.sh — turn the two booted Lima VMs (mxl-dev-1, mxl-dev-2) into a
# 2-node k3s cluster on the Lima `shared` net (192.168.105.0/24), labelled for
# the MXL demo placement. Idempotent-ish; safe to re-run.
#
#   server = mxl-dev-1  (label dmf.io/mxl-demo-role=view)
#   agent  = mxl-dev-2  (label dmf.io/mxl-demo-role=source)
#
# Both nodes also get dmf.io/role=mxl-processor (the chart's default nodeSelector).
# No taints locally: tainting the single server node would block coredns/local-path.
# Taint behaviour is exercised on the real Aliyun cluster, not here.
#
# Writes a host-usable kubeconfig to ./kubeconfig-mxl-dev.yaml (server = VM IP).
set -euo pipefail

SERVER=mxl-dev-1
AGENT=mxl-dev-2
K3S_VERSION="${K3S_VERSION:-v1.30.6+k3s1}"   # match the Aliyun media nodes
HERE="$(cd "$(dirname "$0")" && pwd)"

ip_on_shared() {  # echo the 192.168.105.x address inside VM $1
  limactl shell "$1" -- bash -lc \
    "ip -4 -o addr show | awk '/192\\.168\\.105\\./{print \$4}' | cut -d/ -f1 | head -1"
}
iface_on_shared() {  # echo the iface holding the 192.168.105.x address in VM $1
  limactl shell "$1" -- bash -lc \
    "ip -4 -o addr show | awk '/192\\.168\\.105\\./{print \$2; exit}'"
}

SERVER_IP="$(ip_on_shared "$SERVER")"
SERVER_IFACE="$(iface_on_shared "$SERVER")"
AGENT_IP="$(ip_on_shared "$AGENT")"
AGENT_IFACE="$(iface_on_shared "$AGENT")"
echo "server $SERVER = $SERVER_IP ($SERVER_IFACE) | agent $AGENT = $AGENT_IP ($AGENT_IFACE)"
[ -n "$SERVER_IP" ] && [ -n "$AGENT_IP" ] || { echo "missing shared-net IP — is the 'shared' network up on both VMs?" >&2; exit 1; }

echo "==> installing k3s SERVER on $SERVER"
limactl shell "$SERVER" -- bash -lc "
  set -eux
  if ! sudo test -f /etc/rancher/k3s/k3s.yaml; then
    curl -sfL https://get.k3s.io | sudo INSTALL_K3S_VERSION='$K3S_VERSION' sh -s - server \
      --node-ip '$SERVER_IP' --flannel-iface '$SERVER_IFACE' \
      --write-kubeconfig-mode 644 \
      --node-label dmf.io/role=mxl-processor --node-label dmf.io/mxl-demo-role=view
  fi
  sudo k3s kubectl wait --for=condition=Ready node --all --timeout=120s
"

TOKEN="$(limactl shell "$SERVER" -- sudo cat /var/lib/rancher/k3s/server/node-token)"

echo "==> installing k3s AGENT on $AGENT (joining https://$SERVER_IP:6443)"
limactl shell "$AGENT" -- bash -lc "
  set -eux
  if ! sudo test -f /etc/rancher/node/password; then
    curl -sfL https://get.k3s.io | sudo INSTALL_K3S_VERSION='$K3S_VERSION' \
      K3S_URL='https://$SERVER_IP:6443' K3S_TOKEN='$TOKEN' sh -s - agent \
      --node-ip '$AGENT_IP' --flannel-iface '$AGENT_IFACE' \
      --node-label dmf.io/role=mxl-processor --node-label dmf.io/mxl-demo-role=source
  fi
"

echo "==> waiting for both nodes Ready"
limactl shell "$SERVER" -- sudo k3s kubectl wait --for=condition=Ready node --all --timeout=120s
limactl shell "$SERVER" -- sudo k3s kubectl get nodes -o wide --show-labels | sed 's/,/\n      /g'

echo "==> writing host kubeconfig -> $HERE/kubeconfig-mxl-dev.yaml (server $SERVER_IP)"
limactl shell "$SERVER" -- sudo cat /etc/rancher/k3s/k3s.yaml \
  | sed "s/127.0.0.1/$SERVER_IP/" > "$HERE/kubeconfig-mxl-dev.yaml"
chmod 600 "$HERE/kubeconfig-mxl-dev.yaml"
echo "done. use:  export KUBECONFIG=$HERE/kubeconfig-mxl-dev.yaml"
