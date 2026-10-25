# Media Function descriptor schemas

These schemas define the `dmf-function.yaml` descriptor that sits next to a
Helm chart in `packages/<key>/`. The descriptor declares what a function is,
what it ships and how its ports behave. It never declares trust. The catalog
source index and the ingestion record own source class, launcher privilege,
promotion state and revocation, so the schema rejects those fields.

| File | Checks |
|---|---|
| `dmf-function.schema.json` | The descriptor (JSON Schema draft 2020-12). |
| `revocation-list.schema.json` | A revocation list snapshot that the tool reads for freshness. |

A chart that ships in this repo also carries its own `values.schema.json`.
Helm validates values against it on `install`, `template` and `lint`. Helm
compiles patterns with RE2, so chart schemas can't use lookahead.

## Run the conformance tool

Install the dependencies once:

```bash
python3 -m pip install pyyaml jsonschema
```

Check a package, or a chart that has no descriptor yet:

```bash
bin/dmf-function-conform packages/<key>
bin/dmf-function-conform --chart-only charts/nmos-crosspoint
```

A package carries no launcher code. Its top level may hold only the chart
layout: `Chart.yaml`, `Chart.lock`, `values.yaml`, `values.schema.json`,
`dmf-function.yaml`, `.helmignore`, files named `README*`, `LICENSE*`,
`NOTICE*` or `CHANGELOG*`, and the `templates/`, `charts/`, `crds/`, `ci/`
and `files/` directories. `files/` is for workload content that templates
mount, such as a script delivered through a ConfigMap. The tool also rejects
launcher-named files and directories, such as `playbooks/`, anywhere outside
`templates/`.

The tool prints one `VIOLATION` line per problem and exits with 1 if it finds
any. It also runs `helm lint --strict`; pass `--skip-helm` to leave that out.

To check revocation state, pass `--revocation FILE` or `--require-revocation`.

## Run the tool's tests

```bash
python3 -m unittest discover -s tests/conformance -v
```

`tests/conformance/cases.yaml` lists every good and bad fixture. Each bad
fixture names the good twin it was derived from and the text its violation
must contain.

## Alignment with AMWA IN-003

[IN-003](https://github.com/AMWA-TV/in-003) is an AMWA draft, at `v1alpha1`,
for a media function resource manifest. The repository publishes no tags or
releases yet, so dmfdeploy tracks it at a pinned commit, currently
[`acfa2b9`](https://github.com/AMWA-TV/in-003/tree/acfa2b9f7cb71874df2df264c746a6b72b208cab).

The descriptor stays dmfdeploy's own format. It aligns with IN-003 by field
name and through a generated export. This repository links to IN-003 and uses
its field names, and it copies none of its schema or prose.

### Shared field names

Where a descriptor field means the same thing as an IN-003 field, it uses the
IN-003 name. The IN-003 paths below are manifest paths. A port maps to an
entry in `spec.inputs` when its `direction` is `in`, and to an entry in
`spec.outputs` when it's `out`; the table writes either list as `spec.<dir>`.

| Descriptor path | IN-003 manifest path | Note |
|---|---|---|
| `ports[].geometry.frame_width` | `spec.<dir>[].format.video.frame_width` | Width in pixels. |
| `ports[].geometry.frame_height` | `spec.<dir>[].format.video.frame_height` | Height in pixels. |
| `ports[].geometry.frame_rate` | `spec.<dir>[].format.video.frame_rate` | The descriptor holds a numerator and a denominator; IN-003 holds a string. The export converts it. |
| `ports[].geometry.sample_rate` | `spec.<dir>[].format.audio.sample_rate` | Sample rate in hertz. |
| `ports[].geometry.channels` | `spec.<dir>[].format.audio.channels` | Number of audio channels. |
| `placement.host_capabilities.cpu` | `spec.requirements.host_capabilities.cpu.features` | The descriptor lists feature names as placement hints; IN-003 puts them under requirements. |
| `placement.host_capabilities.network` | `spec.requirements.host_capabilities.network[].features` | Feature names only. IN-003 also describes each network interface. |

### Where the descriptor goes further

The descriptor covers areas that IN-003 doesn't cover yet:

- **Artefact identity.** The chart and every image are pinned by digest, each
  release names its source commit and provenance record, and revocation comes
  from a revocation list. Question 6 in
  [IN-003 issue #4](https://github.com/AMWA-TV/in-003/issues/4) asks whether
  artefact identity belongs in IN-003 or in a separate Increment.
- **Closed content.** The schema rejects unknown keys everywhere. Question 1
  in the same issue asks whether IN-003 intends open content.
- **Package-level checks.** The conformance tool checks things a schema
  can't, such as version identity with `Chart.yaml`, package-key collisions
  and revocation freshness. No question in issue #4 covers these yet.

Question 2 in that issue, on where `host_capabilities` belongs, affects the
last two rows of the table.

### Compatibility path

An IN-003 manifest is generated from the descriptor. That optional manifest
arrives with
[dmfdeploy/dmfdeploy#637](https://github.com/dmfdeploy/dmfdeploy/issues/637),
together with the descriptor field that points to it and the conformance
check that compares the two. Until then the descriptor has no such field, and
the schema rejects one.
Tests check the generated manifest against the IN-003 schema, fetched at the
pinned commit at test time. The IN-003 schema is never vendored into this
repository.

When IN-003 publishes a new revision, the pin moves in a pull request that
updates the mapping table and the export together. [dmfdeploy/dmfdeploy#746](https://github.com/dmfdeploy/dmfdeploy/issues/746)
tracks this work.
