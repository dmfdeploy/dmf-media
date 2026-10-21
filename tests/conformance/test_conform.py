#!/usr/bin/env python3
"""Run every case in cases.yaml against bin/dmf-function-conform.

    python3 -m unittest discover -s tests/conformance -v

Set DMF_CONFORM_SKIP_HELM=1 to skip `helm lint --strict` where helm is absent.
"""

import os
import subprocess
import sys
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TOOL = os.path.join(ROOT, "bin", "dmf-function-conform")
FIXTURES = os.path.join(HERE, "fixtures")
CHART_DIRS = [os.path.join(ROOT, "charts", "nmos-crosspoint")]

with open(os.path.join(HERE, "cases.yaml"), encoding="utf-8") as fh:
    CASES = yaml.safe_load(fh)


def run_tool(dirs, args, extra=()):
    argv = [sys.executable, TOOL, *extra]
    if os.environ.get("DMF_CONFORM_SKIP_HELM"):
        argv.append("--skip-helm")
    resolved = []
    for a in args:
        # Treat anything that names an existing fixture path as a path.
        p = os.path.join(FIXTURES, a)
        resolved.append(p if os.path.exists(p) else a)
    argv += resolved + [os.path.join(FIXTURES, d) for d in dirs]
    return subprocess.run(argv, capture_output=True, text=True, check=False)


class Conformance(unittest.TestCase):
    def test_every_bad_case_names_an_existing_good_twin(self):
        good = {c["name"] for c in CASES["good"]}
        for case in CASES["bad"]:
            self.assertIn(case["twin"], good, case["name"])

    def test_at_least_six_distinct_bad_cases(self):
        self.assertGreaterEqual(len(CASES["bad"]), 6)

    def test_chart_only_accepts_the_real_chart_with_a_values_schema(self):
        argv = [sys.executable, TOOL, "--chart-only"]
        if os.environ.get("DMF_CONFORM_SKIP_HELM"):
            argv.append("--skip-helm")
        proc = subprocess.run(argv + CHART_DIRS, capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)


def _make(case, want_ok):
    def test(self):
        extra = case.get("args", [])
        proc = run_tool(case["dirs"], extra)
        if want_ok:
            self.assertEqual(proc.returncode, 0, proc.stderr)
        else:
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("VIOLATION", proc.stderr)
            self.assertIn(case["expect"], proc.stderr)
    return test


for _c in CASES["good"]:
    setattr(Conformance, "test_good_" + _c["name"].replace("-", "_"), _make(_c, True))
for _c in CASES["bad"]:
    setattr(Conformance, "test_bad_" + _c["name"].replace("-", "_"), _make(_c, False))

if __name__ == "__main__":
    unittest.main()
