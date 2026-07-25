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
echo "Render matrix + diffs written to $OUT_DIR"
