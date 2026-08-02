# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: GPL-3.0-or-later

import unittest

from arc.spatial.operators import _extract_results_and_metadata


class TestReportLoading(unittest.TestCase):
    def test_extracts_results_from_aggregate_report(self):
        payload = {
            "metadata": {"model_path": "model.ifc"},
            "element_results": [
                {"rule_id": "r1", "status": "PASS"},
                {"rule_id": "r2", "status": "FAIL"},
            ],
        }
        results, metadata = _extract_results_and_metadata(payload)
        self.assertEqual(len(results), 2)
        self.assertEqual(metadata["model_path"], "model.ifc")

    def test_extracts_single_result_payload(self):
        payload = {"rule_id": "r1", "status": "FAIL", "message": "x"}
        results, metadata = _extract_results_and_metadata(payload)
        self.assertEqual(len(results), 1)
        self.assertEqual(metadata, {})


if __name__ == "__main__":
    unittest.main()
