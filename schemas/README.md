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
