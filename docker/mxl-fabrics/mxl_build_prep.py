#!/usr/bin/env python3
"""Build-prep gate: verify the MXL source checkout being patched/built
matches the upstream SHA this dmf-media revision was validated against
(umbrella dmfdeploy/dmfdeploy#315).

`docker/mxl-fabrics/Dockerfile` takes the upstream MXL source as an
operator-supplied build context with no ref pin of its own — the patch
scripts (patch-sink.py, patch-sink-too-early-pacing.py) are anchor-based and
fail loudly on *content* drift, but nothing recorded *which* SHA a build was
validated against, or checked it before patching started. This module closes
that gap: `verify_upstream_sha()` compares the expected SHA (recorded in
docker/mxl-fabrics/MXL_UPSTREAM_SHA, staged into the build context at
/tmp/MXL_UPSTREAM_SHA — see the Dockerfile) against the actual SHA of the
source checkout under build, and fails loudly on any mismatch — before
either patch script touches a single file.

Bump procedure (deliberate upstream bump): update
docker/mxl-fabrics/MXL_UPSTREAM_SHA to the new SHA, then rebuild. The patch
scripts' own anchor checks are the content-drift guard: they fail on their
own if upstream moved the code they patch — this SHA check only guards
*which commit* was built, it does not replace them.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

_SHA_LEN = 40
_HEX_CHARS = set("0123456789abcdefABCDEF")


def _is_hex40(value):
    return len(value) == _SHA_LEN and all(c in _HEX_CHARS for c in value)


def _expected_sha_candidates():
    candidates = []
    env_path = os.environ.get("MXL_UPSTREAM_SHA_FILE")
    if env_path:
        candidates.append(env_path)
    candidates.append("/tmp/MXL_UPSTREAM_SHA")
    candidates.append(str(Path(__file__).resolve().parent / "MXL_UPSTREAM_SHA"))
    return candidates


def _resolve_expected_sha():
    """First existing wins: MXL_UPSTREAM_SHA_FILE env, /tmp/MXL_UPSTREAM_SHA
    (the in-container location), or MXL_UPSTREAM_SHA next to this script."""
    tried = []
    for candidate in _expected_sha_candidates():
        tried.append(candidate)
        if os.path.isfile(candidate):
            with open(candidate) as f:
                raw = f.read().strip()
            if not _is_hex40(raw):
                sys.stderr.write(
                    "mxl_build_prep: expected-SHA file "
                    f"{candidate!r} does not contain exactly 40 hex chars "
                    f"(got {raw!r}). Fix docker/mxl-fabrics/MXL_UPSTREAM_SHA "
                    "(the source this file was staged from) and retry.\n"
                )
                sys.exit(1)
            return raw, candidate
    sys.stderr.write(
        "mxl_build_prep: could not find an expected-upstream-SHA file. "
        f"Tried, in order: {tried}. Expected exactly one line of 40 hex "
        "chars in docker/mxl-fabrics/MXL_UPSTREAM_SHA (staged into the "
        "build context at /tmp/MXL_UPSTREAM_SHA — see the Dockerfile), or "
        "set MXL_UPSTREAM_SHA_FILE to point at an equivalent file.\n"
    )
    sys.exit(1)


def _resolve_source_root():
    """env MXL_SOURCE_ROOT if set; else 'mxl' if that is a directory relative
    to CWD (the in-container layout — Dockerfile WORKDIR is /home/mxl and
    sources are COPY'd into ./mxl); else '.'."""
    env_root = os.environ.get("MXL_SOURCE_ROOT")
    if env_root:
        return env_root
    if os.path.isdir("mxl"):
        return "mxl"
    return "."


def _resolve_actual_sha():
    env_sha = os.environ.get("MXL_SOURCE_SHA", "").strip()
    if env_sha:
        actual, source = env_sha, "env MXL_SOURCE_SHA"
    else:
        source_root = _resolve_source_root()
        git_path = shutil.which("git")
        actual = None
        source = None
        if git_path:
            try:
                result = subprocess.run(
                    [git_path, "-C", source_root, "rev-parse", "HEAD"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                actual = result.stdout.strip()
                source = f"git -C {source_root} rev-parse HEAD"
            except subprocess.CalledProcessError:
                actual = None
        if actual is None:
            sys.stderr.write(
                "mxl_build_prep: could not determine the actual upstream "
                f"MXL SHA — MXL_SOURCE_SHA is not set, and {source_root!r} "
                "is not a usable git checkout (or `git` is unavailable). "
                "Rebuild passing --build-arg MXL_SOURCE_SHA=$(git -C "
                "<mxl-checkout> rev-parse HEAD).\n"
            )
            sys.exit(1)

    if not _is_hex40(actual):
        sys.stderr.write(
            f"mxl_build_prep: actual SHA from {source} is {actual!r} — not "
            "a full 40-char hex SHA. An abbreviation cannot be compared "
            "safely without the object database. Rebuild passing the FULL "
            "SHA: --build-arg MXL_SOURCE_SHA=$(git -C <mxl-checkout> "
            "rev-parse HEAD).\n"
        )
        sys.exit(1)
    return actual, source


def _mismatch_block(expected, expected_source, actual, actual_source):
    bar = "=" * 72
    return (
        f"\n{bar}\n"
        "mxl_build_prep: UPSTREAM MXL SHA MISMATCH — refusing to patch/build\n"
        f"{bar}\n"
        f"  expected SHA (from {expected_source}):\n"
        f"    {expected}\n"
        f"  actual SHA   (from {actual_source}):\n"
        f"    {actual}\n"
        "\n"
        "If this is a DELIBERATE upstream bump:\n"
        "  1. Update docker/mxl-fabrics/MXL_UPSTREAM_SHA to the new SHA.\n"
        "  2. Re-run the anchor checks by rebuilding — patch-sink.py and\n"
        "     patch-sink-too-early-pacing.py fail on their own anchors if\n"
        "     upstream moved the code they patch, which is the real\n"
        "     content-drift guard; this SHA check only guards which\n"
        "     commit was built.\n"
        "\n"
        "If this is UNINTENTIONAL, rebuild against the pinned SHA above,\n"
        "e.g.:\n"
        f"  git -C <mxl-checkout> checkout {expected}\n"
        "  --build-arg MXL_SOURCE_SHA=$(git -C <mxl-checkout> rev-parse HEAD)\n"
        f"{bar}\n"
    )


def verify_upstream_sha():
    """Compare the expected upstream MXL SHA against the actual SHA of the
    source checkout under build. Prints a one-line confirmation to stdout
    and returns on match; on mismatch (or if either SHA cannot be resolved)
    writes a loud diagnostic to stderr and exits non-zero."""
    expected, expected_source = _resolve_expected_sha()
    actual, actual_source = _resolve_actual_sha()
    if expected.lower() == actual.lower():
        print(
            f"mxl_build_prep: upstream SHA verified — {actual} "
            f"(expected per {expected_source})"
        )
        return
    sys.stderr.write(_mismatch_block(expected, expected_source, actual, actual_source))
    sys.exit(1)


if __name__ == "__main__":
    verify_upstream_sha()
