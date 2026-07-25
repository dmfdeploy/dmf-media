#!/bin/sh
# umbrella #201 WP2 acceptance-gate demo harness (spec §10 hard gate: "an
# entry-time-selector-only implementation FAILS").
#
# Renders the REAL source-supervisor.sh out of the chart (helm template,
# not a hand-copied re-implementation) and runs it as a live process INSIDE
# a debian:trixie-slim container (matching the real image's own base OS,
# per docker/mxl-fabrics/Dockerfile — chosen so the script's hardcoded
# `/coordinator` and default `/home/mxl/domain` paths are exercised
# UNCHANGED from production, not relaxed for testability; also gives the
# GNU coreutils `stdbuf` the script depends on, which BusyBox lacks). No
# real cluster, no kubectl — mxl-fabrics-demo and ip are stubbed on PATH
# (tests/harness/stub-bin/), and the coordinator ConfigMap's mount is
# stood in by a host bind-mount the driver writes to directly.
#
# Proof scenarios:
#   (0) entry gate holds — nothing selected, no coordinator data -> never starts
#   (1) positive path — selected + target-info/epoch present -> starts
#   (2) predicate re-check covers target-info/epoch too, not just selection
#       (codex fix-round-2 P1): clearing target-info/epoch with selection
#       STILL intact stops+parks a RUNNING initiator, it does NOT restart
#       while they stay cleared, and once repopulated it starts against
#       the FRESH values, never a stale cached one
#   (3) stop-on-de-select — a RUNNING initiator stops and parks when its
#       source is de-selected (active-source switched to another source)
#   (4) no-relaunch-on-epoch-bump — a de-selected source does NOT relaunch
#       when the coordinator's epoch bumps (the §6.2 phase-2 re-point)
#       while still de-selected
#   (5) re-select — un-parks and starts again once re-selected
#   (6) epoch-restart gate is independently load-bearing (codex fix-round-2
#       P1, the hard one): freezes the supervisor process (SIGSTOP),
#       applies a de-select AND an epoch bump TOGETHER while frozen, then
#       resumes (SIGCONT) — a correct implementation must produce ZERO new
#       initiator starts in this window; codex's exact
#       epoch-changed-only-mutant (the guard removed) produces an
#       illegitimate extra start+stop pair here, which this phase catches
#       deterministically (no timing-window sleeps — the race is
#       constructed via process suspension, not luck)
#
# Every assertion is a hard fail (set -e + explicit checks) — this script
# IS the acceptance proof, not just a demo. Requires docker.
# Run: tests/harness/run-demo.sh
# Evidence lands in tests/harness/evidence/ (gitignored, regenerated per run).
set -eu

HARNESS_DIR="$(cd "$(dirname "$0")" && pwd)"
CHART_DIR="$HARNESS_DIR/../../charts/mxl-fabrics-demo"
EVIDENCE_DIR="$HARNESS_DIR/evidence"
IMAGE="debian:trixie-slim"
CONTAINER_NAME="dmf-media-wp2-demo-$$"

FLOW_ID="11112222-3333-4444-5555-666677778888"
SOURCE_ID="source-a"
OTHER_SOURCE_ID="source-b"

fail() { echo "ACCEPTANCE GATE FAILED: $*" >&2; docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true; exit 1; }
log() { echo "[demo] $*" | tee -a "$EVIDENCE_DIR/demo-transcript.log"; }

command -v docker >/dev/null 2>&1 || { echo "docker is required for this harness" >&2; exit 1; }

rm -rf "$EVIDENCE_DIR"
mkdir -p "$EVIDENCE_DIR/coordinator" "$EVIDENCE_DIR/domain"
: > "$EVIDENCE_DIR/demo-transcript.log"

log "=== umbrella #201 WP2 acceptance demo — $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

log "Rendering the REAL source-supervisor.sh from the chart (helm template, production-default domainPath/coord paths)"
helm template t "$CHART_DIR" \
  --set role=source \
  --set "sourceId=$SOURCE_ID" \
  --set "flow.id=$FLOW_ID" \
  --set pattern=smpte \
  --show-only templates/source-handshake.yaml \
  > "$EVIDENCE_DIR/rendered-source-handshake.yaml"

PYEXE=/opt/homebrew/Cellar/ansible/13.6.0/libexec/bin/python
command -v "$PYEXE" >/dev/null 2>&1 || PYEXE=python3
"$PYEXE" - "$EVIDENCE_DIR/rendered-source-handshake.yaml" "$EVIDENCE_DIR/source-supervisor.sh" <<'PYEOF'
import sys, yaml
src, dst = sys.argv[1], sys.argv[2]
docs = list(yaml.safe_load_all(open(src)))
for d in docs:
    if d and d.get("kind") == "ConfigMap":
        open(dst, "w").write(d["data"]["source-supervisor.sh"])
        break
else:
    raise SystemExit("no ConfigMap found in rendered output")
PYEOF
chmod +x "$EVIDENCE_DIR/source-supervisor.sh"
log "Rendered script: $EVIDENCE_DIR/source-supervisor.sh (uses production default paths: /coordinator, /home/mxl/domain)"

# The producer (writer container) output this script's wait_for_flow()
# blocks on — pre-create it, standing in for the SEPARATE writer container
# in a real pod (out of scope here: this harness proves the supervisor's
# active-source gating, not the writer/producer pairing).
mkdir -p "$EVIDENCE_DIR/domain/$FLOW_ID.mxl-flow"
: > "$EVIDENCE_DIR/domain/$FLOW_ID.mxl-flow/flow_def.json"

count_starts() {
  log_file="$EVIDENCE_DIR/initiator-invocations.log"
  [ -f "$log_file" ] || { echo 0; return; }
  grep -c '^start ' "$log_file" 2>/dev/null || echo 0
}

count_launch_attempts() {
  # The SUPERVISOR's own decision to call start_initiator (its "launching
  # initiator for epoch=..." line), not the stub child's own bookkeeping.
  # Phase 6 needs this: a restart that's killed by the predicate re-check
  # in the SAME iteration can tear the stub down before its own trap/log
  # lines ever execute (a real race in the harness's instrumentation, not
  # in the chart), so count_starts() alone can undercount an illegitimate
  # restart that genuinely happened. The supervisor's own log line is
  # unambiguous proof start_initiator was called, regardless of how long
  # the child survived.
  grep -c "launching initiator for epoch=" "$EVIDENCE_DIR/supervisor-stdout.log" 2>/dev/null || echo 0
}

initiator_running() {
  # True iff the newest recorded stub-initiator start has NOT logged a
  # matching stop line yet — process-external evidence (the stub's OWN
  # invocation log), not the supervisor script's internal belief about its
  # own pid.
  log_file="$EVIDENCE_DIR/initiator-invocations.log"
  [ -f "$log_file" ] || return 1
  starts="$(count_starts)"
  stops="$(grep -c '^stop ' "$log_file" 2>/dev/null || true)"
  : "${starts:=0}"
  : "${stops:=0}"
  [ "$starts" -gt "$stops" ]
}

initiator_stopped() { ! initiator_running; }
log_shows() { grep -q "$1" "$EVIDENCE_DIR/supervisor-stdout.log" 2>/dev/null; }
container_alive() { docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null | grep -q true; }

wait_until() {
  # wait_until <description> <max_seconds> <predicate...>
  desc="$1"; max="$2"; shift 2
  i=0
  while [ "$i" -lt "$max" ]; do
    if "$@"; then
      log "OK  (${i}s): $desc"
      return 0
    fi
    i=$((i + 1))
    sleep 1
  done
  fail "timed out after ${max}s waiting for: $desc"
}

log "Pulling/starting $IMAGE (matches docker/mxl-fabrics/Dockerfile's own base OS) ..."
docker run -d --rm \
  --name "$CONTAINER_NAME" \
  -v "$EVIDENCE_DIR/coordinator:/coordinator" \
  -v "$EVIDENCE_DIR/domain:/home/mxl/domain" \
  -v "$EVIDENCE_DIR:/evidence" \
  -v "$HARNESS_DIR/stub-bin:/stub-bin:ro" \
  -e HARNESS_EVIDENCE_DIR=/evidence \
  "$IMAGE" \
  sh -c 'mkdir -p /shared && PATH=/stub-bin:$PATH exec /evidence/source-supervisor.sh' \
  > "$EVIDENCE_DIR/container-id" 2>&1
docker logs -f "$CONTAINER_NAME" > "$EVIDENCE_DIR/supervisor-stdout.log" 2>&1 &
LOG_TAIL_PID=$!
log "container=$CONTAINER_NAME started"

cleanup() {
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  kill "$LOG_TAIL_PID" 2>/dev/null || true
}
trap cleanup EXIT

wait_until "container is running" 15 container_alive

# ── phase 0: not selected, no coordinator data yet — must NOT start ──────
log "--- phase 0: entry gate holds (no selection, no coordinator data) ---"
sleep 3
initiator_running && fail "initiator started with no selection and no coordinator data — entry gate did not hold"
log "OK  (3s): initiator did not start with nothing selected"

# ── phase 1 (positive path): select + populate coordinator -> starts ─────
log "--- phase 1: positive path (select + target-info/epoch present) ---"
printf '%s' "$SOURCE_ID" > "$EVIDENCE_DIR/coordinator/active-source"
printf '%s' "opaque-target-info-v1" > "$EVIDENCE_DIR/coordinator/target-info"
printf '%s' "podA:1000" > "$EVIDENCE_DIR/coordinator/epoch"
wait_until "initiator starts once selected + target-info/epoch present" 15 initiator_running

# ── phase 2: full predicate re-check — clearing target-info/epoch with ───
# ── selection intact must ALSO stop+park (codex fix-round-2 P1) ──────────
log "--- phase 2: predicate covers target-info/epoch too, not just selection ---"
rm -f "$EVIDENCE_DIR/coordinator/target-info" "$EVIDENCE_DIR/coordinator/epoch"
wait_until "supervisor log records target-info cleared while selected" 15 log_shows "target-info cleared while selected"
wait_until "initiator process stopped (target-info/epoch cleared, still selected)" 15 initiator_stopped
sleep 5
initiator_running && fail "initiator restarted while target-info/epoch are still empty — predicate re-check does not cover them"
log "OK  (5s observed): stayed parked while target-info/epoch remain cleared"
log "PROOF (spec §4.1 'that predicate', the whole conjunction): the continuous re-check covers target-info/epoch presence, not just selection."
printf '%s' "opaque-target-info-v1b-fresh-after-clear" > "$EVIDENCE_DIR/coordinator/target-info"
printf '%s' "podA:1500" > "$EVIDENCE_DIR/coordinator/epoch"
wait_until "initiator restarts once target-info/epoch are repopulated" 15 initiator_running
wait_until "restart used the FRESH (post-clear) target-info, not a stale cached one" 5 sh -c "grep -q 'v1b-fresh-after-clear' \"$EVIDENCE_DIR/initiator-invocations.log\""
log "PROOF: restart after repopulation targets the FRESH values only."

# ── phase 3: RUNNING initiator stops+parks on de-select ──────────────────
log "--- phase 3: stop-on-de-select of a RUNNING initiator ---"
printf '%s' "$OTHER_SOURCE_ID" > "$EVIDENCE_DIR/coordinator/active-source"
wait_until "supervisor log records the de-select" 15 log_shows "de-selected"
wait_until "initiator process actually stopped" 15 initiator_stopped
log "PROOF: a RUNNING initiator stops and parks when its source is de-selected."

# ── phase 4: epoch bump while de-selected -> NO relaunch ─────────────────
log "--- phase 4: epoch bump while de-selected (spec §6.2 phase-2 re-point) ---"
printf '%s' "opaque-target-info-v2-fresh-endpoint" > "$EVIDENCE_DIR/coordinator/target-info"
printf '%s' "podB:2000" > "$EVIDENCE_DIR/coordinator/epoch"
# Observe for a real window (longer than the supervisor's own poll
# interval) that nothing relaunches — absence-of-event needs time, not a
# single instant check.
sleep 12
initiator_running && fail "de-selected source RELAUNCHED on the epoch bump — the exact codex R3 failure this gate exists to catch"
log "OK  (12s observed): de-selected source did NOT relaunch on the epoch bump"
log "PROOF: a de-selected source does not relaunch on the phase-2 epoch bump."

# ── phase 5: re-select -> un-parks and starts against the fresh endpoint ─
log "--- phase 5: re-select (un-park) ---"
printf '%s' "$SOURCE_ID" > "$EVIDENCE_DIR/coordinator/active-source"
wait_until "initiator starts again once re-selected" 15 initiator_running
log "PROOF: re-selection un-parks the supervisor and it starts against the fresh (phase-4) endpoint."

# ── phase 6: epoch-restart gate is independently load-bearing ────────────
# Deterministic construction (codex fix-round-2 P1, no timing-window
# sleeps): freeze the supervisor process, apply a de-select AND an epoch
# bump TOGETHER while it cannot observe either individually, then resume.
# A correct implementation's epoch-restart branch re-checks is_selected
# fresh and skips (calls start_initiator zero times). A mutant with that
# guard removed calls start_initiator anyway — logged unambiguously by the
# SUPERVISOR itself ("launching initiator for epoch=...") the instant it
# decides to restart, which is the discriminator used here rather than the
# stub child's own bookkeeping: a restart the predicate re-check kills in
# the SAME iteration can tear the child down before it schedules long
# enough to write its own start line — a real race in the harness's
# process-level instrumentation, not in the chart, that would otherwise
# mask a genuine illegitimate restart as if it never happened.
log "--- phase 6: epoch-restart gate discriminates codex's epoch-changed-only mutant ---"
launches_before="$(count_launch_attempts)"
log "supervisor-decided launches recorded before freeze: $launches_before"
docker kill -s STOP "$CONTAINER_NAME" >/dev/null
sleep 1
printf '%s' "$OTHER_SOURCE_ID" > "$EVIDENCE_DIR/coordinator/active-source"
printf '%s' "opaque-target-info-v3-race" > "$EVIDENCE_DIR/coordinator/target-info"
printf '%s' "podC:3000" > "$EVIDENCE_DIR/coordinator/epoch"
docker kill -s CONT "$CONTAINER_NAME" >/dev/null
log "froze the process, applied de-select + epoch bump together, resumed"
# The mutant's illegitimate restart includes the SAME 5s stop-before-start
# pause the real epoch-restart branch uses — observe well past that.
sleep 15
launches_after="$(count_launch_attempts)"
log "supervisor-decided launches recorded after the frozen race window: $launches_after"
[ "$launches_after" -eq "$launches_before" ] || fail "epoch-restart fired during a de-select it should have refused (supervisor-decided launches $launches_before -> $launches_after) — the epoch-restart's own is_selected guard is not independently load-bearing"
initiator_running && fail "initiator left running after a de-select applied during the frozen race window"
log "OK  (15s observed): zero new launch decisions, ended parked — the epoch-restart guard held under the frozen de-select+epoch-bump race."
log "PROOF: the epoch-restart gate is independently load-bearing — codex's epoch-changed-only mutant (guard removed) would show launches_after > launches_before here."

log "=== ALL ACCEPTANCE CRITERIA PASSED ==="
log "Evidence directory: $EVIDENCE_DIR"
log "  demo-transcript.log            — this run's narrated phase-by-phase log"
log "  supervisor-stdout.log          — the rendered script's own stdout/stderr (docker logs)"
log "  initiator-invocations.log      — stub mxl-fabrics-demo start/stop records"
log "  rendered-source-handshake.yaml — the exact helm-template output used"
log "  source-supervisor.sh           — the extracted script that was actually run"
