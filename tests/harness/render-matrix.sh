#!/bin/sh
# umbrella #201 WP2 — helm template render matrix + diffs.
#
# Renders the chart across the shapes the WO2 acceptance criteria name:
#   default   — bare `helm template` with chart defaults (nothing WP2 added
#               overridden), a regression sanity check on its own
#   source-a  — J1's first source instance (catalog/topology-params.j1.yaml)
#   source-b  — J1's second source instance
#   view-a    — the viewer release with source-a selected (its flow.id
#               resolved to source-a's, per the seam doc's projection)
#   view-b    — the viewer release with source-b selected
#
# Diffs source-a vs source-b and view-a vs view-b so a reviewer can see
# EXACTLY which values differ per instance/selection — proving the N-shaped
# parameterisation touches only what it should.
#
# Run: tests/harness/render-matrix.sh
# Output lands in tests/harness/render-matrix/ (gitignored, regenerated per run).
set -eu

HARNESS_DIR="$(cd "$(dirname "$0")" && pwd)"
CHART_DIR="$HARNESS_DIR/../../charts/mxl-fabrics-demo"
OUT_DIR="$HARNESS_DIR/render-matrix"

# J1's real instance values (catalog/topology-params.j1.yaml, WP1 frozen).
SOURCE_A_FLOW="5fbec3b1-1b0f-417d-9059-8b94a47197ed"
SOURCE_A_PATTERN="smpte"
SOURCE_B_FLOW="b0ae9cba-a989-4568-ac96-8bd19272c966"
SOURCE_B_PATTERN="ball"

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

render() {
  name="$1"; shift
  helm template t "$CHART_DIR" "$@" > "$OUT_DIR/$name.yaml"
  echo "rendered: $OUT_DIR/$name.yaml"
}

render default --set role=source
render source-a --set role=source --set sourceId=source-a --set "flow.id=$SOURCE_A_FLOW" --set "pattern=$SOURCE_A_PATTERN"
render source-b --set role=source --set sourceId=source-b --set "flow.id=$SOURCE_B_FLOW" --set "pattern=$SOURCE_B_PATTERN"
render view-a --set role=view --set "flow.id=$SOURCE_A_FLOW"
render view-b --set role=view --set "flow.id=$SOURCE_B_FLOW"

echo
echo "=== diff: source-a vs source-b ==="
diff -u "$OUT_DIR/source-a.yaml" "$OUT_DIR/source-b.yaml" | tee "$OUT_DIR/diff-source-a-vs-source-b.diff" || true

echo
echo "=== diff: view-a vs view-b ==="
diff -u "$OUT_DIR/view-a.yaml" "$OUT_DIR/view-b.yaml" | tee "$OUT_DIR/diff-view-a-vs-view-b.diff" || true

echo
echo "=== assert: umbrella #308 mxl-gst-sink pacing env vars appear ONLY on the view-side status container ==="
fail() {
  echo "FAIL: $1" >&2
  exit 1
}

# view-a/view-b render target.yaml's status container (PREVIEW=1) — the only
# status container that ever spawns mxl-gst-sink (mxl-status-server.py only
# starts its preview child when PREVIEW=1). All three #308 env vars, and
# PREVIEW=1 itself, must be present.
for f in "$OUT_DIR/view-a.yaml" "$OUT_DIR/view-b.yaml"; do
  grep -q '{ name: MXL_GST_TOO_EARLY_RETRY_NS,' "$f" || fail "MXL_GST_TOO_EARLY_RETRY_NS missing from $f (view status container)"
  grep -q '{ name: MXL_GST_LOG_RATE_LIMIT_SECONDS,' "$f" || fail "MXL_GST_LOG_RATE_LIMIT_SECONDS missing from $f (view status container)"
  grep -q '{ name: MXL_GST_LOG_LEVEL,' "$f" || fail "MXL_GST_LOG_LEVEL missing from $f (view status container)"
  grep -q '{ name: PREVIEW, value: "1" }' "$f" || fail "PREVIEW=1 missing from $f (view status sidecar)"
done

# default/source-a/source-b render initiator.yaml's status container
# (PREVIEW=0) — it never spawns mxl-gst-sink, so the #308 env vars must be
# ABSENT there (inert env plumbing is scope creep this harness should catch).
for f in "$OUT_DIR/default.yaml" "$OUT_DIR/source-a.yaml" "$OUT_DIR/source-b.yaml"; do
  grep -qE 'MXL_GST_TOO_EARLY_RETRY_NS|MXL_GST_LOG_RATE_LIMIT_SECONDS|MXL_GST_LOG_LEVEL' "$f" \
    && fail "umbrella #308 pacing env vars found on $f — source-side status container never spawns mxl-gst-sink"
  grep -q '{ name: PREVIEW, value: "0" }' "$f" || fail "PREVIEW=0 missing from $f (source status sidecar)"
done

echo "OK: #308 pacing env vars present only on view-side (PREVIEW=1) status containers; PREVIEW=1/0 intact on both sides"

echo
echo "Render matrix + diffs written to $OUT_DIR"
