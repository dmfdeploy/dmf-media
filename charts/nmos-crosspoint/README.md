# nmos-crosspoint Helm chart

NMOS IS-04/IS-05 crosspoint controller with BCP-007-03 MXL support
([Gemini2350/nmos-crosspoint_v3](https://github.com/Gemini2350/nmos-crosspoint_v3)),
packaged as a DMF media function (ADR-0013 catalog model, ADR-0025 Lane B
in-cluster Helm). Deployed by the `media-launch-nmos-crosspoint` AWX job template
via `dmf-runbooks/playbooks/launch-nmos-crosspoint.yml`.

## The image: by digest only

The chart runs the fork's published image, unmodified, pinned by digest with
its source commit recorded beside it (`image.digest`, `image.commit`). That is
the image the IS-05 A/B crosspoint slice tested
([dmfdeploy/dmfdeploy#629](https://github.com/dmfdeploy/dmfdeploy/issues/629)):

- `tests/is05-ab-fixture/crosspoint/lines.json` pins each controller line.
- `tests/is05-ab-fixture/crosspoint/check_pins.py` fails unless this chart's
  digest and commit are a pinned line, and a recorded slice run that deployed
  the controller from this chart (`reference/crosspoint-chart/`) used the same
  digest and passed.
- The template fails on a tag, on an `image.tag` value, or on anything that is
  not `sha256:` plus 64 hex digits.

To move the controller to another commit or line, change `lines.json` and
`values.yaml` together, run the slice for the line and from the chart, and
commit both runs' observations. dmf-infra playbook 630 mirrors the same digest
into the cluster-internal Zot, with every manifest of the index, so the digest
in Zot is the one tested here.

## What it deploys

| Object | Purpose |
|---|---|
| Deployment `nmos-crosspoint` | Node server and web UI, non-root (uid 1000), read-only root filesystem, listening on `containerPort` (8080) |
| Service `nmos-crosspoint` | ClusterIP, port 80 → the container's `http` port |
| IngressRoute `nmos-crosspoint` | Private (Tailscale) lane via `traefik-private` |
| ConfigMap `nmos-crosspoint-config` | `settings.json` (IS-04 registry under `staticNmosRegistries`) |
| Secret `nmos-crosspoint-auth` | `users.json` (admin SHA256 from OpenBao; `__noAuth` write removed) |

The server rewrites `./config/settings.json` at startup, so its config
directory is writable. An init container copies the rendered `settings.json`
and `users.json` into an `emptyDir` at `/nmos-crosspoint/server/config`; the
image's own entrypoint then starts the server. `state/`, `log/` and `/tmp` are
`emptyDir` volumes too.

The controller subscribes to the registry's IS-04 Query API over a WebSocket on
the port the registry names in its `ws_href`. The nmos-cpp chart's registry
Service exposes it (`httpPort` + 1). If that port is unreachable, the
controller process exits and restarts.

## Key values

| Key | Default | Notes |
|---|---|---|
| `image.repository` | `zot.zot.svc.cluster.local:5000/dmf/nmos-crosspoint` | cluster-internal Zot; no tag |
| `image.digest` | the tested index digest | the only image selector |
| `image.commit` | `fa31061c…` | provenance, not used by the templates |
| `containerPort` | `8080` | settings.json `server.port`; non-root cannot bind 80 |
| `service.port` | `80` | unchanged; the catalog probe targets it |
| `registry.ip` | `nmos-cpp-registry.nmos.svc.cluster.local` | **bare hostname** (no scheme/port) |
| `registry.port` | `80` | |
| `ingress.host` | `nmos-xp.dmf.example.com` | override per env |
| `ingress.ingressClass` | `traefik-private` | private lane binding |
| `auth.adminPasswordSha256` | placeholder | injected by the launcher role at deploy time — never commit a real hash |

## Scope

The controller discovers NMOS senders and receivers from the registry and
connects them over IS-05, including BCP-007-03 MXL senders and receivers. The
console does not switch through it yet; the AWX reconnect stays the default
switching path. See the
[plan](https://github.com/dmfdeploy/dmfdeploy/blob/45eb326e973b10e0b52deef290981ceac9bcf5bd/docs/plans/DMF%20NMOS%20Crosspoint%20Media%20Function%20Plan%202026-06-21.md)
and tracking issues dmfdeploy/dmfdeploy#108 and dmfdeploy/dmfdeploy#629.
