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

You need Docker, kind, kubectl and Python 3.10 or later. The driver installs
Pillow into a temporary virtual environment. Set `KEEP_CLUSTER=1` to keep the
cluster afterwards, or `SKIP_NMOS_TESTING=1` to skip the second gate.

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

## Reference observations

[`reference/`](reference/) holds the observations from the run recorded as the
reference for the crosspoint adoption work: `observations.json`, both
nmos-testing result files, and preview frames before and after a switch and
from the negative case.

## Notes

- The node reaches the registry by its Service ClusterIP. On the local kind
  cluster each cluster DNS lookup took 3 to 6 s, longer than the heartbeat
  timeout, and the node lost its registration.
- The supervisor's preview pipeline uses `videorate drop-only=true` and
  `sync=false`. With the plain `videorate` of the shipped status server, a
  smoke test wrote no frame for one flow and grew to 1.4 GiB for the other.
