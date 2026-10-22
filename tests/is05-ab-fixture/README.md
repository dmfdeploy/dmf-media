# IS-05 A/B MXL fixture

This fixture tests one assumption behind IS-05 switching for MXL media
functions: a viewer-side NMOS node can observe a resolved `/active` change, and
a local supervisor can then replace only the preview child process. It is the
first item of [dmfdeploy/dmfdeploy#624](https://github.com/dmfdeploy/dmfdeploy/issues/624).
The sidecar chart, the crosspoint controller, the console, Fabrics replication
and AWX are out of scope.

## Run it

```sh
tests/is05-ab-fixture/run.sh [OUT_DIR]
```

The command creates a single-node kind cluster, runs every check, writes the
observations to `OUT_DIR` (default `tests/is05-ab-fixture/out/<UTC time>`) and
deletes the cluster. It exits 0 only when every acceptance item passes. A full
run takes about eight minutes on a 4-CPU ARM64 Docker host, most of it in the
nmos-testing suites.

You need Docker, kind, kubectl and Python 3.11 or later. The driver installs
Pillow into a temporary virtual environment. Set `SKIP_NMOS_TESTING=1` to skip
the second gate.

The script never touches a cluster it did not create. If a cluster named
`is05-ab` already exists, it stops with an error; set `KIND_CLUSTER` to an
unused name instead. The cluster's credentials live in a temporary kubeconfig
that the script deletes on exit, so your own kubeconfig and current context stay
unchanged. Set `KEEP_CLUSTER=1` to keep the cluster afterwards, then run
`kind export kubeconfig --name <cluster>` to reach it.

The `is05-ab-fixture` workflow runs the same command on a GitHub ARM64 runner
and uploads `OUT_DIR` as an artifact.

## What runs

All pods run as uid 1000 and share one MXL domain, a tmpfs on the node.

| Pod | Containers | Image (pinned by digest) |
|---|---|---|
| `source-a` | `mxl-gst-testsrc`, SMPTE bars, flow A | MXL demo image `v1.0.3` |
| `source-b` | `mxl-gst-testsrc`, checkers-8, flow B | MXL demo image `v1.0.3` |
| `nmos-registry` | nmos-cpp registry | `rhastie/nmos-cpp` `master-b6c8367` |
| `viewer` | nmos-cpp node with one MXL receiver; the supervisor | both of the above |

The nmos-cpp image is built from nmos-cpp `b6c8367`, which contains the MXL
pull requests #483, #503 and #521. The node is the stock `nmos-cpp-node` with
`"receivers": ["xv"]`; it needs no code change.

[`supervisor/mxl-is05-supervisor.py`](supervisor/mxl-is05-supervisor.py) polls
the node's own Connection API for the receiver's `/active` every 200 ms. When
the selected `mxl_flow_id` changes, it stops the `mxl-gst-sink` child, deletes
the last preview frame and starts a new child on the new flow. It serves
`/status` and `/preview.jpg` on port 9000. The fixture mounts it from a
ConfigMap, so the MXL image is unchanged.

## What it checks

[`run.py`](run.py) maps each acceptance item of the issue to one check:

1. **Two flows readable at once.** From inside the viewer pod, both flows'
   head indices advance, and two sinks read frames from A and B at the same
   time.
2. **Selection.** After each PATCH, `/staged` and resolved `/active` carry the
   selected `mxl_flow_id` and the domain's `mxl_domain_id`.
3. **Only the child is replaced.** Pod UID, container restart counts and start
   times, and the supervisor PID stay the same; each switch starts a new child
   PID whose predecessor is the previous child.
4. **Picture changes within a bound.** The driver reads `/preview.jpg` every
   100 ms and classifies each frame as A or B. It records the time from PATCH
   to the first frame of the new pattern, and the gap after the last frame of
   the old one, for twelve switches. The bound is 10 s. The result is a
   measured interruption, not a glitch-free claim.
5. **Heartbeat.** The registry's Query API returns the viewer node once a
   second for longer than the 12 s expiry.
6. **Negative case.** The viewer runs again with `SUPERVISE=0`, the static
   flow contract the shipped status server uses. `/active` moves to B, and the
   preview must not reach B within the bound.
7. **Non-root.** Both containers and the preview child run as a non-zero uid,
   and the pod sets `runAsNonRoot`.

The second gate runs AMWA nmos-testing (`master-6ad129b`, pinned by digest)
against the viewer node: the IS-05-01 suite and BCP-007-03-01. It is warn mode:
the results land in the observations and never change the exit status.

## Crosspoint slice

The crosspoint slice ([dmfdeploy/dmfdeploy#629](https://github.com/dmfdeploy/dmfdeploy/issues/629))
runs the same seven checks with the same bounds, but every switch is the
crosspoint controller's own connection call. The driver never sends a PATCH.

```sh
SWITCH_VIA=crosspoint tests/is05-ab-fixture/run.sh [OUT_DIR]
SWITCH_VIA=crosspoint CONTROLLER_LINE=qvest tests/is05-ab-fixture/run.sh [OUT_DIR]
SWITCH_VIA=crosspoint CONTROLLER_FROM_CHART=1 tests/is05-ab-fixture/run.sh [OUT_DIR]
```

With `CONTROLLER_FROM_CHART=1` (needs `helm`), the slice deploys the controller
by rendering [`charts/nmos-crosspoint`](../../charts/nmos-crosspoint) as it
ships: its digest, paths, user, init container and port. Only the repository
is pointed at the line's public source, because the fixture has no Zot
mirror. The run fails if the chart's digest is not the line's.

[`crosspoint/lines.json`](crosspoint/lines.json) pins each controller line by
source commit and image digest. The slice adds these pods:

| Pod | What | Image (pinned by digest) |
|---|---|---|
| `sender-a`, `sender-b` | Fixture-only nmos-cpp nodes, one MXL video sender each, no media | `rhastie/nmos-cpp` `master-b6c8367` |
| `crosspoint` | The controller, with its own entrypoint, non-root, read-only root filesystem | from `lines.json` |
| `capture` | `tcpdump` on the node, TCP port 8020; the one root pod | `nicolaka/netshoot` `v0.14` |

nmos-cpp derives a sender node's flow ID from its `seed_id`. The slice gives
each test source that flow ID, and it checks that the sender node's IS-04 flow,
its sender's `flow_id` and its IS-05 `/active` `mxl_flow_id` all agree.

The driver talks to the controller only through the controller's public
WebSocket API, as its web UI does. It logs in with a password generated for
each run, reads the device and flow numbers from the `crosspoint` sync channel,
and sends `makeconnection` ([`crosspoint/client.py`](crosspoint/client.py)).

On top of the seven items, the slice checks two things:

- The controller runs from a digest-only image as a non-zero uid, and both
  senders advertise their source's flow.
- Every PATCH the viewer's node received is the controller's: one per
  connection call, with the selected sender's `sender_id`, one leg of
  `mxl_domain_id` and `mxl_flow_id`, `master_enable: true`, immediate
  activation, and no transport file or SDP. The slice reads the PATCHes from
  the packet capture, so they are what arrived on the wire.

With nmos-testing on, the slice also runs BCP-007-03-01 against `sender-a`.

To move the controller to another line, commit or image, change
`lines.json` and the chart's `image.digest` and `image.commit`, run the slice
for that line and from the chart, and commit the observations as
`reference/crosspoint-<line>/` and `reference/crosspoint-chart/`. [`crosspoint/check_pins.py`](crosspoint/check_pins.py)
fails until the recorded runs used the same commit and image and passed, for
every line and for the chart. The fixture workflow runs the pin check, the
slice for every line, and the slice from the chart; a change under
`charts/nmos-crosspoint` triggers it too.

## Reference observations

[`reference/`](reference/) holds the observations from the run recorded as the
reference for the crosspoint adoption work: `observations.json`, both
nmos-testing result files, and preview frames before and after a switch and
from the negative case.

[`reference/crosspoint-gemini/`](reference/crosspoint-gemini/) and
[`reference/crosspoint-qvest/`](reference/crosspoint-qvest/) hold the slice's
recorded runs for each controller line, and
[`reference/crosspoint-chart/`](reference/crosspoint-chart/) the run from the
chart: `observations.json`, the PATCHes the
viewer's node received (`captured-patches.json`, pod addresses replaced by
names), the nmos-testing results for the viewer and `sender-a`, and preview
frames. `check_pins.py` compares `lines.json` against these files.

`reference/crosspoint-gemini/sbom-arm64.spdx.json.gz` is an SPDX 2.3 SBOM of
the pinned controller's arm64 image, `sha256:27d9dfbb…`, the arm64 manifest in
the index that `lines.json` pins. The file records the tool and the time it was
generated. Read it with `gunzip -c`.

## Notes

- The viewer in `nmos/node.json` advertises the format of the flows it reads
  (960x540, 30000/1001, progressive), because the pinned controller checks a
  receiver's BCP-004 caps before it connects a sender. The observations in
  `reference/` were recorded with the nmos-cpp default caps (1920x1080, 25 fps,
  interlaced). They don't include caps, and they weren't re-recorded.
- The node reaches the registry by its Service ClusterIP. On the local kind
  cluster each cluster DNS lookup took 3 to 6 s, longer than the heartbeat
  timeout, and the node lost its registration.
- The supervisor's preview pipeline uses `videorate drop-only=true` and
  `sync=false`. With the plain `videorate` of the shipped status server, a
  smoke test wrote no frame for one flow and grew to 1.4 GiB for the other.
