# `topology_params` seam mapping (umbrella #201 WP1)

How the `topology_params` contract (`catalog/topology-params.schema.yaml`)
moves from its authoritative source in this repo's git catalog through to a
running k3s topology. Normative source: `docs/plans/DMF v0.2b Multi-Source
Switch Spec 2026-07-15.md` §3.2 in the umbrella repo — this doc restates that
seam catalog-side, with the concrete J1 mapping. It documents the *intended*
seam; wiring it up is WP2 (charts), WP3 (dmf-runbooks playbook projection),
and WP3a (dmf-cms launch seam) — this WP is doc/schema only.

`topology_params` is also the declared, named future input of "Design beat
v1" (umbrella issue #231) — see the schema file's own Design-surface-input
section for the normative statement; #231 authors instances of this
contract, it does not define a new one.

## The four hops

```
git catalog (dmf-media, authoritative — authors topology_params)
    │   topology_params  (the whole named object, one instance per topology)
    ▼
AWX launch extra_vars  ──►  carries topology_params AS THE OBJECT
    │                        (job-template defaults hold NO topology values —
    │                         nothing is re-authored per layer)
    ▼
playbook (dmf-runbooks, the projector)  ──►  iterates sources[]; derives
    │                                         per-source flow.id / pattern +
    │                                         the coordinator active-source
    │                                         + the viewer's OWN flow.id
    │                                         (looked up as
    │                                         sources[source_selection]
    │                                         .flow_id) — mechanical,
    │                                         lossless, nothing re-authored
    ▼
Helm chart values (mxl-fabrics-demo)  ──►  helm --set flow.id=… pattern=…
    │                                       (per source); viewer's own
    │                                       flow.id (the selected source's,
    │                                       not the baseline's) + coordinator
    │                                       active-source
    ▼
k3s runtime (reconciled)
```

1. **Catalog (this repo, authoritative).** `topology_params` is authored once
   as its own catalog entry — see `catalog/topology-params.j1.yaml` for J1's
   concrete two-source instance. It is *not* split across the existing
   per-function entries (`catalog/mxl-videotestsrc.yaml`,
   `catalog/mxl-videotest-view.yaml`); those describe the deployable function
   *types* (image, chart, health probe) and are unchanged by this contract —
   §3.3's N-source-shaped constraint explicitly forbids per-source catalog
   entries (no `source_a`/`source_b`, no new catalog/job-template entries to
   go from 2 sources to N).

2. **AWX launch `extra_vars` (dmf-cms → AWX, WP3a).** The console's launch
   call threads the `topology_params` object unchanged as `extra_vars` on the
   AWX job launch. Today (`dmf-cms/main.py:1567` → `_run_deploy_operation` →
   `awx.py:117` `launch_job`) that call posts `body={}` — WP3a's job is to
   carry `topology_params` there instead. The catalog JTs already have
   `ask_variables_on_launch: true` (umbrella #239, dmf-infra), which is a
   prerequisite: AWX silently *drops* launch-time `extra_vars` on a JT where
   that flag is `false`.

   `target_facility` is the one field the catalog does **not** author a real
   value for. It is a NetBox site slug, minted per-env by dmf-infra's
   `dmf-born-inventory` role, and env-rotating by design — the public
   catalog must never hardcode env material. WP3a's console launch seam
   resolves the current env's NetBox site slug at launch time (exactly one
   site in the J1 lane) and injects it into the `extra_vars` payload in
   place of the catalog instance's illustrative placeholder (see
   `catalog/topology-params.j1.yaml`).

   **`target_facility` does NOT select the AWX job's inventory/`limit`.**
   In J1 the job template's scope is PREBOUND: catalog JTs are created by
   `awx-integration` against the NetBox-driven inventory, which in the J1
   lane covers exactly the single facility, before any launch happens.
   `target_facility` carries the *expected* site for the launcher to check
   against, not a selector the launch call resolves inventory from.
   Launch-time inventory/`limit` selection is explicitly deferred to the
   #231 / multi-facility follow-on.

3. **Playbook projection (dmf-runbooks, WP3).** The launch playbook receives
   `topology_params` as `extra_vars` and iterates `sources[]`, projecting
   each source's `id`/`flow_id`/`pattern`, the coordinator's
   `active-source` (= `viewer.source_selection`), and the viewer's OWN
   `flow.id` — looked up as `sources[<source_selection>].flow_id`, not left
   on whichever source's flow the viewer started with — into Helm `--set`
   values, mechanically and losslessly (no re-authoring, no
   interpretation). `target_facility` is validated here fail-closed as
   AGREEMENT, not selection: the received slug must match the NetBox site
   slug backing the JT's already-prebound inventory scope (WP3a resolved it
   to the env's real site at launch, hop 2 above — J1 has exactly one legal
   site, §3.4), or the run is refused. WP3 never derives inventory/`limit`
   from it.

4. **Helm chart values (mxl-fabrics-demo, WP2).** One Helm release per
   `sources[]` entry (role=source), each with its own `flow.id` and
   `pattern` — the chart's fixed shared UUID (`values.yaml:60` today) goes
   away. One release for the viewer (role=view), receiving TWO projected
   values: the coordinator's shared `active-source` field (set to
   `viewer.source_selection`'s value) and the viewer's own `flow.id` (the
   looked-up value from step 3 above — the selected source's flow, not a
   fixed one). Source-role supervisors gate their initiator on
   `active-source == <own source.id>` (§4.1 — this supervisor logic is new
   WP2 work, not present in shipped `source-handshake.yaml`).

## J1's concrete mapping

| `topology_params` field | J1 value (see `catalog/topology-params.j1.yaml`) | Chart projection |
|---|---|---|
| `sources[0].id` / `.flow_id` / `.pattern` | first source instance | `helm --set role=source --set flow.id=<flow_id> --set pattern=<pattern>` → one Helm release |
| `sources[1].id` / `.flow_id` / `.pattern` | second source instance | second, independent Helm release, same chart |
| `viewer.id` | the viewer instance | `helm --set role=view` → the viewer's Helm release |
| `viewer.source_selection` | one of the two source ids | TWO values: coordinator `data.active-source` (patched by the playbook, gates the two source supervisors) AND the viewer's own `flow.id`, looked up as `sources[source_selection].flow_id` — the selected source's flow, not the baseline one |
| `target_facility` | **resolved by WP3a at launch** (the env's sole NetBox site slug, dmf-born-inventory-minted) — the catalog instance's `dmf-example-site` is illustrative only, never the real value; **validated by WP3 as AGREEMENT, not selection** — must match the NetBox site already backing the JT's prebound inventory scope, fail-closed | k3s namespace `mxl`, single-node placement; L3 (#202) capacity scope. Does NOT select AWX inventory/`limit` in J1 — that scope is prebound at JT-creation time (`awx-integration`), not at launch; launch-time inventory selection from this field is deferred to #231/multi-facility |

## What this WP does not do

Doc/schema only (umbrella #201 WP1) — no chart, playbook, or console code
changes. The mapping above is the *contract* WP2/WP3/WP3a implement against;
none of the projection, chart parameterisation, or launch-seam plumbing is
built here.
