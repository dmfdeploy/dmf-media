# Handoff: Workstream C — build + run the MXL Fabrics cross-host demo

**For:** a freshly-cleared agent picking up the MXL fabrics spike.
**Goal:** build the fabrics-enabled ARM image (image-preload approach), deploy the
`mxl-fabrics-demo` chart on the two Aliyun media nodes, run the target/initiator
handshake, and verify a cross-host grain transfer over the `tcp` provider. This is
**the actual MXL test** — everything up to here (nodes joined, cross-cloud networking)
is done and verified.

Branch for ALL work: `feat/mxl-single-node-spike` (must NOT merge to `main`).

> **✅ DONE (2026-05-30) — cross-host transfer is GREEN.** This handoff's image-preload
> path was superseded: we built ONE consolidated image
> (`ghcr.io/dmfdeploy/mxl-fabrics-demo:v1.0.2-fabrics-dev`, public — nodes pull
> anonymously) carrying the fabrics demo **and** the demo media functions
> (`mxl-gst-testsrc`/`info`/`fake-reader`), then ran the two-phase deploy. Test-pattern
> grains flow media-02 → libfabric **tcp** over **eth0** → media-01 (receiver Active,
> head advancing). NOTE: the data-plane NIC is **eth0**, not eth1 (single VPC NIC on
> g8y.large). See `mxl-fabrics-runbook.md` for the as-built build→deploy→verify steps.

---

## 0. Read these first (full context, in order)
1. **Agent memory** — `MEMORY.md` index in this project's memory dir, esp.
   `mxl-fabrics-spike-plan.md` (the whole evolving story) and
   `repo-privacy-and-scrub-policy.md` (the IP/scrub rules — important, see §6).
2. **As-built plan** — `dmf-infra/docs/mxl-media-nodes-plan.md` (AS-BUILT section:
   every divergence, the per-node `/32` cross-cloud solution, MTU, tolerations).
3. **The runbook** — `dmf-media/docs/mxl-fabrics-runbook.md` (build → deploy →
   verify; this handoff expands its §1 image step into the preload path).
4. **Cluster-access skill** — `~/repos/dmfdeploy/.claude/skills/dmf-cluster-access/SKILL.md`
   (how to SSH/kubectl the live cluster; **§0 Secrets Discipline — read every time**).

## 1. What already works (don't redo)
- Two Aliyun Ubuntu 24.04 **ARM** nodes (`aliyun-media-01`, `aliyun-media-02`) are
  live k3s **agents** in the Hetzner cluster `g2r6-foa9`, `Ready`, tainted
  `dmf.io/mxl=true:NoSchedule` + labeled `dmf.io/role=mxl-processor`.
- Cross-cloud pod networking, DNS, ClusterIP services, monitoring, and bulk TCP are
  all verified. node-exporter/promtail already run on the media nodes.
- A test pod tolerating both taints schedules + networks fine; images pull from
  docker.io on the media nodes (they have a working `--netfilter-mode=off` net path).

## 2. The artifacts you'll use (all already committed)
| Path | What |
|---|---|
| `dmf-media/docker/mxl-fabrics/Dockerfile` | Fabrics ARM image (libfabric + `-DMXL_ENABLE_FABRICS_OFI=ON` + `mxl-fabrics-demo` stage) |
| `dmf-media/charts/mxl-fabrics-demo/` | Helm chart: target + initiator (hostNetwork on eth1, both taints tolerated, two-phase handshake) |
| `dmf-media/catalog/mxl-fabrics-demo.yaml` | Catalog entry |
| `dmf-media/docs/mxl-fabrics-runbook.md` | Build/deploy/verify runbook |
| `~/repos/dmfdeploy/mxl/` | **MXL upstream source** = the Docker build context (has CMakeLists.txt, lib/, tools/, vcpkg.json) |

## 3. Cluster + node access (NO secrets in this doc — see where real values live)
- **Real IPs live in `dmf-env`** (private, gitleaks-allowlisted). Media-node public
  IPs: `dmf-env/inventories/aliyun-media/hosts.ini` (tofu-generated, gitignored —
  run `bin/tf-apply.sh aliyun-media output` or read it locally). Hetzner control-node
  tailnet IP: `dmf-env/inventories/aliyun-media/group_vars/all/main.yml`
  (`k3s_server_url` / `mxl_media_control_ssh_ip`).
- **SSH to media nodes:** `ssh -i ~/.ssh/id_ed25519_k3s_aliyun k3s-admin@<media-public-ip>`
  (host-key checking off for these throwaway nodes is fine).
- **kubectl** (read/verify): SSH to the g2r6-foa9 control node
  (`ssh -i ~/.ssh/id_ed25519_k3s_hetzner k3s-admin@<ctl-tailnet-ip>`) and use
  `sudo kubectl --kubeconfig /etc/rancher/k3s/k3s.yaml ...` — per the dmf-cluster-access
  skill. Your Mac is on the tailnet (`tailscale status`), so the tailnet IPs are reachable.

## 4. THE TASK — build (image-preload) + run
The media nodes are ARM and have k3s **containerd** but no image builder. Image-preload
= build on a node, import straight into each node's containerd (no registry, sidesteps
Zot/ghcr). The build is a real C++ compile (vcpkg deps + libfabric) — budget time on a
2-core node.

### 4a. Build the fabrics image on a media node
```bash
# 1. install a builder on the media node (Ubuntu)
ssh -i ~/.ssh/id_ed25519_k3s_aliyun k3s-admin@<media01-public> 'sudo apt-get update && sudo apt-get install -y podman'

# 2. ship the MXL source + Dockerfile to the node (build context = the MXL source root)
rsync -a --exclude build ~/repos/dmfdeploy/mxl/ k3s-admin@<media01-public>:/home/k3s-admin/mxl-src/
scp dmf-media/docker/mxl-fabrics/Dockerfile k3s-admin@<media01-public>:/home/k3s-admin/mxl-src/Dockerfile.fabrics

# 3. build (ARM64 native; long). Target the mxl-fabrics-demo stage.
ssh ... k3s-admin@<media01-public> '
  cd /home/k3s-admin/mxl-src
  sudo podman build --target mxl-fabrics-demo -f Dockerfile.fabrics \
    -t localhost/mxl-fabrics-demo:v1.0.1-fabrics-dev .'
```

### 4b. Preload into containerd on BOTH media nodes
```bash
# on media01: export, import to k3s containerd (k8s.io namespace)
ssh ... k3s-admin@<media01-public> '
  sudo podman save localhost/mxl-fabrics-demo:v1.0.1-fabrics-dev -o /tmp/mxlf.tar
  sudo k3s ctr -n k8s.io images import /tmp/mxlf.tar'
# copy the tar to media02 and import there too (or rebuild on media02)
scp k3s-admin@<media01-public>:/tmp/mxlf.tar /tmp/mxlf.tar
scp /tmp/mxlf.tar k3s-admin@<media02-public>:/tmp/mxlf.tar
ssh ... k3s-admin@<media02-public> 'sudo k3s ctr -n k8s.io images import /tmp/mxlf.tar'
```

### 4c. Deploy phase 1 — the target (pullPolicy: Never, preloaded image)
```bash
helm upgrade --install mxl-fabrics dmf-media/charts/mxl-fabrics-demo \
  --set image.registry=localhost --set image.repository=mxl-fabrics-demo \
  --set image.tag=v1.0.1-fabrics-dev --set image.pullPolicy=Never \
  --set target.nodeName=aliyun-media-01
# read the target-info the target prints:
kubectl -n mxl logs deploy/mxl-fabrics-demo-target | sed -n '/target-info/,+2p'
```

### 4d. Deploy phase 2 — the initiator (paste the target-info)
```bash
helm upgrade mxl-fabrics dmf-media/charts/mxl-fabrics-demo --reuse-values \
  --set initiator.enabled=true --set initiator.nodeName=aliyun-media-02 \
  --set initiator.targetInfo='<paste blob>'
```

### 4e. Verify (success criteria)
```bash
kubectl -n mxl logs deploy/mxl-fabrics-demo-initiator   # grain transfer activity
kubectl -n mxl logs deploy/mxl-fabrics-demo-target      # target receiving
```
**Success = a grain transfer completes across the two hosts over eth1 (tcp provider).**
Both pods are `hostNetwork` on eth1 (Aliyun VPC, full MTU) — unaffected by the
cross-cloud flannel/MTU caveats.

## 5. Also: the shared-mem baseline
Run `mxl-hello` on a media node too (single-node shared-memory comparison) — chart at
`dmf-media/charts/mxl-hello/`, set its `placement.nodeSelector`/`tolerations` to the
media node + both taints (see catalog §5). Note mxl-hello images are at
`ghcr.io/dmfdeploy/mxl-*:v1.0.1-dev` (non-fabrics) and pull fine cross-cloud now.

## 6. Rules / gotchas (don't trip these)
- **Privacy/scrub:** env-specific IP literals go ONLY in `dmf-env` (private, allowlisted).
  `dmf-infra` + `dmf-media` are gitleaks-enforced — a real `10.x/172.16-31.x/192.168.x/
  100.64-127.x` literal in a committed file there **blocks the commit**. Use placeholders
  / point at `dmf-env`. (This handoff itself follows that.)
- **§0 secrets discipline** (dmf-cluster-access skill): never echo/cat a secret through
  the agent; the operator types secrets; rotate anything that leaks.
- **Tolerations are mandatory** on any media-node pod: `dmf.io/mxl=true:NoSchedule` AND
  `node.kubernetes.io/network-unavailable` (the chart already has both).
- **target-info handshake is manual/two-phase** — and the target regenerates it on
  restart; if the target pod restarts, redo 4c→4d.
- **MTU:** cross-cloud TCP is fine (verified). The fabrics path is eth1 hostNetwork
  (same-VPC, 1500 MTU) so it's irrelevant there anyway.
- **No registry needed** for the fabrics image with preload; if you'd rather use a
  registry, the public `ghcr.io/dmfdeploy` is reachable cross-cloud — but preload is simpler for a 2-node spike.

## 7. Deferred / open
- **verbs/eRDMA** provider: needs an eRDMA-capable instance family + RDMA device access
  (privileged/SR-IOV) in the pod; flip `fabrics.provider`. Out of scope for first test.
- **Dockerfile arch:** the final `COPY` uses the `aarch64-linux-gnu` triplet (ARM). If
  building x86 anywhere, swap to `x86_64-linux-gnu`.
- **Build deps to watch:** `libfabric-dev` (apt) + vcpkg deps (catch2/spdlog/fmt/etc.)
  resolve at build; if vcpkg bootstrap is slow/fails, that's the gating risk.
- **Persist g2r6-foa9 /32 routes in the umbrella** (`~/repos/dmfdeploy` g2r6-foa9
  group_vars) — the spike tree has the line but the umbrella checkout (where `321` runs)
  needs it mirrored. Not required for the fabrics test.
