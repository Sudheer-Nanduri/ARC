# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

import json
import unittest
from pathlib import Path

from arc.core.report_generator import generate_html_report, generate_json_report

_TMP = Path(__file__).resolve().parent.parent / "_tmp"


class TestReportGenerator(unittest.TestCase):
    def setUp(self):
        _TMP.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        for path in _TMP.glob("report_generator_*"):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def test_generate_json_report_includes_structured_metadata(self):
        results = [
            {
                "rule_id": "RULE_1",
                "element_id": "E1",
                "status": "FAIL",
                "message": "blocked",
                "severity": "critical",
                "category": "accessibility",
            },
            {
                "rule_id": "RULE_2",
                "element_id": "E2",
                "status": "PASS",
                "message": "ok",
                "severity": "major",
                "category": "fire_egress",
            },
        ]
        outpath = _TMP / "report_generator_report.json"
        generate_json_report(
            results,
            str(outpath),
            metadata={"model_name": "Demo.ifc", "mode": "pre_compliance"},
        )
        payload = json.loads(outpath.read_text(encoding="utf8"))

        self.assertEqual(payload["model_details"]["model_name"], "Demo.ifc")
        self.assertEqual(payload["metadata"]["mode"], "pre_compliance")
        self.assertEqual(payload["category_summaries"][0]["category"], "accessibility")
        self.assertEqual(payload["top_failed_rules"][0]["rule_id"], "RULE_1")
        self.assertEqual(payload["advanced_insights"]["spatial_conflict_count"], 0)

    def test_generate_html_report_contains_visual_sections(self):
        results = [
            {
                "rule_id": "door_clearance",
                "element_id": "door1",
                "status": "FAIL",
                "message": "blocked",
                "severity": "major",
                "category": "accessibility",
                "details": {
                    "blocking_elements": ["rail1"],
                    "measurement_method": "bbox_opening_plus_nearby_obstruction_proxy",
                },
            }
        ]
        outpath = _TMP / "report_generator_report.html"
        generate_html_report(
            results,
            str(outpath),
            metadata={"model_name": "Demo.ifc", "source": "Unit test"},
        )
        html = outpath.read_text(encoding="utf8")

        self.assertIn("ARC Compliance Report", html)
        self.assertIn("Flagship Spatial and Topology Findings", html)
        self.assertIn("Most Frequent Blockers", html)
        self.assertIn("door_clearance", html)


if __name__ == "__main__":
    unittest.main()
