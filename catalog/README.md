# Function Catalog

This directory contains the function catalog for the DMF media platform.
Each YAML manifest describes a deployable media function — its EBU
placement, artifacts, lifecycle playbooks, and dependencies.

## Architecture reference

The authoritative schema is defined in the umbrella repo:

- `docs/architecture/DMF Function Catalog Model.md` — full schema,
  YAML-vs-NetBox split rationale, lifecycle integration
- `docs/decisions/0013-media-function-catalog-model.md` — ADR-0013

## Schema overview

Each file is named `<key>.yaml` where `key` is the unique function
identifier threaded through NetBox tags, AWX job templates, and dmf-cms
button IDs.

### Required fields

| Field | Type | Description |
|---|---|---|
| `key` | string | Unique handle within the platform |
| `display_name` | string | Operator-visible name in dmf-cms |
| `summary` | string | 1–2 sentence description (block scalar `|` preferred) |
| `ebu.layer` | int | EBU layer number (typically 4, 5, or 6) |
| `ebu.vertical` \| `ebu.media_function_type` | string | **Exactly one of** (see fail-closed rule below). `vertical` ∈ {`orchestration`, `control`, `monitoring`, `security`} for support/control functions; `media_function_type` ∈ {`source`, `view`, `processor`, `mixer`, `output`, `render`, `gfx`, `multiviewer`} for media-processing functions (ADR-0046) |
| `ebu.lifecycle_owner` | string | Which wrapper drives launch: `provision` or `configure` |
| `provision.namespace` | string | Kubernetes namespace where the workload's Helm release is deployed. Source of truth for drift detection (catalog-drift-check) and any operator that needs to locate the workload. Added 2026-05-12 per decision `catalog-namespace-source-of-truth` (Option A). |
| `provision.image.repository` | string | Container image registry path |
| `provision.image.digest` | string | SHA256 digest (must be a digest, not a tag) |
| `provision.chart.name` | string | Helm chart name |
| `provision.chart.version` | string | Helm chart semver |
| `provision.chart.source` | string | OCI registry URL for the chart |
| `provision.resources.requests` | mapping | *Optional.* Aggregate EFFECTIVE workload demand for the entry's role-pod — NOT a per-container `ResourceRequirements` object. Computed as the steady-state container sum + pod overhead (if any), × replicas, summed across the chart's rendered Deployments for this role. `initContainers` are refused fail-closed by the CI gate — Kubernetes' real init-container scheduling accounting is not a simple sum, and this gate does not approximate it; scheduler-accurate init accounting must be added deliberately the first time a chart actually needs one. Accepted quantity grammar is narrow and fails closed, and is stricter here than in the chart itself: `cpu` must be whole millicores with an explicit `m` suffix (`"225m"`) — a bare integer is refused as a likely-forgotten `m`, not accepted as whole cores; `memory` must be whole binary `Ki`\|`Mi`\|`Gi` (`"320Mi"`). Consumed by the L3 console capacity preflight (umbrella dmfdeploy/dmfdeploy#202 WP0); equality-gated against the live chart render in CI by `bin/check-catalog-demand.py`, which does accept bare whole-core `cpu` (`"1"`, ×1000m) when parsing the chart's own rendered output — legal Kubernetes YAML the chart may emit, even though this catalog never authors it that way. A `view` entry carrying a `topology_ref` field (an optional filename naming a `topology_params` instance in this same directory — see `docs/topology-params-seam.md`) does NOT declare its topology-launched sources' demand here — that shared per-source profile lives in the referenced topology instance's own `topology_params.source_profile` (same grammar), gated separately, and reported as `viewer_profile + len(sources[]) x source_profile` (umbrella dmfdeploy/dmfdeploy#347; see `docs/topology-params-seam.md`). |
| `provision.netbox_service.name` | string | NetBox `ipam.Service` record name |
| `provision.netbox_service.protocol` | string | Protocol (`tcp` or `udp`) |
| `provision.netbox_service.ports` | list[int] | Port numbers |
| `provision.netbox_service.parent_object` | string | Generic-relation target type |
| `provision.netbox_service.tags` | list[str] | Must include exactly one `lifecycle:*` tag |
| `configure.playbook` | string | Path to launch playbook within this repo |
| `configure.awx_job_template` | string | AWX job template name |
| `configure.on_success_tag` | string | NetBox tag to flip on successful launch |
| `configure.health_probe.kind` | string | Probe type (`http` for v1) |
| `configure.health_probe.path` | string | HTTP path for health check |
| `configure.health_probe.expect_status` | int | Expected HTTP status code |
| `monitoring` | mapping | ADR-0038 monitoring intent (`scrape`, `probe`, `snmp`) |
| `finalise.playbook` | string | Path to teardown playbook within this repo |
| `finalise.awx_job_template` | string | AWX job template name for teardown |
| `finalise.on_success_tag` | string | NetBox tag to flip on successful teardown |
| `dependencies` | list[str] | List of keys this function depends on (informational v1) |

## Classification fail-closed rule (ADR-0046 decision 6)

Exactly **one** of `ebu.vertical` or `ebu.media_function_type` must be
present per catalog entry — neither-both-nor-neither. Each value must
fall within its enum:

- `ebu.vertical` ∈ {`orchestration`, `control`, `monitoring`, `security`}
- `ebu.media_function_type` ∈ {`source`, `view`, `processor`, `mixer`,
  `output`, `render`, `gfx`, `multiviewer`}

A *media-processing* function sets `media_function_type`; a
*support/control* function sets `vertical`. The console's catalog
loader (dmf-cms) enforces this fail-closed: entries violating the rule
are rejected with an error-level log and excluded from the API response.

## Monitoring extension (ADR-0038)

The `monitoring:` block declares whether the function should be scraped,
probed, or polled over SNMP. The launcher translates the block into the
NetBox monitoring contract described in ADR-0038.

For `nmos-cpp`, monitoring is probe-only:

```yaml
monitoring:
  scrape:
    enabled: false
  probe:
    enabled: true
    probe_module: http_2xx
  snmp:
    enabled: false
```

The NetBox-side launcher then stamps `monitoring:probe` and
`probe_module` on the catalog `ipam.Service` record via the scoped
`dmf-catalog-svc` writer. Scrape annotations are not emitted because the
chart does not expose a Prometheus metrics port.

## YAML vs NetBox split

Two stores answer two different questions:

| Question | Source of truth |
|---|---|
| What functions exist in the platform? | YAML manifests here |
| Which are currently deployed in this cluster? | NetBox `ipam.Service` tag |
| What does the function look like (image, chart, schema)? | YAML manifests here |
| What endpoint is the deployed instance reachable at? | NetBox `ipam.Service` |

Convention: when YAML and NetBox disagree, **NetBox is the truth about
runtime state, YAML is the truth about intent.** Drift means a Configure
or Finalise playbook did not complete — it is an alert, not a normal state.

## Adding a new catalog entry

1. Copy an existing entry (e.g. `nmos-cpp.yaml`) as a starting point.
2. Update all fields per the schema above — `key`, `display_name`, image,
   chart, NetBox service, playbook paths, and AWX job template names.
3. Create the referenced playbooks under `playbooks/configure-media/`.
4. Register the entry in `lifecycle-provision.yml` and
   `lifecycle-configure.yml` (in dmf-infra).
5. Create AWX job templates via the `awx-integration` role.
6. Run `lifecycle-provision.yml` to bootstrap the entry.

See the architecture reference for the full lifecycle flow.
