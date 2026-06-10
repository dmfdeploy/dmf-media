# Role layout — EBU DMF mapping

This repo owns the media-domain layers of the EBU DMF Reference Architecture
V2.0 (2026-04-15) — Layer 4 (Media Exchange) and Layer 5 (Media Functions).

| Role directory | EBU scope |
|---|---|
| (future) `mxl-prereq/`, `libfabric/` | Layer 4 — Media Exchange (MXL SDK, RDMA fabric, shared memory) |
| `ebu-list/` | Layer 5 — Media Functions (2110 packet analysis) |
| `flow-exporters/` | Layer 5 — Media Functions (per-flow telemetry) |
| `ptp-monitor/` | Layer 5 — Media Functions (PTP topology + offset) |
| `netbox-media-plugin/` | Layer 5 — Media Functions (sender/receiver/flow schema) |
| `media-controllers/` | Layer 5 — Media Functions (control plane glue) |

> **`nmos-cpp/` was relocated to [`dmf-runbooks/roles/nmos-cpp/`](https://forgejo.dmf.example.com/forgejo-svc/dmf-runbooks)
> on 2026-05-06.** AWX-driven catalog launchers run from the dmf-runbooks
> AWX project; that repo now owns the role implementation. The `catalog/`
> entry definition (`catalog/nmos-cpp.yaml`) remains here as the catalog-
> source-of-truth metadata. See ADR-0014 (multi-project AWX layout) and
> ADR-0025 (in-cluster EE pod + Helm chart for media catalog launchers).

All roles are stubs until media hardware lands. Layer 1–3 (Infrastructure,
Host Platform, Container Platform) and verticals (Security, Monitoring,
Orchestration, Control) are inherited from the base platform deployed by
`dmf-infra`.

See `dmfdeploy/docs/architecture/DMF EBU Mapping (2026-04-25).md` for the full canon.
