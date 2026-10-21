#!/usr/bin/env python3
"""Static gate for the crosspoint slice (dmfdeploy/dmfdeploy#629).

Moving the controller to another line, commit or image must pass the slice;
it is not a values-only change. This check makes that hold in review:

1. Every image in the fixture manifests is repository@sha256:<digest> with no
   tag (the controller image is the __CONTROLLER_IMAGE__ placeholder, filled
   from crosspoint/lines.json).
2. Every line in lines.json names a 40-character commit and a digest-only
   image.
3. Every line has a recorded slice run, reference/crosspoint-<line>/
   observations.json, made with that same commit and image, in which every
   acceptance item and every slice item passed.

So a pin change fails here until someone runs the slice on the new pin
(run.sh with SWITCH_VIA=crosspoint CONTROLLER_LINE=<line>) and commits its
observations. The fixture workflow runs the slice itself on the same change.

Usage: check_pins.py [FIXTURE_DIR]   (default: this file's parent directory)
Exit 0 when all checks pass, 1 otherwise; each problem is printed.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DIGEST_REF = re.compile(r"^[a-z0-9._\-/:]+@sha256:[0-9a-f]{64}$")
IMAGE_LINE = re.compile(r"^\s*(?:-\s*)?image:\s*(\S+)\s*$")
PLACEHOLDER = "__CONTROLLER_IMAGE__"


def digest_only(ref: str) -> bool:
    if not DIGEST_REF.match(ref):
        return False
    name = ref.split("@", 1)[0]
    return ":" not in name.rsplit("/", 1)[-1]


def check(fixture: Path) -> list[str]:
    problems: list[str] = []
    for manifest in sorted((fixture / "manifests").glob("*.yaml")):
        for n, text in enumerate(manifest.read_text().splitlines(), 1):
            m = IMAGE_LINE.match(text)
            if m and m.group(1) != PLACEHOLDER and not digest_only(m.group(1)):
                problems.append(f"{manifest.name}:{n}: image is not digest-only: {m.group(1)}")

    lines = json.loads((fixture / "crosspoint" / "lines.json").read_text())
    if lines.get("default") not in lines.get("lines", {}):
        problems.append(f"lines.json: default line {lines.get('default')!r} is not defined")
    for name, line in lines.get("lines", {}).items():
        if not re.fullmatch(r"[0-9a-f]{40}", line.get("commit", "")):
            problems.append(f"lines.json: {name}: commit must be a full 40-character SHA")
        if not digest_only(line.get("image", "")):
            problems.append(f"lines.json: {name}: image must be repository@sha256:<digest>, got {line.get('image')!r}")
        ref = fixture / "reference" / f"crosspoint-{name}" / "observations.json"
        if not ref.is_file():
            problems.append(f"{name}: no recorded slice run at {ref.relative_to(fixture)}")
            continue
        obs = json.loads(ref.read_text())
        ran = obs.get("controller_line", {})
        if obs.get("switch_via") != "crosspoint":
            problems.append(f"{name}: {ref.relative_to(fixture)} is not a crosspoint run")
        for key in ("commit", "image"):
            if ran.get(key) != line.get(key):
                problems.append(f"{name}: lines.json {key} {line.get(key)!r} was not tested; "
                                f"the recorded slice ran {ran.get(key)!r}")
        failed = [k for k, v in {**obs.get("acceptance", {}), **obs.get("slice_acceptance", {})}.items() if v is not True]
        if not obs.get("acceptance") or not obs.get("slice_acceptance") or failed:
            problems.append(f"{name}: recorded slice run did not pass: {failed or 'no acceptance recorded'}")
    return problems


def main() -> int:
    fixture = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    problems = check(fixture)
    for p in problems:
        print(f"FAIL  {p}")
    if not problems:
        print("OK    controller pins are digest-only and each has a passing slice run on the same commit and image")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
