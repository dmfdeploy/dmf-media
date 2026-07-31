# dmf-media

Media-domain modules for the DMF Platform — NMOS IS-04/05 discovery, AMWA BCP compliance,
EBU LIST 2110 packet analysis, PTP topology monitoring, flow-level exporters, and the
NetBox media plugin (sender/receiver/flow schema).

## Dependencies

- `dmf-infra` — base platform (k3s, networking, storage, observability)
- `dmf-env` — generic environment provisioning + bootstrap tooling

## Structure

```
roles/          — Media-domain Ansible roles (all stubs until Phase 3)
charts/         — Helm charts for media components
playbooks/      — Media deployment playbooks
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

Scaffold only. Media roles are stubs. Content lands in Phase 3 of the DMF Platform Plan.

## License

This project is licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for the full text. Upstream attribution is documented in [NOTICE](NOTICE).
