# Contributing to dmf-media

Media-domain catalog metadata and (future) Layer 5 roles + Helm charts.

This repo is part of the **dmfdeploy platform**. GitHub is the canonical home and the
single source of truth: all changes land via **Pull Request** against `main`.

## Quick start

1. Read the platform overview in the [dmfdeploy/dmfdeploy](https://github.com/dmfdeploy/dmfdeploy) umbrella repo
   (`docs/architecture/dmfdeploy-platform-plan.md`, the long-term blueprint; current
   plans are listed in `docs/plans/INDEX.md`) and apply any relevant ADRs from
   `docs/decisions/INDEX.md`.
2. Set up your machine and run the checks (see [Develop and test](#develop-and-test)).
3. Fork or branch, make your change on a topic branch, open a PR against `main`.
4. Ensure CI is green, your commits are **signed off** (see DCO below), and the
   PR body links its issue (see [What a pull request needs](#what-a-pull-request-needs)).

## Develop and test

This section is the one place for development and test commands. The README,
`AGENTS.md` and the READMEs under `schemas/` and `tests/` point here. The
commands match what CI runs in `.github/workflows/ci.yml` and
`.github/workflows/is05-ab-fixture.yml`. Where CI doesn't run a command, the
text says so.

### Prerequisites

- **Python 3.12 or later.** CI runs on Ubuntu 24.04, whose `python3` is 3.12.
- **[Helm](https://helm.sh/).** CI installs it with `azure/setup-helm`. The
  catalog check and the conformance tests run it. See
  [What happens without Helm](#what-happens-without-helm).
- **Network access to Ansible Galaxy**, to install the collections.
- **Optional tools.** [gitleaks](https://github.com/gitleaks/gitleaks) powers
  the commit hook. The two checks that run a cluster or a container need more.
  See [Run the cluster and container checks](#run-the-cluster-and-container-checks).

You don't need a cluster, a cloud account, or secrets for the fast checks.

### Set up

Run these commands from the repository root. Check first that `python3` is
version 3.12 or later. If it isn't, use the full name of a newer interpreter,
such as `python3.13`, in place of `python3`.

```bash
python3 --version
python3 -m venv .venv
. .venv/bin/activate
python -m pip install ansible-core ansible-lint yamllint pyyaml==6.0.3 jsonschema==4.26.0
ansible-galaxy collection install -r requirements.yml
```

CI's lint job installs the `ansible` package, which includes `ansible-core`.
`ansible-core` plus the collections is enough to run the checks here. The
`pyyaml` and `jsonschema` versions are the ones the conformance job pins.
`ansible-galaxy` installs the collections into `~/.ansible/collections`,
outside the virtual environment, where other projects can see them.

### Run the fast checks

Run these commands from the repository root, with the virtual environment
active, before you push. They take under a minute together.

```bash
yamllint .
ansible-lint . -p
for pb in playbooks/*.yml; do
  echo "=== syntax-check: $pb ==="
  ansible-playbook "$pb" --syntax-check
done
python3 bin/check-catalog-demand.py
python3 -m unittest discover -s tests/conformance -v
for chart in charts/*/; do
  if [ -f "${chart}values.schema.json" ]; then
    echo "=== chart-only: $chart ==="
    python3 bin/dmf-function-conform --chart-only "$chart"
  fi
done
```

Lint each chart as well. CI runs this for every directory under `charts/` that
holds a `Chart.yaml`:

```bash
helm lint charts/mxl-fabrics-demo
helm lint charts/nmos-cpp
helm lint charts/nmos-crosspoint
```

| Command | What it does | Time |
| --- | --- | --- |
| `yamllint .` | Checks the YAML structure | A few seconds |
| `ansible-lint . -p` | Lints the playbooks and roles | About 10 seconds |
| Syntax check | Checks every playbook in `playbooks/` | A few seconds |
| `helm lint` | Lints one chart | About a second per chart |
| `bin/check-catalog-demand.py` | Renders the chart and compares its resource demand with the catalog entry | Under a second |
| `unittest discover` | Runs the 49 conformance tool tests against good and bad fixtures | About 10 seconds |
| `--chart-only` | Checks the values schema of each chart that has one, and runs `helm lint --strict` | About a second |

To check a package under `packages/`, run `python3 bin/dmf-function-conform
packages/<key>`. CI does this for every package once the directory exists. It
doesn't exist yet. The [schema README](schemas/README.md) describes what the
tool checks.

### What happens without Helm

Two of the fast checks need the `helm` command, and neither one skips when it
is missing:

- `bin/check-catalog-demand.py` stops with an error, because it renders the
  chart with `helm template`.
- Ten of the 49 conformance tests fail, because the tool runs
  `helm lint --strict` on each package or chart that it checks. Set
  `DMF_CONFORM_SKIP_HELM=1` to skip that step in the tests. A run with it set
  passes, but it doesn't cover the Helm lint.

### Run the cluster and container checks

Two checks need more than Python and Helm. Run them when your change touches
the code they cover.

| Command | Needs | Time | In CI |
| --- | --- | --- | --- |
| `python3 tests/is05-ab-fixture/crosspoint/check_pins.py` | Python only | Under a second | Yes, on pull requests that change `tests/is05-ab-fixture/`, `charts/nmos-crosspoint/` or the workflow |
| `tests/is05-ab-fixture/run.sh [OUT_DIR]` | Docker, kind, kubectl, Python 3.11 or later, network access | About 8 minutes on a 4-CPU ARM64 Docker host | Yes, on the same pull requests, on an ARM64 runner |
| `tests/harness/render-matrix.sh` | Helm | Under a second | No |
| `tests/harness/run-demo.sh` | Docker with a running daemon, Helm, `python3` with PyYAML | Not measured | No |

`tests/is05-ab-fixture/run.sh` creates a single-node kind cluster, runs the
checks, writes the observations to `OUT_DIR` and deletes the cluster. The
[fixture README](tests/is05-ab-fixture/README.md) describes what it checks.
CI runs it in four variants. Set these environment variables to run the
others:

```bash
SWITCH_VIA=crosspoint CONTROLLER_LINE=gemini tests/is05-ab-fixture/run.sh [OUT_DIR]
SWITCH_VIA=crosspoint CONTROLLER_LINE=qvest tests/is05-ab-fixture/run.sh [OUT_DIR]
SWITCH_VIA=crosspoint CONTROLLER_LINE=gemini CONTROLLER_FROM_CHART=1 tests/is05-ab-fixture/run.sh [OUT_DIR]
```

The two harness scripts in `tests/harness/` are described in
[tests/harness/README.md](tests/harness/README.md). `render-matrix.sh` writes
its output to `tests/harness/render-matrix/`, and `run-demo.sh` writes to
`tests/harness/evidence/`. Git ignores both directories.

A local two-node k3s cluster on Lima, for building the MXL image, is described
in [dev/lima/README.md](dev/lima/README.md).

### What CI runs

The `ci` workflow has these jobs:

- **`ansible`** installs the newest `ansible` package, Helm and PyYAML, then
  runs `yamllint`, `ansible-lint`, the syntax check, `helm lint` and the
  catalog check from the fast checks.
- **`conformance`** installs the pinned `pyyaml` and `jsonschema`, then runs the
  conformance tests and the `--chart-only` check.
- **`review-comments`** runs `bin/check-review-comments.sh` and its
  `--self-test`.
- **`working-model`** confirms that the working-model block in `AGENTS.md`,
  `CLAUDE.md` and `QWEN.md` matches the umbrella template. It needs a checkout
  of the umbrella repo, so it runs in CI only.

The `is05-ab-fixture` workflow runs the pin check and the cluster run from the
table in the cluster and container section. The `guard` workflow scans for
secrets and vulnerabilities, lints the workflow files, and checks commit
subjects and the issue link. The `DCO`
workflow checks sign-offs. Those three run in CI only. No check needs an
existing cluster, a cloud account, or a secret. The `is05-ab-fixture` run
creates its own temporary kind cluster.

You can run the review-comment check yourself:

```bash
bin/check-review-comments.sh
bin/check-review-comments.sh --self-test
```

The self-test takes about half a minute.

### Install the hooks

Run this once in each clone:

```bash
bin/install-hooks.sh
```

The script sets `core.hooksPath` to `.githooks/`. The `pre-commit` and
`pre-push` hooks scan your changes with gitleaks. They skip the scan with a
warning when gitleaks isn't installed. CI runs the same scan.

## Branch & PR model

- **GitHub Pull Requests only.** Direct push to `main` is blocked; force-push is
  banned; linear history is required.
- Topic branches: **`<handle>/<short-slug>`** (e.g. `jdoe/fix-probe-path`). One
  logical change per branch; rebase onto `main` rather than long-lived branches.
- A reviewer (per `CODEOWNERS`) and green required checks are needed to merge.

## What a pull request needs

CI checks each of these.

- **An issue link in the PR body.** Write a fully qualified reference to an issue
  in the [dmfdeploy/dmfdeploy](https://github.com/dmfdeploy/dmfdeploy/issues)
  backlog, such as `Refs dmfdeploy/dmfdeploy#912`. Use `Closes dmfdeploy/dmfdeploy#N`
  if the pull request finishes the issue. A bare `#N` doesn't count, because it
  points at this repo. If your change has no issue, such as a typo fix, a
  maintainer can add the `no-issue` label. Only maintainers can add labels, so
  if you open the pull request from a fork, ask in the PR. The check runs when
  you open the pull request, push a commit, or edit the PR body. Adding a label
  doesn't run it, so after a maintainer adds `no-issue`, edit the PR body or
  push a commit to run the check again.
- **A DCO sign-off on every commit.** Use `git commit -s`. See the next section.
- **Conventional commit subjects.** Use `type(scope): summary`, with one
  optional scope. The scope may use only `a-z`, `0-9`, `.`, `_` and `-`. The types are `feat`, `fix`, `docs`, `chore`, `refactor`, `test`,
  `build`, `ci`, `perf`, `revert` and `style`. An `!` before the colon marks a
  breaking change. Other types, such as `wip`, are rejected, and so is a scope
  list such as `(api,ui)`.
- **Passing CI.** The `ci` workflow runs the checks in
  [Develop and test](#develop-and-test). The `guard` workflow adds secret and
  vulnerability scans.

Also leave agent session links out of commit messages and the PR body. The
`guard` workflow rejects one known session-link format. It can't recognize
every tool's links, so reviewers look for them too.

## Developer Certificate of Origin (DCO)

We use the [Developer Certificate of Origin](https://developercertificate.org/),
not a CLA. **Every commit must be signed off:**

```bash
git commit -s -m "fix: correct the probe path"
```

This appends a `Signed-off-by: Your Name <you@example.com>` trailer certifying you
have the right to submit the work under the project license. A **DCO check** runs
on every PR and fails if any commit is missing the trailer. Amend with
`git commit --amend -s` or rebase with `git rebase --signoff main` to fix. PRs are
**rebase-merged** by default so your signed-off commits land on `main` unchanged.

## Versioning & releases

This repo carries a `VERSION` file (single semver line). Per **ADR-0005**,
`VERSION` is the single source of truth — any release-tagged change must update it
in the same commit. **No VERSION bump → no release.** Release tags are `v<VERSION>`,
created by release automation, never by hand.

## Secrets & public-safety posture

**Secrets stay in OpenBao.** Never commit, track, or reference credentials, tokens,
keys, kubeconfigs, or Terraform state — not even with a "remove later" TODO. Use
**placeholder syntax** for any IPs, DNS names, or operator identity in code, docs,
PR descriptions, or issues (`<control-node-public-ip>`, `dmf.example.com`,
`<handle>`). A local pre-commit gitleaks hook runs on commit, and CI runs
secret-scanning + scrub gates on every PR — but redaction is your responsibility
first. If you need a secret, ask a maintainer — do not improvise a transport.

## Must / Must not

### MUST
- Open changes as **GitHub PRs against `main`** with **signed-off** commits.
- Use Conventional Commit messages and `<handle>/<short-slug>` topic branches.
- Update `VERSION` in the same commit as any release-tagged change.
- Use **placeholder syntax** for all IPs / DNS / operator identity in every artifact.

### MUST NOT
- Commit secrets, tokens, keys, kubeconfigs, or Terraform state.
- Push directly to `main`, force-push, or use `--no-verify` / `--no-gpg-sign`.
- Paste secrets, real IPs/DNS, or operator identity into issues, PRs, or CI logs.

## AI agent contract

Much of this platform is built by AI agents. Agents contribute the same way:
**PRs against `main`, signed off, CI green.** Additionally, agents must run cluster
mutation only via `bin/run-playbook.sh` (ADR-0010), must not use
`--no-verify`/`--force`/`--no-gpg-sign`, and must stop and ask before modifying a
sub-repo with uncommitted state.

## Reporting security issues

See [`SECURITY.md`](SECURITY.md). **Do not** open a public issue for a vulnerability.

## License & spec

Contributions are licensed under [Apache 2.0](LICENSE). The canonical governance
model is **ADR-0041 — DMF Release and Contribution Model** in the
[dmfdeploy/dmfdeploy](https://github.com/dmfdeploy/dmfdeploy)
umbrella repo (`docs/decisions/`).
