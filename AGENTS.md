# AGENTS.md — dmf-media

Media-domain parts of the dmfdeploy platform: the function catalog, Helm charts,
the SHA-pinned MXL image and a test harness. It depends on `dmf-infra`. The
`roles/` directories and numbered EBU playbooks are stubs. Do not implement the
stub roles one at a time: they land together (ADR-0004).

## Boot steps

1. In the [umbrella repo](https://github.com/dmfdeploy/dmfdeploy) (a sibling checkout), run `git pull` and
   `bin/generate-status.sh`, then read `STATUS.digest.local.md`. Post shared
   notes to the pinned [umbrella issue 618](https://github.com/dmfdeploy/dmfdeploy/issues/618).
2. Apply any fitting umbrella ADR. Run `bin/install-hooks.sh` once per clone.

## Build and test

Run `ansible-galaxy collection install -r requirements.yml`, then `yamllint .`,
`ansible-lint . -p`, `ansible-playbook playbooks/<name>.yml --syntax-check`,
`helm lint charts/<chart>` and `python3 bin/check-catalog-demand.py` (needs
`helm` and PyYAML). `tests/harness/run-demo.sh` needs `helm` and Docker.

## Rules that are easy to miss

- Open a PR against `main` from a `<handle>/<short-slug>` branch, with
  Conventional Commits signed off by `git commit -s`. Never push to `main`,
  force-push, or use `--no-verify` or `--no-gpg-sign`. Update `VERSION` in the
  same commit as any release-tagged change, and never tag releases by hand.
- Never commit secrets, tokens, keys, kubeconfigs or Terraform state. Use
  placeholders (`dmf.example.com`) for IPs, DNS names and operator identity in
  every file, PR and issue. Report vulnerabilities per `SECURITY.md`, never publicly.
- Run cluster changes only through the umbrella's `bin/run-playbook.sh`
  (ADR-0010); read its AGENTS.md on secrets first. Ask before you modify a repo with uncommitted changes.
- Stage the five `docker/mxl-fabrics` files into the MXL checkout and pass its
  real SHA as `MXL_SOURCE_SHA`. The README has the exact commands.
- `nmos-cpp` lives in `dmf-runbooks/roles/nmos-cpp/`; only its catalog entry is
  here. Per ADR-0025, the AWX EE pod installs charts from cluster-internal Zot
  ([plan](https://github.com/dmfdeploy/dmfdeploy/blob/45eb326e973b10e0b52deef290981ceac9bcf5bd/docs/plans/DMF%20Cluster-Internal%20Ansible%20Execution%20and%20Catalog%20Helm%20Pivot%20Plan%202026-05-19.md)).

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
   Discussions first); STATUS.md = pointer file (operator notes live in pinned issue dmfdeploy/dmfdeploy#618);
   STATUS.local.md = live repo snapshot.
<!-- WORKING-MODEL-BLOCK-END -->
