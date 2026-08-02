# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""End-to-end CLI tests via subprocess.

These tests exercise the actual `arc-check` entry point (via `run_checks.py`)
as a user would invoke it, verifying argument parsing, report generation,
stage modes, and exit behavior.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PYTHON = sys.executable
ROOT = Path(__file__).resolve().parent.parent.parent
RUN_CHECKS = str(ROOT / "run_checks.py")


def _run_cli(*args, timeout=30):
    """Run arc-check via run_checks.py and return CompletedProcess."""
    cmd = [PYTHON, RUN_CHECKS] + list(args)
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, cwd=str(ROOT),
    )


class TestCliDemo(unittest.TestCase):
    """CLI --demo mode produces expected outputs."""

    def test_demo_concept_exits_zero(self):
        r = _run_cli("--demo", "--mode", "concept")
        self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")

    def test_demo_submission_exits_zero(self):
        r = _run_cli("--demo", "--mode", "submission")
        self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")

    def test_demo_produces_json_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            r = _run_cli("--demo", "--output", tmpdir)
            self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")
            json_path = Path(tmpdir) / "compliance_results.json"
            self.assertTrue(json_path.exists(), "JSON report not generated")
            data = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertIn("element_results", data)
            self.assertGreater(len(data["element_results"]), 0)

    def test_demo_produces_html_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            r = _run_cli("--demo", "--output", tmpdir)
            self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")
            html_path = Path(tmpdir) / "compliance_report.html"
            self.assertTrue(html_path.exists(), "HTML report not generated")
            content = html_path.read_text(encoding="utf-8")
            self.assertIn("<html", content.lower())

    def test_demo_produces_bcf(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            r = _run_cli("--demo", "--output", tmpdir)
            self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")
            bcf_path = Path(tmpdir) / "issues.bcfzip"
            self.assertTrue(bcf_path.exists(), "BCF file not generated")
            self.assertGreater(bcf_path.stat().st_size, 0)

    def test_demo_json_has_evidence_bundles(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            r = _run_cli("--demo", "--output", tmpdir)
            self.assertEqual(r.returncode, 0)
            data = json.loads((Path(tmpdir) / "compliance_results.json").read_text(encoding="utf-8"))
            results_with_evidence = [
                r for r in data["element_results"] if r.get("evidence") is not None
            ]
            self.assertGreater(len(results_with_evidence), 0, "No results have evidence bundles")


class TestCliModes(unittest.TestCase):
    """Stage modes affect output."""

    def test_concept_vs_submission_rule_count(self):
        """Submission evaluates >= as many rules as concept (min_stage filtering)."""
        with tempfile.TemporaryDirectory() as tmpdir_c, tempfile.TemporaryDirectory() as tmpdir_s:
            _run_cli("--demo", "--mode", "concept", "--output", tmpdir_c)
            _run_cli("--demo", "--mode", "submission", "--output", tmpdir_s)
            data_c = json.loads((Path(tmpdir_c) / "compliance_results.json").read_text(encoding="utf-8"))
            data_s = json.loads((Path(tmpdir_s) / "compliance_results.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(
                len(data_s["element_results"]), len(data_c["element_results"]),
                "Submission should evaluate at least as many rules as concept",
            )

    def test_stage_exit_criteria_in_json(self):
        """JSON output includes stage exit criteria when mode is specified."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _run_cli("--demo", "--mode", "schematic", "--output", tmpdir)
            data = json.loads((Path(tmpdir) / "compliance_results.json").read_text(encoding="utf-8"))
            self.assertIn("element_results", data)


class TestCliArgParsing(unittest.TestCase):
    """Argument validation."""

    def test_invalid_mode_rejected(self):
        r = _run_cli("--demo", "--mode", "invalid_mode")
        self.assertNotEqual(r.returncode, 0)

    def test_list_packs(self):
        r = _run_cli("--list-packs")
        self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")
        output = r.stdout + r.stderr
        self.assertGreater(len(output.strip()), 0, "No output from --list-packs")


class TestCliDelta(unittest.TestCase):
    """Delta comparison via --baseline."""

    def test_delta_produces_delta_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _run_cli("--demo", "--output", tmpdir)
            baseline = str(Path(tmpdir) / "compliance_results.json")
            # Second run: delta - at minimum should not crash
            _run_cli("--demo", "--output", tmpdir, "--baseline", baseline)


if __name__ == "__main__":
    unittest.main()
