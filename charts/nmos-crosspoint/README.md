# nmos-crosspoint Helm chart

AMWA NMOS IS-04/05 crosspoint routing UI ([3dmedium/nmos_crosspoint](https://github.com/3dmedium/nmos_crosspoint))
packaged as a DMF media function (ADR-0013 catalog model, ADR-0025 Lane B
in-cluster Helm). Deployed by the `media-launch-nmos-crosspoint` AWX job template
via `dmf-runbooks/playbooks/launch-nmos-crosspoint.yml`.

## What it deploys

| Object | Purpose |
|---|---|
| Deployment `nmos-crosspoint` | Svelte UI + Node/Express WebSocket server (port 80) |
| Service `nmos-crosspoint` | ClusterIP |
| IngressRoute `nmos-crosspoint` | Private (Tailscale) lane via `traefik-private` |
| ConfigMap `nmos-crosspoint-config` | `settings.json` (IS-04 registry under `staticNmosRegistries`) |
| Secret `nmos-crosspoint-auth` | `users.json` (admin SHA256 from OpenBao; `__noAuth` write removed) |

The ConfigMap + Secret are merged into `/app/server/config` via a projected
volume — the server reads `./config/settings.json` and `./config/users.json`.

## Key values

| Key | Default | Notes |
|---|---|---|
| `registry.ip` | `nmos-cpp-registry.nmos.svc.cluster.local` | **bare hostname** (no scheme/port) |
| `registry.port` | `80` | |
| `ingress.host` | `nmos-xp.dmf.example.com` | override per env |
| `ingress.ingressClass` | `traefik-private` | private lane binding |
| `auth.adminPasswordSha256` | placeholder | injected by the launcher role at deploy time — never commit a real hash |
| `image.repository` | `zot.zot.svc.cluster.local:5000/dmf/nmos-crosspoint` | cluster-internal Zot |

## Scope

Phase 1 deploys the routing UI; it discovers and **lists** NMOS senders/receivers
from the registry. Switching real flows depends on the registered devices
advertising routable IS-05 control hrefs — see the
[plan](../../../dmfdeploy/docs/plans/DMF%20NMOS%20Crosspoint%20Media%20Function%20Plan%202026-06-21.md)
and tracking issue dmfdeploy/dmfdeploy#108.
