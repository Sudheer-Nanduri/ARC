# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for arc.core.data_models.

Covers: AABB, Element construction, RuleResult serialisation.
"""
import unittest

from arc.core.data_models import AABB, Element, RuleResult


class TestDataModels(unittest.TestCase):

    def test_element_construction(self):
        aabb = AABB([0, 0, 0], [1, 1, 1])
        e = Element(guid="e1", ifc_class="IfcWall", aabb=aabb)
        self.assertEqual(e.guid, "e1")
        self.assertEqual(e.ifc_class, "IfcWall")

    def test_rule_result_pass(self):
        rr = RuleResult(rule_id="r1", element_id="e1", passed=True, message="ok")
        self.assertTrue(rr.passed)

    def test_rule_result_serializes_aabb(self):
        rr = RuleResult(
            rule_id="r1",
            element_id="e1",
            passed=False,
            aabb={"min": [0, 0, 0], "max": [1, 1, 1]},
        )
        payload = rr.to_dict()
        self.assertEqual(payload["aabb"]["max"], [1, 1, 1])


if __name__ == "__main__":
    unittest.main()
