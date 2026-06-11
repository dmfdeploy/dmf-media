# QWEN.md — dmf-media

<!-- WORKING-MODEL-BLOCK-START — generated from umbrella docs/templates/working-model-block.md; do not edit copies, edit the template and run bin/check-working-model-sync.sh -->
## Working model (mandatory)

Canonical: [docs/WORKING-MODEL.md](https://github.com/dmfdeploy/dmfdeploy/blob/main/docs/WORKING-MODEL.md)
in the umbrella repo. The three rules that matter mid-task:

1. **Work starts at an issue** in the canonical backlog
   ([dmfdeploy/dmfdeploy issues](https://github.com/dmfdeploy/dmfdeploy/issues);
   milestone + `component:*`/`workstream:*` labels). Non-trivial work gets a
   plan doc in umbrella `docs/plans/` with `tracking_issue` frontmatter.
2. **The completing PR closes the issue and flips the plan frontmatter in the
   same change.** From a component repo, reference umbrella issues **fully
   qualified** — `Closes dmfdeploy/dmfdeploy#N`; bare `#N` targets the wrong repo.
3. **Never invent a local backlog** (TODO files, ad-hoc trackers). Issues =
   liveness; plan frontmatter = design state; ADRs = decisions (RFC in
   Discussions first); STATUS.md = cross-repo now.
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
2. `dmfdeploy/QWEN.md` — full boot ritual + skills index + Qwen-specific rules
3. `dmfdeploy/docs/decisions/INDEX.md` — ADRs applicable to your task
4. The most recent file under `dmfdeploy/docs/handoffs/`

For cluster ops, secrets, or dmf-cms releases, also read §0 Secrets Discipline
of the relevant skill in `dmfdeploy/.claude/skills/`. Qwen doesn't have
Claude's `/skill-name` invocation — read the SKILL.md as documentation
and apply its sections like instructions.

If you change cross-repo state, update the `<!-- HUMAN-START -->` section of
`dmfdeploy/STATUS.md` before ending the session.

---

## Repo-specific notes

This repo is **scaffold-only and reserved for thesis-killer #1** per the
strategic review (NMOS IS-04/05 + EBU LIST 2110 on commodity k3s). All
roles are TODO-only stubs:

- (NMOS IS-04/05 registry role moved to `dmf-runbooks/roles/nmos-cpp/` on 2026-05-06)
- `roles/ebu-list/` — EBU LIST 2110 packet analysis
- `roles/flow-exporters/` — flow-level metrics
- `roles/ptp-monitor/` — PTP topology monitoring
- `roles/netbox-media-plugin/` — sender/receiver/flow schema
- `roles/media-controllers/` — media-domain control surface

**Do not implement these incrementally.** They land together as part of
Move 1 (the NMOS spike). The whole point of Move 1 is to discover whether
the architecture survives contact with the media domain — partial
implementation defeats the falsifying-experiment design.

If you're tempted to "just add a small thing" in this repo, check the
strategic review and TODOS.md first to confirm whether this is the right
move at the right time. Per ADR-0004, Move 1 is the highest-priority
unaddressed thesis-killer; everything in this repo is reserved for it.

For deeper guidance see `CLAUDE.md` in this repo. The boot ritual ↑
supersedes anything in CLAUDE.md that conflicts.
