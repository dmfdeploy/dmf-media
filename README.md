# dmf-media

[![CI](https://github.com/dmfdeploy/dmf-media/actions/workflows/ci.yml/badge.svg)](https://github.com/dmfdeploy/dmf-media/actions/workflows/ci.yml)

The media-domain content of the dmfdeploy platform: a function catalog, Helm charts for NMOS and MXL workloads, a SHA-pinned MXL image build and a catalog drift detector.
Part of [dmfdeploy](https://github.com/dmfdeploy/dmfdeploy), an independent open prototype that explores ideas from the [EBU](https://tech.ebu.ch/) *Dynamic Media Facility* publications. Status: prototype (VERSION 0.1.0), single-node; the media roles are stubs.

**Planned scope, not yet implemented:** NMOS IS-04/05 discovery, AMWA BCP compliance checks, EBU LIST 2110 packet analysis, PTP topology monitoring, flow exporters and a NetBox media plugin. See [Status](#status) for what runs today.

## Dependencies

- `dmf-infra`: base platform (k3s, networking, storage, observability)
- `dmf-env`: generic environment provisioning + bootstrap tooling

## Structure

```
catalog/        — Function-catalog entries (YAML) + topology-params schema/instance
charts/         — Helm charts: mxl-fabrics-demo, nmos-cpp, nmos-crosspoint
docker/         — Dockerfiles: the compiled upstream MXL image (mxl-fabrics)
bin/            — CI gates + publish scripts (catalog-demand check, GHCR chart/image publish)
tests/          — Harness scripts (tests/harness/): the acceptance-gate proof
                  (run-demo.sh) and the render matrix (render-matrix.sh), plus
                  their PATH stubs; both write generated, untracked output
playbooks/      — lifecycle-operate.yml (catalog drift detector); the eight numbered
                  EBU playbooks (400-599) are stubs
roles/          — Media-domain Ansible roles (all stubs until Phase 3)
docs/           — Design docs and runbooks
```

## Building the mxl-fabrics image

`docker/mxl-fabrics/Dockerfile` builds the consolidated MXL demo image
against an **operator-supplied upstream MXL checkout** as build context.

The Dockerfile `COPY`s five files that live in *this* repo and not in upstream.
Stage them into the checkout (the build context) first:

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
`docker/mxl-fabrics/MXL_UPSTREAM_SHA` to the new SHA, then rebuild.
`patch-sink.py` and `patch-sink-too-early-pacing.py` fail on their own
anchors if upstream moved the code they patch, which is the content-drift
guard; the SHA check only guards which commit was built.

## Status

This is not a scaffold-only repo. Real work lives here: the function catalog
(`catalog/`: one function entry, `mxl-videotest-view`, plus the
`topology_params` schema and a concrete J1 instance), the three Helm charts (`charts/`),
the compiled-from-source upstream MXL image (`docker/mxl-fabrics/`, SHA-pinned via
`docker/mxl-fabrics/MXL_UPSTREAM_SHA`). `mxl_build_prep.py` verifies the pin
fail-closed before any patch script touches a source file. The fourth piece is the
catalog drift detector (`playbooks/lifecycle-operate.yml`), which asserts that
a declared `lifecycle:active`/`lifecycle:bootstrapped` state matches what is
actually deployed. In v1 that detector checks exactly one entry, `nmos-cpp`,
hardcoded in the playbook's `_catalog_entries`. Loading the declarations
from `catalog/*.yaml` is explicitly deferred (the playbook says so in its own
comment), so the catalog entries are not yet covered.

**Media roles are stubs.** All five `roles/*/tasks/main.yml` entries
(`ebu-list`, `flow-exporters`, `ptp-monitor`, `netbox-media-plugin`,
`media-controllers`) are 3-line TODO placeholders, and the eight numbered EBU
playbooks (`400-mxl-prereq.yml` through `599-media-functions-verify.yml`) are
each explicitly self-labelled `STUB`. That reserved Layer 4/5 skeleton
targets Phase 3 of the platform plan and is not implemented yet.

The real media-domain capability runs through a different surface than that
reserved skeleton: this repo's catalog, charts, and MXL image feed the
launch/switch/teardown playbooks that live in `dmf-runbooks`.
The last recorded check is from 2026-07-30. The operator console drove a live
source switch (source-a to source-b) through the
`switch-mxl-fabrics-demo.yml` playbook in `dmf-runbooks` on the deployed
`mxl-fabrics-demo` chart: two sources, one viewer, on a single ARM node. This
repo does not re-run that check on each release, so treat it as a dated
observation. Nothing broader (multi-node or cross-host) has been demonstrated.

## Repository layout and contributors

`AGENTS.md`, `CLAUDE.md` and `QWEN.md` hold instructions for AI coding agents that help maintain this repository.

## License

This project is licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for the full text. Upstream attribution is documented in [NOTICE](NOTICE).
