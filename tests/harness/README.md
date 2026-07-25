# WP2 acceptance-gate harness (umbrella #201)

Local, no-cluster proof that the source-supervisor's selection-gated
behavior (spec §4.1) actually holds — the hard acceptance gate named in
umbrella issue #201's §10 WP2 row: *"an entry-time-selector-only
implementation FAILS — must demonstrate (i) stop-on-de-select of a running
initiator and (ii) no relaunch of a de-selected source on the phase-2 epoch
bump."* Extended in a codex fix round to also prove (iii) the continuous
re-check covers the WHOLE §4.1 predicate (selection AND target-info AND
epoch), not just selection, and (iv) the epoch-restart guard is
independently load-bearing, not just incidentally safe because an earlier
check happens to run first — see "Phases" below.

## What's here

- `run-demo.sh` — the acceptance-gate proof. Renders the REAL
  `source-supervisor.sh` out of `charts/mxl-fabrics-demo` (helm template,
  not a hand-copied re-implementation) and runs it as a live process inside
  a `debian:trixie-slim` container (matching `docker/mxl-fabrics/Dockerfile`'s
  own base OS, so the script's hardcoded `/coordinator` path and default
  `/home/mxl/domain` are exercised unchanged from production — not relaxed
  for testability — and so GNU `stdbuf`, which the script depends on and
  BusyBox lacks, is present). `mxl-fabrics-demo` and `ip` are stubbed on
  `PATH` (`stub-bin/`); the coordinator ConfigMap's mount is stood in by a
  host bind-mount the driver writes to directly. No cluster, no kubectl.
  Every proof point is a hard `set -e` assertion — the script IS the proof,
  not a demo that happens to look right.
- `stub-bin/mxl-fabrics-demo` — records every invocation (args, start/stop)
  to `initiator-invocations.log` and then blocks until killed, standing in
  for the real long-running initiator process.
- `stub-bin/ip` — stubs `ip -4 -o addr show <iface>` (Linux iproute2 isn't
  present on the macOS harness-runner).
- `render-matrix.sh` — renders the chart across J1's two source instances
  and both viewer selections, diffing source-a vs source-b and view-a vs
  view-b so a reviewer can see exactly which values differ per
  instance/selection.

Requires: `helm`, `docker` (daemon running), `python3` (or the ansible venv
python — the script falls back automatically) with PyYAML.

## Phases (`run-demo.sh`)

0. Entry gate holds — nothing selected, no coordinator data — never starts.
1. Positive path — selected + target-info/epoch present — starts.
2. Predicate covers target-info/epoch too, not just selection — clearing
   them with selection still intact stops+parks a RUNNING initiator; it
   does not restart while they stay cleared; once repopulated it starts
   against the FRESH values, never a stale cached one.
3. Stop-on-de-select — a RUNNING initiator stops and parks when its source
   is de-selected.
4. No-relaunch-on-epoch-bump — a de-selected source does not relaunch when
   the coordinator's epoch bumps (the §6.2 phase-2 re-point).
5. Re-select — un-parks and starts again once re-selected.
6. Epoch-restart gate is independently load-bearing — freezes the
   supervisor process (`docker kill -s STOP`), applies a de-select AND an
   epoch bump TOGETHER while it can't observe either individually, then
   resumes (`SIGCONT`). A correct implementation calls `start_initiator`
   zero times in this window; codex's exact epoch-changed-only mutant
   (the `&& is_selected` guard removed from the epoch-restart branch)
   calls it once — an illegitimate restart the predicate re-check kills a
   moment later. Deterministic by construction (process suspension, not a
   timing-window sleep that could flake either way). Self-verified before
   this fix round shipped: hand-applied codex's exact mutant to a scratch
   chart copy and confirmed this phase fails against it, then confirmed it
   passes against the real chart — both runs used the unmodified harness.

## Running it

```sh
tests/harness/run-demo.sh
tests/harness/render-matrix.sh
```

Both are idempotent — each run wipes and regenerates its own output
directory (`evidence/`, `render-matrix/`; both gitignored). `run-demo.sh`
exits non-zero with `ACCEPTANCE GATE FAILED: ...` on the first violated
assertion; a clean run prints `=== ALL ACCEPTANCE CRITERIA PASSED ===`.

## Reading the evidence

After `run-demo.sh`, `evidence/`:

- `demo-transcript.log` — the narrated phase-by-phase run, with each
  assertion's observed timing.
- `supervisor-stdout.log` — the rendered script's own log lines (`docker
  logs`) — shows `[supervisor] de-selected (active-source != source-a);
  stopping and parking` and `[supervisor] target-info cleared while
  selected; stopping and parking` at the exact moments of proof, and every
  `[supervisor] launching initiator for epoch=...` line phase 6's
  discriminator counts.
- `initiator-invocations.log` — the stub's own start/stop record,
  independent of the supervisor's internal belief about its own pid; used
  for phases 0-5 (e.g. no new `start` line appears between the phase-3
  de-select and phase-5 re-select, spanning the phase-4 epoch bump). Phase
  6 deliberately does NOT rely on this log — a restart the predicate
  re-check kills in the very same iteration can tear the stub child down
  before it schedules long enough to write its own line, which would
  under-count a genuine illegitimate restart. Phase 6 instead counts the
  supervisor's own `launching initiator for epoch=` decision line, which
  is unambiguous regardless of how briefly the child survives.
- `rendered-source-handshake.yaml` / `source-supervisor.sh` — exactly what
  was run, for re-execution or manual inspection.

## Scope

This harness proves the SOURCE supervisor's selection gate — the piece
umbrella #201 WP2 (this repo) is responsible for. It does not stand up the
viewer side, the coordinator's real `kubectl patch` publisher, or the WP4/5
switch actuator (dmf-cms, future work) — those are separate repos'
responsibilities; this harness drives the coordinator files directly, the
same shape the real actuator will eventually patch into the ConfigMap.
