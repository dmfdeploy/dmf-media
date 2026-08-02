#!/usr/bin/env python3
"""check-catalog-demand.py - L3 demand profile equality gate (umbrella #202 WP0).

For every catalog/*.yaml entry whose provision.chart.name is mxl-fabrics-demo,
render the chart for that entry's role (ebu.media_function_type) and compute
its steady-state pod demand:

    per rendered Deployment:
        demand = sum(containers requests) + spec.template.spec.overhead (if present)
        demand *= spec.replicas (default 1)
    role total = sum(demand over all rendered Deployments)

initContainers are refused fail-closed: any rendered Deployment carrying a
non-empty spec.template.spec.initContainers hard-fails the gate by name.
Kubernetes' real init-container scheduling accounting is NOT a simple sum or
max-of-sums — sequential init containers schedule at the max of any single
init container's own request vs the app-container sum, and restartable
sidecars use a different cumulative formula again. Emulating either wrong
here would silently certify an incorrect demand number, so this gate refuses
to guess; scheduler-accurate init accounting must be added deliberately the
first time a chart actually needs initContainers, not approximated.

Fail if that total does not equal the entry's declared
provision.resources.requests — a drifted catalog profile would feed the L3
capacity preflight a stale number while the chart itself claims something
different.

Quantity grammar is intentionally narrow and fails closed: memory must be
whole binary Ki/Mi/Gi ('256Mi') on both sides. cpu grammar differs by side
deliberately: the CATALOG-declared cpu (our authored contract) must be
explicit whole millicores with the 'm' suffix ('225m') — a bare integer is
refused outright, since it is far more plausibly a forgotten 'm' than an
intentional whole-core request. The RENDERED chart's cpu may additionally be
a bare whole core ('1', ×1000m) — that is legitimate Kubernetes YAML the
chart may legally emit, even though we'd never author it that way in the
catalog by hand. Anything else (decimals, K/M/G, bare bytes) is refused on
both sides, so a chart evolving to use a quantity form this gate can't parse
fails loudly instead of silently skipping. All arithmetic is integer; a
quantity that parses to zero fails even if both sides agree on it, since zero
can never be a real workload demand and matching zeros would mask the parse
actually having failed.

The catalog is also checked for coverage: exactly one entry per expected role
({source, view}) must cover this chart. Coverage is usually the entry's own
ebu.media_function_type, but umbrella #201's topology-launch model lets a
'view' entry (via topology_ref) also cover 'source' role on behalf of the
per-launch sources[] its own release-group loop provisions — N
topology-listed sources still count once. Fewer roles covered (a
chart-name typo, a deleted profile), extras, or duplicates (e.g. a standing
source entry alongside topology-derived source coverage) are a hard
failure — there is no silent-success path when nothing matches.

Topology-derived source coverage is not just a role-count claim (umbrella
#347): the topology instance's own topology_params.source_profile — ONE
demand profile shared by every sources[] release, authored once in the
referenced topology_ref file (dmf-media catalog/topology-params.j1.yaml) —
is validated against a real rendered role=source release-group, exactly as
a standing entry's own declared demand is validated against its own role
render. A topology-carrying 'view' entry's full reported demand is its own
declared profile PLUS len(sources[]) copies of source_profile — never the
viewer profile alone, and never the viewer profile scaled by N (that would
silently drop the source releases' own, different, cost from the number
this gate certifies). Missing, malformed, or drifted source_profile is a
hard failure, same as any other quantity mismatch this gate catches.

Usage: bin/check-catalog-demand.py   (run from the dmf-media repo root)
"""

import os
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG_DIR = REPO_ROOT / "catalog"
CHART_NAME = "mxl-fabrics-demo"
CHART_DIR = REPO_ROOT / "charts" / CHART_NAME
EXPECTED_ROLES = {"source", "view"}


class QuantityError(Exception):
    pass


def cpu_to_millicores_rendered(value, origin):
    s = str(value).strip()
    if s.endswith("m") and s[:-1].isdigit():
        n = int(s[:-1])
    elif s.isdigit():
        n = int(s) * 1000
    else:
        raise QuantityError(
            f"cpu quantity '{value}' ({origin}) is not accepted grammar — "
            "only whole millicores ('123m') or whole cores ('1') are allowed"
        )
    if n <= 0:
        raise QuantityError(f"cpu quantity '{value}' ({origin}) must be > 0, got {n}m")
    return n


def cpu_to_millicores_declared(value, origin):
    s = str(value).strip()
    if not (s.endswith("m") and s[:-1].isdigit()):
        suspicion = (
            f" — did you mean '{s}m'?" if s.isdigit() else ""
        )
        raise QuantityError(
            f"cpu quantity '{value}' ({origin}) is not accepted grammar — "
            "the catalog must declare explicit whole millicores with the "
            f"'m' suffix (e.g. '225m'); bare integers are refused{suspicion}"
        )
    n = int(s[:-1])
    if n <= 0:
        raise QuantityError(f"cpu quantity '{value}' ({origin}) must be > 0, got {n}m")
    return n


def memory_to_bytes(value, origin):
    s = str(value).strip()
    units = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3}
    n = None
    for suffix, mult in units.items():
        if s.endswith(suffix) and s[: -len(suffix)].isdigit():
            n = int(s[: -len(suffix)]) * mult
            break
    if n is None:
        raise QuantityError(
            f"memory quantity '{value}' ({origin}) is not accepted grammar — "
            "only whole binary Ki/Mi/Gi ('256Mi') are allowed"
        )
    if n <= 0:
        raise QuantityError(f"memory quantity '{value}' ({origin}) must be > 0, got {n}B")
    return n


def sum_container_requests(containers, kind, role):
    cpu_total = 0
    mem_total = 0
    for c in containers:
        name = c.get("name", "<unnamed>")
        requests = c.get("resources", {}).get("requests", {})
        if "cpu" not in requests or "memory" not in requests:
            raise QuantityError(
                f"{kind} container '{name}' (role={role}) is missing "
                "resources.requests.cpu/memory in the rendered chart"
            )
        origin = f"rendered {kind} container '{name}' role={role}"
        cpu_total += cpu_to_millicores_rendered(requests["cpu"], origin)
        mem_total += memory_to_bytes(requests["memory"], origin)
    return cpu_total, mem_total


def deployment_demand(doc, role):
    name = doc.get("metadata", {}).get("name", "<unnamed>")
    spec = doc.get("spec", {}).get("template", {}).get("spec", {})
    containers = spec.get("containers", [])
    init_containers = spec.get("initContainers", [])

    if init_containers:
        raise QuantityError(
            f"rendered Deployment '{name}' (role={role}) has "
            f"{len(init_containers)} initContainer(s) — init-container demand "
            "accounting is deliberately unsupported by this gate. Kubernetes "
            "schedules sequential init containers at the max of any single "
            "init container's own request vs the app-container sum, and "
            "restartable sidecars use a different cumulative formula again; "
            "emulating either wrong here would silently certify an incorrect "
            "demand number. Add scheduler-accurate init accounting "
            "deliberately when a chart first needs initContainers."
        )

    if not containers:
        raise QuantityError(f"rendered Deployment '{name}' (role={role}) has zero containers")

    cpu, mem = sum_container_requests(containers, "container", role)

    overhead = spec.get("overhead") or {}
    if "cpu" in overhead:
        cpu += cpu_to_millicores_rendered(overhead["cpu"], f"rendered overhead.cpu role={role}")
    if "memory" in overhead:
        mem += memory_to_bytes(overhead["memory"], f"rendered overhead.memory role={role}")

    replicas = doc.get("spec", {}).get("replicas", 1)
    if not isinstance(replicas, int) or replicas <= 0:
        raise QuantityError(f"rendered Deployment '{name}' (role={role}) has invalid replicas={replicas!r}")

    return cpu * replicas, mem * replicas, len(containers)


def rendered_role_demand(role):
    result = subprocess.run(
        ["helm", "template", "t", str(CHART_DIR), "--set", f"role={role}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise QuantityError(f"helm template failed for role={role}:\n{result.stderr}")

    total_cpu_m = 0
    total_mem_b = 0
    total_containers = 0
    deployment_count = 0
    for doc in yaml.safe_load_all(result.stdout):
        if not doc or doc.get("kind") != "Deployment":
            continue
        deployment_count += 1
        cpu, mem, count = deployment_demand(doc, role)
        total_cpu_m += cpu
        total_mem_b += mem
        total_containers += count

    if deployment_count == 0:
        raise QuantityError(f"rendered chart for role={role} produced zero Deployments")

    return {"cpu_m": total_cpu_m, "mem_b": total_mem_b, "containers": total_containers}


def _validate_topology_ref_path(ref, origin):
    """Fail-closed basename/containment check for a topology_ref value.

    Mirrors dmf_cms.catalog.load_topology_instance's own guard (dmf-cms
    src/dmf_cms/catalog.py): topology_ref must be a plain filename inside
    CATALOG_DIR — no path separators, parent traversal, or absolute paths,
    checked twice (pre-I/O basename shape, then resolved-path containment)
    for the same defense-in-depth reason that function documents.

    Type-checked before any of the path calls below (umbrella #347 gate
    fix): a truthy non-string YAML value (an int, a mapping, a list) or an
    explicit null previously reached ``os.path.isabs``/``os.sep in ref``
    unchecked and raised a bare ``TypeError`` that escaped this module's
    ``QuantityError`` handling in ``main()`` entirely. Both invalid-type and
    empty-string are refused here, fail-closed, as a named gate failure.
    """
    if not isinstance(ref, str):
        raise QuantityError(f"{origin}: topology_ref type invalid: expected string, got {type(ref).__name__}")
    if not ref:
        raise QuantityError(f"{origin}: topology_ref type invalid: expected a non-empty string, got ''")
    if (
        os.path.isabs(ref)
        or os.sep in ref
        or (os.altsep and os.altsep in ref)
        or Path(ref).name != ref
        or ".." in Path(ref).parts
    ):
        raise QuantityError(
            f"{origin}: topology_ref {ref!r} must be a plain filename within "
            "the catalog directory — no path separators, parent traversal, "
            "or absolute paths"
        )
    catalog_root = CATALOG_DIR.resolve()
    path = (catalog_root / ref).resolve()
    if path != catalog_root and catalog_root not in path.parents:
        raise QuantityError(f"{origin}: topology_ref {ref!r} resolves outside the catalog directory")
    return path


def _load_topology_object(key, topology_ref):
    """Load a topology_ref target and return its topology_params object.

    Validates just enough of the umbrella #201 WP1 topology_params contract
    for role-coverage and demand-validation purposes: the target file must
    exist, parse as a mapping, carry a top-level topology_params object with
    a non-empty sources[] list and a viewer mapping. Full contract
    validation (schema version, flow_id uniqueness, viewer.source_selection,
    ...) is dmf_cms.catalog.load_topology_instance's job at launch time, not
    this gate's — this only needs to know the target names real sources (so
    a viewer entry can claim 'source' role coverage on their behalf) and, as
    of umbrella #347, carries the shared source_profile demand contract
    validated separately by _validate_source_profile.
    """
    origin = f"{key} topology_ref"
    path = _validate_topology_ref_path(topology_ref, origin)
    if not path.is_file():
        raise QuantityError(f"{origin} '{topology_ref}' not found in catalog")
    try:
        raw = yaml.safe_load(path.read_text())
    except Exception as exc:
        raise QuantityError(f"{origin} '{topology_ref}' failed to parse: {exc}")
    if not isinstance(raw, dict):
        raise QuantityError(f"{origin} '{topology_ref}' did not yield a mapping")
    tp = raw.get("topology_params")
    if not isinstance(tp, dict):
        raise QuantityError(f"{origin} '{topology_ref}' has no top-level topology_params object")
    sources = tp.get("sources")
    if not isinstance(sources, list) or not sources:
        raise QuantityError(f"{origin} '{topology_ref}': sources must be a non-empty list")
    if not isinstance(tp.get("viewer"), dict):
        raise QuantityError(f"{origin} '{topology_ref}': viewer must be a mapping")
    return tp


def _validate_source_profile(key, topology_ref, tp):
    """Validate the topology's shared source_profile against a real render.

    umbrella #347: every sources[] release shares ONE demand profile,
    authored once as topology_params.source_profile — never a per-source
    catalog entry (§3.3's no-per-source-entries rule extends to demand).
    Uses the same catalog-strict quantity grammar as a standing entry's own
    provision.resources.requests (cpu_to_millicores_declared/memory_to_bytes)
    and the same rendered-role comparison as the per-entry demand loop
    below, just against role=source instead of the caller entry's own role.
    Fails closed if the profile is missing, malformed, or does not equal the
    rendered role=source chart demand.
    """
    origin = f"{key} topology_ref '{topology_ref}' source_profile"
    profile = tp.get("source_profile")
    if not isinstance(profile, dict):
        raise QuantityError(f"{origin} is missing or not a mapping")
    resources = profile.get("resources")
    declared = resources.get("requests") if isinstance(resources, dict) else None
    if not declared or "cpu" not in declared or "memory" not in declared:
        raise QuantityError(f"{origin}: missing resources.requests.cpu/memory demand profile")

    declared_cpu_m = cpu_to_millicores_declared(declared["cpu"], f"{origin}.resources.requests.cpu")
    declared_mem_b = memory_to_bytes(declared["memory"], f"{origin}.resources.requests.memory")
    rendered = rendered_role_demand("source")

    if rendered["cpu_m"] != declared_cpu_m or rendered["mem_b"] != declared_mem_b:
        raise QuantityError(
            f"{origin}: demand mismatch — declared cpu={declared['cpu']} "
            f"({declared_cpu_m}m) memory={declared['memory']} ({declared_mem_b}B), "
            f"rendered (role=source, {rendered['containers']} containers) "
            f"cpu={rendered['cpu_m']}m memory={rendered['mem_b']}B"
        )
    return declared_cpu_m, declared_mem_b, rendered["containers"]


def _fmt_cpu_m(n):
    return f"{n}m"


def _fmt_mem_b(n):
    if n % (1024**2) == 0:
        return f"{n // (1024**2)}Mi"
    if n % 1024 == 0:
        return f"{n // 1024}Ki"
    return f"{n}B"


def _covered_roles(entry_path, entry):
    """Return (covered_roles, topology_object_or_None) for one catalog entry.

    An entry always covers its own ebu.media_function_type. A 'view' entry
    may additionally carry a topology_ref (umbrella #201 WP3a) naming a
    topology_params instance in this same catalog dir; per
    mxl-videotest-view.yaml's own netbox_service comment ("the source-*
    records ... are created by the viewer JT's own release-group loop only
    while a topology launch is live"), the source role it names is
    provisioned per-launch by the viewer, not by a standing catalog entry —
    so a topology_ref with a real sources[] ALSO counts as 'source'
    coverage. Source ids (source-a, source-b, ...) are launch-time
    identities, not roles: N sources still collapse to exactly one
    'source' coverage claim, so the exact-one-provider check below stays
    meaningful. A topology_ref on any entry whose own role isn't 'view'
    fails closed — the runbook makes topology explicitly viewer-owned.

    ``topology_ref`` is looked up by KEY PRESENCE, not truthiness (umbrella
    #347 gate fix): an entry that never authors the field stays plain
    'absent' (no source coverage, no failure) — but a present value that
    isn't a non-empty string (an int, a mapping, a list, or an explicit
    ``null``) is a named gate failure, not a silent fall-through to
    'absent'. Truthiness alone previously let ``topology_ref: null`` slip
    past unchecked (None is falsy) straight into "no coverage claimed",
    the same class of miss as the unguarded TypeError this fix also closes
    one level down in _validate_topology_ref_path.
    """
    key = entry.get("key", entry_path.name)
    role = entry.get("ebu", {}).get("media_function_type")

    if "topology_ref" not in entry:
        return {role}, None

    topology_ref = entry["topology_ref"]
    if not isinstance(topology_ref, str) or not topology_ref:
        raise QuantityError(
            f"{key}: topology_ref type invalid: expected string, got {type(topology_ref).__name__}"
            if not isinstance(topology_ref, str)
            else f"{key}: topology_ref type invalid: expected a non-empty string, got ''"
        )

    if role != "view":
        raise QuantityError(
            f"{key}: topology_ref is set but ebu.media_function_type is "
            f"{role!r}, not 'view' — topology instances are viewer-owned"
        )

    tp = _load_topology_object(key, topology_ref)
    return {role, "source"}, tp


def main():
    failures = []

    entries = []
    for entry_path in sorted(CATALOG_DIR.glob("*.yaml")):
        entry = yaml.safe_load(entry_path.read_text())
        chart = entry.get("provision", {}).get("chart", {})
        if chart.get("name") == CHART_NAME:
            entries.append((entry_path, entry))

    role_counts = {}
    topology_by_key = {}
    for entry_path, entry in entries:
        key = entry.get("key", entry_path.name)
        try:
            covered, tp = _covered_roles(entry_path, entry)
        except QuantityError as e:
            failures.append(str(e))
            continue
        for covered_role in covered:
            role_counts.setdefault(covered_role, []).append(key)
        if tp is not None:
            topology_by_key[key] = tp

    if set(role_counts.keys()) != EXPECTED_ROLES or any(
        len(keys) != 1 for keys in role_counts.values()
    ):
        failures.append(
            "catalog role coverage mismatch for chart "
            f"'{CHART_NAME}': expected exactly one entry each for "
            f"{sorted(EXPECTED_ROLES)}, found "
            f"{ {r: ks for r, ks in role_counts.items()} }"
        )

    for entry_path, entry in entries:
        key = entry.get("key", entry_path.name)
        role = entry.get("ebu", {}).get("media_function_type")
        if role not in EXPECTED_ROLES:
            failures.append(
                f"{key}: ebu.media_function_type is '{role}', expected one of {sorted(EXPECTED_ROLES)}"
            )
            continue

        declared = entry.get("provision", {}).get("resources", {}).get("requests")
        if not declared or "cpu" not in declared or "memory" not in declared:
            failures.append(
                f"{key}: missing provision.resources.requests.cpu/memory demand profile"
            )
            continue

        try:
            declared_cpu_m = cpu_to_millicores_declared(declared["cpu"], f"catalog {key} provision.resources.requests.cpu")
            declared_mem_b = memory_to_bytes(declared["memory"], f"catalog {key} provision.resources.requests.memory")
            rendered = rendered_role_demand(role)
        except QuantityError as e:
            failures.append(f"{key}: {e}")
            continue

        if rendered["cpu_m"] != declared_cpu_m or rendered["mem_b"] != declared_mem_b:
            failures.append(
                f"{key}: demand mismatch — declared cpu={declared['cpu']} "
                f"({declared_cpu_m}m) memory={declared['memory']} ({declared_mem_b}B), "
                f"rendered (role={role}, {rendered['containers']} containers) "
                f"cpu={rendered['cpu_m']}m memory={rendered['mem_b']}B"
            )
            continue

        print(
            f"PASS: {key} (role={role}) declared == rendered "
            f"(cpu={declared['cpu']}, memory={declared['memory']}, "
            f"{rendered['containers']} containers)"
        )

        if "topology_ref" in entry:
            tp = topology_by_key.get(key)
            if tp is None:
                # Already recorded as a failure in the coverage pass above
                # (bad type, wrong role, unparseable target, ...) — do not
                # double-report it here.
                continue
            topology_ref = entry["topology_ref"]
            try:
                source_cpu_m, source_mem_b, source_containers = _validate_source_profile(key, topology_ref, tp)
            except QuantityError as e:
                failures.append(f"{key}: {e}")
                continue
            n = len(tp["sources"])
            print(
                f"PASS: {key} topology_ref '{topology_ref}' source_profile "
                f"(role=source) declared == rendered (cpu={_fmt_cpu_m(source_cpu_m)}, "
                f"memory={_fmt_mem_b(source_mem_b)}, {source_containers} containers)"
            )
            agg_cpu_m = declared_cpu_m + n * source_cpu_m
            agg_mem_b = declared_mem_b + n * source_mem_b
            print(
                f"J1 aggregate for {key}: {n} x {_fmt_cpu_m(source_cpu_m)}/{_fmt_mem_b(source_mem_b)} "
                f"+ {_fmt_cpu_m(declared_cpu_m)}/{_fmt_mem_b(declared_mem_b)} = "
                f"{_fmt_cpu_m(agg_cpu_m)}/{_fmt_mem_b(agg_mem_b)}"
            )

    if failures:
        print(f"\nFAIL: {len(failures)} catalog demand check(s) failed:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
