# Runbook: MXL Fabrics cross-host TCP demo (SPIKE)

Branch: `feat/mxl-single-node-spike` · two Aliyun ARM media nodes already joined
g2r6-foa9 (see `dmf-infra/docs/mxl-media-nodes-plan.md`).

> **Status: GREEN (2026-05-30).** Test-pattern grains produced on `aliyun-media-02`
> are forwarded over the libfabric **tcp** provider across the Aliyun VPC into the
> receiver's domain on `aliyun-media-01`; the received flow is Active with its head
> index advancing (~2-grain / ~50ms latency). Both pods stable, 0 restarts.

## 0. What this proves
A `target` (receiver) and an `initiator` (sender) running from one consolidated
`mxl-fabrics-demo` image on **two different MXL media nodes**, transferring grains
across hosts over the node's VPC NIC **eth0** (Aliyun VPC private, `<aliyun-vpc-cidr>`)
via `hostNetwork`. The initiator side also runs the full **demo media functions**
(test-pattern producer + reader + info) so there are real grains to move. First
cross-host fabrics test (vs single-node shared-mem `mxl-hello`).

- **NIC = `eth0`.** The `g8y.large` nodes have a **single** VPC NIC (`eth0`); there is
  **no eth1**. `fabrics.interface: eth0` in the chart.
- **tcp provider does NOT bypass the kernel.** Grains traverse the kernel TCP/IP stack
  on both hosts (the libfabric RMA semantics are emulated by the tcp provider). This
  proves the *plumbing*; the zero-copy path is `verbs`/eRDMA (deferred — see §5).

## 1. Build the consolidated image (gating step)
One image carries **every** demo function — the `mxl` base stage `COPY`s all
`/usr/bin/mxl-*`, so entrypoints just differ per chart container:
`mxl-fabrics-demo`, `mxl-gst-testsrc` / `mxl-gst-sink` / `mxl-gst-looping-filesrc`,
`mxl-info`, and `fake-reader.sh`. `dmf-media/docker/mxl-fabrics/Dockerfile` adds, over
the stock `examples/Dockerfile`: `libfabric` + `-DMXL_ENABLE_FABRICS_OFI=ON` (the
fabrics demo) and GStreamer (`libgstreamer1.0-dev` + plugins → `tools/mxl-gst` builds,
gated by `gstreamer_FOUND`). `-DBUILD_UTILS=OFF -DBUILD_TESTS=OFF -DBUILD_DOCS=OFF`
(the `utils/gst-looping-filesrc` CMakeLists hard-requires GStreamer and is redundant
with the `tools/mxl-gst` copy; tests/docs trimmed for build time).

Build context = the MXL source root (`~/repos/dmfdeploy/mxl`). Nodes are **ARM64** →
build **natively** on a media node (no qemu). The build is a real C++/vcpkg compile —
budget time on a 2-core node.

```bash
# 1. install a builder on the media node (Ubuntu)
ssh -i ~/.ssh/id_ed25519_k3s_aliyun k3s-admin@<media01-public> 'sudo apt-get install -y podman'
# 2. ship MXL source + Dockerfile (context = the MXL source root)
rsync -a --exclude build --exclude .git ~/repos/dmfdeploy/mxl/ k3s-admin@<media01-public>:/home/k3s-admin/mxl-src/
scp dmf-media/docker/mxl-fabrics/Dockerfile k3s-admin@<media01-public>:/home/k3s-admin/mxl-src/Dockerfile.fabrics
# 3. build (ARM64 native; long — GStreamer + vcpkg). Target the mxl-fabrics-demo stage.
ssh ... k3s-admin@<media01-public> '
  cd /home/k3s-admin/mxl-src
  sudo podman build --target mxl-fabrics-demo -f Dockerfile.fabrics \
    -t ghcr.io/dmfdeploy/mxl-fabrics-demo:v1.0.2-fabrics-dev .'
```

### 1a. Publish to ghcr (public) — nodes pull anonymously
The chart pulls from **`ghcr.io/dmfdeploy/mxl-fabrics-demo`** (public package → nodes
pull with **no imagePullSecret**, no Zot). Push needs a GHCR PAT with `write:packages`.
🛑 **§0 secrets discipline:** the token is never echoed/argv'd — pipe it from the macOS
Keychain (`security -s ghcr.io -a <gh-user> -w`) straight into `podman login --password-stdin`.

```bash
security find-generic-password -s "ghcr.io" -a "<gh-user>" -w \
  | ssh -i ~/.ssh/id_ed25519_k3s_aliyun k3s-admin@<media01-public> \
      'sudo podman login ghcr.io -u <gh-user> --password-stdin'
ssh ... k3s-admin@<media01-public> '
  sudo podman push ghcr.io/dmfdeploy/mxl-fabrics-demo:v1.0.2-fabrics-dev
  sudo podman logout ghcr.io'
```
First push of a **new package** lands **private** → make it Public once at
`github.com/orgs/dmfdeploy/packages` → `mxl-fabrics-demo` → visibility. New tags on an
already-public package inherit public visibility (no repeat step).

> Alternative (no registry): `podman save | sudo k3s ctr -n k8s.io images import -` on
> **both** nodes + `image.pullPolicy=Never`. The public registry
> (`ghcr.io/dmfdeploy`, anon-read, reachable cross-cloud) is a third option but is a
> plain registry, not a pull-through cache.

## 2. Deploy phase 1 — the target (receiver)
Run `helm` on the g2r6-foa9 control node (`sudo helm --kubeconfig
/etc/rancher/k3s/k3s.yaml ...`), chart shipped there via `scp`/tar.

```bash
helm install mxl-fabrics dmf-media/charts/mxl-fabrics-demo \
  --set target.nodeName=aliyun-media-01
# target pod = [target + info]; read the target-info it prints:
kubectl -n mxl logs deploy/mxl-fabrics-demo-target -c target | sed -n 's/.*Target info:[[:space:]]*//p' | tail -1
```
Copy the opaque target-info blob.

> **Reinstall caveat:** the chart creates the `mxl` Namespace, so a `helm uninstall`
> deletes it. Wait for the namespace to fully terminate before re-installing, else the
> install fails recreating it.

## 3. Deploy phase 2 — the initiator (sender + producer stack)
```bash
helm upgrade mxl-fabrics dmf-media/charts/mxl-fabrics-demo --reuse-values \
  --set initiator.enabled=true \
  --set initiator.nodeName=aliyun-media-02 \
  --set initiator.targetInfo='<paste blob>'
```
The initiator pod = `writer` (`mxl-gst-testsrc` test pattern, creates flow `5fbec3b1…`
+ grains in the shared domain) → `initiator` (`mxl-fabrics-demo -i`, waits for the
flow then forwards grains over the fabric) → `reader` (`fake-reader.sh`) → `info`
(`mxl-info`). Toggle via `demoFunctions.{writer,localReader,info}`.

## 4. Verify the cross-host transfer
```bash
# sender connects + forwards:
kubectl -n mxl logs deploy/mxl-fabrics-demo-initiator -c initiator   # "Endpoint is now connected"
kubectl -n mxl logs deploy/mxl-fabrics-demo-initiator -c writer      # gst test pattern pipeline
# RECEIVER proves arrival — head index advancing, Active: true:
kubectl -n mxl logs deploy/mxl-fabrics-demo-target -c info | grep -E 'Head index|Active|Latency'
```
**Success = the target flow is `Active: true` with its `Head index` climbing over time**
(it tracks the producer with a small grain/ms latency). Both pods are `hostNetwork` on
`eth0` (Aliyun VPC, full 1500 MTU) — unaffected by the cross-cloud flannel/MTU caveats.

## 5. Notes / next
- **`Recreate` strategy** on both Deployments: the pods are `hostNetwork` and bind host
  port `1234`; RollingUpdate would clash two pods on that port on one node.
- **Restart caveat:** the target regenerates target-info on (re)start; if the target
  pod restarts, redo §2→§3 with the new blob.
- **verbs/eRDMA (the kernel-bypass path):** flip `fabrics.provider`, use an
  eRDMA-capable instance family + RDMA device access in the pod (privileged / SR-IOV).
  **Open:** whether the `g8y`/Yitian-710 Arm family even exposes eRDMA is unverified —
  if not, verbs would force a non-Arm family.
- **Baseline:** `mxl-hello` (single-node shared-mem) uses the same demo functions; they
  now live in this one consolidated image too.
- **Teardown:** `helm uninstall mxl-fabrics`; the Aliyun nodes themselves teardown via
  `dmf-env bin/tf-apply.sh aliyun-media destroy`.
