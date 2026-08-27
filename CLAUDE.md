# dmf-media

<!-- WORKING-MODEL-BLOCK-START — generated from umbrella docs/templates/working-model-block.md; do not edit copies, edit the template and run bin/check-working-model-sync.sh -->
## Working model (mandatory)

Canonical: [docs/WORKING-MODEL.md](https://github.com/dmfdeploy/dmfdeploy/blob/main/docs/WORKING-MODEL.md)
in the umbrella repo. The three rules that matter mid-task:

1. **Work starts at an issue** in the canonical backlog
   ([dmfdeploy/dmfdeploy issues](https://github.com/dmfdeploy/dmfdeploy/issues)):
   `component:*`/`workstream:*` labels **always**, plus a milestone **only if
   the work is scheduled** — unscheduled work gets `platform-debt` and no
   milestone (§2). Non-trivial work gets a plan doc in umbrella `docs/plans/`
   with `tracking_issue` frontmatter.
2. **The completing PR auto-closes its issue; you still flip the plan
   frontmatter by hand in that PR.** Reference umbrella issues **fully
   qualified** — `Closes dmfdeploy/dmfdeploy#N` (bare `#N` targets the wrong
   repo); the daily issue-close reconciler honors that ref, cross-repo
   included. Manual close is a fallback.
3. **Never invent a local backlog** (TODO files, ad-hoc trackers). Issues =
   liveness; plan frontmatter = design state; ADRs = decisions (RFC in
   Discussions first); STATUS.md = committed notes; STATUS.local.md = live repo snapshot.
<!-- WORKING-MODEL-BLOCK-END -->

## DMF Platform context — read first

This repo is a component of the **DMF Platform**, an umbrella workspace
checked out alongside this repo. Operators set `$DMFDEPLOY_UMBRELLA` to its
local path. Cross-cutting state (status, decisions, plans, skills) lives
there, not here.

Before any non-trivial change in this repo:

```bash
cd "$DMFDEPLOY_UMBRELLA"
git fetch && git pull
bin/generate-status.sh --no-fetch    # refreshes STATUS.md
```

Then read in order:
1. `dmfdeploy/STATUS.md` — what's happening across all repos right now
2. `dmfdeploy/CLAUDE.md` — full boot ritual + workspace map
3. `dmfdeploy/docs/decisions/INDEX.md` — ADRs applicable to your task
4. The most recent file under `dmfdeploy/docs/handoffs/`

For cluster ops, secrets, or dmf-cms releases, also read §0 Secrets Discipline
of the relevant skill in `dmfdeploy/.claude/skills/`.

If you change cross-repo state, update the `<!-- HUMAN-START -->` section of
`dmfdeploy/STATUS.md` before ending the session.

---

Media-domain Ansible roles and Helm charts for the DMF Platform.
Depends on `dmf-infra` as the base platform.

Scope: NMOS IS-04/05 registry, EBU LIST, flow exporters, PTP monitoring,
NetBox media plugin, media-specific AWX Job Templates.

See `dmfdeploy/docs/architecture/DMF Platform Plan.md` for strategic context.

## Charts directory

> **2026-05-23 — ADR-0025 Lane B landed.** Per
> [ADR-0025](https://github.com/dmfdeploy/dmfdeploy/blob/main/docs/decisions/0025-ansible-in-cluster-pods-and-catalog-helm.md),
> catalog functions (NMOS-cpp first; future EBU LIST, flow-exporters, etc.)
> deploy as Helm charts hosted in cluster-internal Zot, installed by the
> AWX EE pod via `kubernetes.core.helm`. The charts live here:

- `charts/nmos-cpp/` — NMOS IS-04/05 registry + mock nodes (landed 2026-05-23, Lane B)
- `charts/<future>/` — pattern for future Layer 5 functions

The NetBox-side launcher tasks (provision tags, flip lifecycle tag) for each
function live in `dmf-runbooks/roles/<function>/`. Together with the chart,
they form the per-function "catalog launcher" stack.

Image build pipeline for media-domain images (e.g. NMOS-cpp arm64 builds)
is operator-managed today; codification into a `dmf-media-build-and-release`
skill is an open decision in the 2026-05-19 plan §8.2.

**Plan:** `docs/plans/DMF Cluster-Internal Ansible Execution and Catalog Helm Pivot Plan 2026-05-19.md` (umbrella docs tree).
