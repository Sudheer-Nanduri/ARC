# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for arc.core.rule_engine.

Covers: JSON check types, Python rule execution, sandbox enforcement,
selector scoping, dependency ordering, and new check types
(ratio, distance_to_nearest, count_nearby).
"""
import unittest

from arc.core.context import Context
from arc.core.data_models import AABB, Element
from arc.core.rule_engine import RuleEngine
from arc.core.rule_loader import load_python_rules, load_rules


class TestJsonCheckTypes(unittest.TestCase):

    def test_door_width_pass_and_fail(self):
        """min_width check correctly classifies a wide and a narrow door."""
        engine = RuleEngine.from_json_dir("arc/core/rules/json_rules")
        e_ok  = Element(guid="door_ok",  ifc_class="IfcDoor", aabb=AABB([0,0,0],[1.0,0.2,2.1]), properties={"Width": 1.0})
        e_bad = Element(guid="door_bad", ifc_class="IfcDoor", aabb=AABB([0,0,0],[0.75,0.2,2.1]), properties={"Width": 0.75})
        results = engine.execute(Context([e_ok, e_bad]))
        r = {r.element_id: r for r in results if r.rule_id == "NBC_ACC_04"}
        self.assertTrue(r["door_ok"].passed)
        self.assertFalse(r["door_bad"].passed)

    def test_ratio_pass_and_fail(self):
        """ratio check: WindowArea / floor_area >= min_ratio."""
        rule = {
            "id": "ratio_test", "check_type": "ratio",
            "selector": {"ifc_class": "IfcSpace"},
            "params": {"numerator": "WindowArea", "denominator": "area", "min_ratio": 0.1},
        }
        engine = RuleEngine([rule])
        e_pass = Element(guid="s1", ifc_class="IfcSpace", aabb=AABB([0,0,0],[5,3,3]), properties={"WindowArea": 2.0})
        e_fail = Element(guid="s2", ifc_class="IfcSpace", aabb=AABB([0,0,0],[5,3,3]), properties={"WindowArea": 0.5})
        by_id = {r.element_id: r for r in engine.execute(Context([e_pass, e_fail]))}
        self.assertTrue(by_id["s1"].passed)
        self.assertFalse(by_id["s2"].passed)

    def test_distance_to_nearest(self):
        """distance_to_nearest: space passes when a door is within max_distance."""
        rule = {
            "id": "dist_test", "check_type": "distance_to_nearest",
            "selector": {"ifc_class": "IfcSpace"},
            "params": {"target_class": "IfcDoor", "max_distance": 10.0},
        }
        space = Element(guid="s1", ifc_class="IfcSpace", aabb=AABB([0,0,0],[4,4,3]))
        door  = Element(guid="d1", ifc_class="IfcDoor",  aabb=AABB([5,1,0],[5.8,1.2,2.1]))
        results = engine = RuleEngine([rule])
        self.assertTrue(engine.execute(Context([space, door]))[0].passed)

    def test_count_nearby_fail_then_pass(self):
        """count_nearby: balcony fails without railing, passes once railing added."""
        rule = {
            "id": "count_test", "check_type": "count_nearby",
            "selector": {"ifc_class": "IfcSlab"},
            "params": {"target_class": "IfcRailing", "radius": 5.0, "min_count": 1},
        }
        balcony = Element(guid="b1", ifc_class="IfcSlab",    aabb=AABB([0,0,0],[3,1.5,0.2]))
        railing = Element(guid="r1", ifc_class="IfcRailing", aabb=AABB([0,1.3,0],[3,1.5,1.1]))
        engine = RuleEngine([rule])
        self.assertFalse(engine.execute(Context([balcony]))[0].passed)
        self.assertTrue(
            next(r for r in engine.execute(Context([balcony, railing])) if r.rule_id == "count_test").passed
        )


class TestPythonRules(unittest.TestCase):

    def test_sandbox_allows_execution(self):
        code = "def run(context, element):\n    return {'passed': element.ifc_class == 'Room', 'message': 'ok'}"
        engine = RuleEngine([{"id": "py1", "language": "python", "code": code}])
        e = Element(guid="r1", ifc_class="Room", aabb=AABB([0,0,0],[1,1,1]))
        self.assertTrue(engine.execute(Context([e]))[0].passed)

    def test_sandbox_blocks_imports(self):
        """Python rules containing `import` statements must be rejected."""
        bad = "import os\ndef run(context, element):\n    return {'passed': True}"
        engine = RuleEngine([{"id": "bad", "language": "python", "code": bad}])
        e = Element(guid="x1", ifc_class="Room", aabb=AABB([0,0,0],[1,1,1]))
        r = engine.execute(Context([e]))[0]
        self.assertFalse(r.passed)
        self.assertIn("Imports and global/nonlocal are not allowed", r.message)

    def test_metadata_loaded_from_python_rule_files(self):
        rules = {r["id"]: r for r in load_python_rules("arc/core/rules/python_rules")}
        self.assertEqual(rules["corridor_width"]["category"], "spatial")
        self.assertEqual(rules["nbc_vent_01"]["category"], "ventilation")

    def test_selector_limits_evaluation_scope(self):
        """The corridor_width rule selector picks IfcSpace tagged LongName=LOBBY only."""
        rules = [r for r in load_python_rules("arc/core/rules/python_rules") if r["id"] == "corridor_width"]
        engine = RuleEngine(rules)
        ctx = Context([
            Element(guid="lobby1", ifc_class="IfcSpace", aabb=AABB([0,0,0],[3.5,3.5,2.8]), properties={"LongName": "LOBBY"}),
            Element(guid="room1", ifc_class="IfcSpace", aabb=AABB([0,0,0],[4.0,3.5,2.8]), properties={"LongName": "BEDROOM", "area": 14.0}),
        ])
        results = engine.execute(ctx)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].element_id, "lobby1")

    def test_ventilation_rule_passes_for_habitable_room(self):
        rules = [r for r in load_python_rules("arc/core/rules/python_rules") if r["id"] == "nbc_vent_01"]
        engine = RuleEngine(rules)
        ctx = Context([Element(guid="room1", ifc_class="IfcSpace", aabb=AABB([0,0,0],[4,3.5,2.8]), properties={"SpaceType": "Habitable"})])
        r = engine.execute(ctx)[0]
        self.assertEqual(r.status, "PASS")
        self.assertIn("Ceiling height", r.message)

    def test_accessible_door_reports_blockers(self):
        # Evidence contract: FAIL requires positive obstruction evidence
        # on both pull/push assignments; the rail straddles the door so it
        # blocks both sides. The door's own width never drives a FAIL.
        rules = [r for r in load_python_rules("arc/core/rules/python_rules") if r["id"] == "nbc_acc_01"]
        engine = RuleEngine(rules)
        ctx = Context([
            Element(guid="door1", ifc_class="IfcDoor",    aabb=AABB([0,0,0],[1.0,0.2,2.1])),
            Element(guid="rail1", ifc_class="IfcRailing", aabb=AABB([0.6,-0.2,0],[1.2,0.4,1.1])),
        ])
        r = engine.execute(ctx)[0]
        self.assertFalse(r.passed)
        self.assertEqual(r.details["blocking_elements"], ["rail1"])
        self.assertEqual(r.details["measurement_method"], "approach_zone_aabb")
        self.assertTrue(any("swing direction unknown" in a for a in r.details["assumptions"]))

    def test_egress_rule_uses_path_length(self):
        rules = [r for r in load_python_rules("arc/core/rules/python_rules") if r["id"] == "dcr_fire_03"]
        engine = RuleEngine(rules)
        ctx = Context([
            Element(guid="space1", ifc_class="IfcSpace", aabb=AABB([0,0,0],[4,4,3])),
            Element(guid="door1",  ifc_class="IfcDoor",  aabb=AABB([3.5,1.5,0],[4.5,2.5,2.2])),
            Element(guid="exit1",  ifc_class="IfcDoor",  aabb=AABB([4.3,1.5,0],[5.3,2.5,2.2]), properties={"FireExit": True}),
        ])
        r = engine.execute(ctx)[0]
        self.assertEqual(r.status, "PASS")
        self.assertGreater(r.details["path_length_m"], 0.0)
        self.assertEqual(r.details["path"], ["space1", "door1", "exit1"])


class TestDependencyOrdering(unittest.TestCase):

    def test_toposort_respects_depends_on(self):
        rules = [{"id": "a"}, {"id": "b", "depends_on": ["a"]}]
        engine = RuleEngine(rules)
        ordered = engine._toposort()
        self.assertEqual([r["id"] for r in ordered], ["a", "b"])


class TestRuleLoader(unittest.TestCase):

    def test_combined_loader_includes_expected_rules(self):
        rules = {r["id"].lower(): r for r in load_rules("arc/core/rules")}
        self.assertIn("nbc_acc_04", rules)
        self.assertIn("nbc_acc_01", rules)

    def test_combined_loader_excludes_deprecated_rules(self):
        rules = {r["id"].lower(): r for r in load_rules("arc/core/rules")}
        self.assertNotIn("dcr_park_legacy", rules)
        self.assertNotIn("parking_min_area_py", rules)
        self.assertNotIn("dcr_park_01", rules)


if __name__ == "__main__":
    unittest.main()
