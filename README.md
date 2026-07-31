# dmf-media

Media-domain modules for the DMF Platform — NMOS IS-04/05 discovery, AMWA BCP compliance,
EBU LIST 2110 packet analysis, PTP topology monitoring, flow-level exporters, and the
NetBox media plugin (sender/receiver/flow schema). That list is this repo's full reserved
scope, not a status report — most of it is still unimplemented. See
[Status](#status) below for what actually runs today.

## Dependencies

- `dmf-infra` — base platform (k3s, networking, storage, observability)
- `dmf-env` — generic environment provisioning + bootstrap tooling

## Structure

```
catalog/        — Function-catalog entries (YAML) + topology-params schema/instance
charts/         — Helm charts: mxl-fabrics-demo, nmos-cpp, nmos-crosspoint
docker/         — Dockerfiles: the compiled upstream MXL image (mxl-fabrics), nmos-crosspoint
bin/            — CI gates + publish scripts (catalog-demand check, GHCR chart/image publish)
tests/          — Render-matrix harness with golden manifests (tests/harness/)
playbooks/      — lifecycle-operate.yml (catalog drift detector); the eight numbered
                  EBU playbooks (400-599) are stubs
roles/          — Media-domain Ansible roles (all stubs until Phase 3)
docs/           — Design docs and runbooks
```

## Building the mxl-fabrics image

`docker/mxl-fabrics/Dockerfile` builds the consolidated MXL demo image
against an **operator-supplied upstream MXL checkout** as build context.

The Dockerfile `COPY`s five files that live in *this* repo, not upstream —
so they must be staged into the checkout (the build context) first:

```bash
cp docker/mxl-fabrics/{MXL_UPSTREAM_SHA,mxl_build_prep.py,patch-sink.py,\
patch-sink-too-early-pacing.py,mxl-status-server.py} <mxl-checkout>/
```

For reproducibility (umbrella dmfdeploy/dmfdeploy#315), the expected
upstream SHA is recorded in `docker/mxl-fabrics/MXL_UPSTREAM_SHA`. The build
**must** be invoked with the actual checkout's SHA:

```bash
docker build \
  --build-arg MXL_SOURCE_SHA=$(git -C <mxl-checkout> rev-parse HEAD) \
  -f docker/mxl-fabrics/Dockerfile \
  <mxl-checkout>
```

`mxl_build_prep.py`'s `verify_upstream_sha()` compares that build-arg
against `MXL_UPSTREAM_SHA` before either build-time patch script
(`patch-sink.py`, `patch-sink-too-early-pacing.py`) touches a source file,
and fails loudly (both SHAs, expected-SHA file, bump procedure) on
mismatch.

**Bumping the pinned SHA** (deliberate upstream move): update
`docker/mxl-fabrics/MXL_UPSTREAM_SHA` to the new SHA, then rebuild —
`patch-sink.py` and `patch-sink-too-early-pacing.py` fail on their own
anchors if upstream moved the code they patch, which is the content-drift
guard; the SHA check only guards which commit was built.

## Status

This is not a scaffold-only repo. Real work lives here: the function catalog
(`catalog/` — four entries plus the `topology_params` schema and a concrete
J1 instance), the three Helm charts (`charts/`), the compiled-from-source
upstream MXL image (`docker/mxl-fabrics/`, SHA-pinned via
`docker/mxl-fabrics/MXL_UPSTREAM_SHA` and verified fail-closed by
`mxl_build_prep.py` before any patch script touches a source file), and the
catalog drift detector (`playbooks/lifecycle-operate.yml`), which checks that
a catalog entry's declared `lifecycle:active`/`lifecycle:bootstrapped` state
matches what is actually deployed.

**Media roles are stubs.** All five `roles/*/tasks/main.yml` entries
(`ebu-list`, `flow-exporters`, `ptp-monitor`, `netbox-media-plugin`,
`media-controllers`) are 3-line TODO placeholders, and the eight numbered EBU
playbooks (`400-mxl-prereq.yml` through `599-media-functions-verify.yml`) are
each explicitly self-labelled `STUB`. That reserved Layer 4/5 skeleton
targets DMF Platform Plan Phase 3 and is not implemented yet.

The real media-domain capability runs through a different surface than that
reserved skeleton: this repo's catalog, charts, and MXL image feed the
launch/switch/teardown playbooks that live in `dmf-runbooks`, not here. On
2026-07-30, the operator console drove a live source switch (source-a →
source-b) through `dmf-runbooks` 0.4.3's `switch-mxl-fabrics-demo.yml` on the
deployed `mxl-fabrics-demo` chart — two sources, one viewer, on a single ARM
node (the standing single-node lane); the Helm release advanced cleanly to
revision 2 with the target pod running and no restarts. That is the current
proven scale; nothing broader (multi-node or cross-host) has been
demonstrated.

## License

This project is licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for the full text. Upstream attribution is documented in [NOTICE](NOTICE).
