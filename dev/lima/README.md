# Local MXL test cluster (Lima, 2-node k3s)

A reproducible **two-node k3s** cluster on local Lima VMs for building the MXL
fabrics image and testing the M1.1 catalog cycle **with zero cloud spend**. Mirrors
the Aliyun media pool (Ubuntu 24.04 aarch64, k3s, two hosts) closely enough to
exercise placement, `hostNetwork` cross-host fabrics (tcp, port 1234 — no collision
because the two pods land on **different** nodes), and the full 0→1→2 catalog flow.

| VM | role | k3s | label `dmf.io/mxl-demo-role` |
|----|------|-----|------------------------------|
| `mxl-dev-1` | server + Docker builder | server | `view` |
| `mxl-dev-2` | agent | agent | `source` |

Both on the Lima **`shared`** network (`192.168.105.0/24`) — the local stand-in for
the Aliyun `eth0` VPC plane.

## Prerequisites (already satisfied on this host)
- `limactl` (vz), `socket_vmnet` at `/opt/socket_vmnet` + the lima sudoers, Docker CLI.
- The `shared` network defined in `~/.lima/_config/networks.yaml`.
- **RAM:** 16 GiB host — stop `dmf-sandbox` first (`limactl stop dmf-sandbox`); the two
  MXL VMs use 4 GiB each.

## Bring-up
```bash
cd dmf-media/dev/lima
limactl start --name=mxl-dev-1 ./mxl-dev-1.yaml --tty=false
limactl start --name=mxl-dev-2 ./mxl-dev-2.yaml --tty=false
./bootstrap-k3s.sh                       # installs k3s server+agent, labels, kubeconfig
export KUBECONFIG=$PWD/kubeconfig-mxl-dev.yaml
kubectl get nodes -o wide
```

## Image (thin overlay, no from-source rebuild)
The full `mxl-fabrics-demo:v1.0.3-fabrics-dev` is on GHCR. M1.1 only adds the source
supervisor + status-on-both-roles, so we build a thin overlay `FROM` it on `mxl-dev-1`
and import into k3s on both nodes (`k3s ctr images import`). See `build-image.sh` (TBD).

## Teardown
```bash
limactl stop mxl-dev-1 mxl-dev-2 && limactl delete mxl-dev-1 mxl-dev-2
limactl start dmf-sandbox          # restore the other sandbox
```
