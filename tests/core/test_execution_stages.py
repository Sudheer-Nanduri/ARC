# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""Tests for the 3-stage execution system (concept / schematic / submission).

Covers: stage constants, min_stage filtering, exit criteria evaluation,
and the stage-demoted advisory messaging for concept-stage missing properties.
"""
import unittest

from arc.core.context import Context
from arc.core.data_models import (
    AABB, DEFAULT_STAGE_CRITERIA, Element, RuleResult,
    STAGE_CONCEPT, STAGE_ORDER, STAGE_SCHEMATIC, STAGE_SUBMISSION,
    STATUS_FAIL, STATUS_INCONCLUSIVE, STATUS_PASS, VALID_STAGES,
    StageExitCriteria,
)
from arc.core.rule_engine import RuleEngine


class TestStageConstants(unittest.TestCase):

    def test_valid_stages_are_the_three_names(self):
        self.assertEqual(VALID_STAGES, {"concept", "schematic", "submission"})

    def test_stage_order_is_strictly_increasing(self):
        self.assertLess(STAGE_ORDER[STAGE_CONCEPT], STAGE_ORDER[STAGE_SCHEMATIC])
        self.assertLess(STAGE_ORDER[STAGE_SCHEMATIC], STAGE_ORDER[STAGE_SUBMISSION])

    def test_default_criteria_exist_for_every_stage(self):
        for stage in VALID_STAGES:
            self.assertIn(stage, DEFAULT_STAGE_CRITERIA)
            self.assertIsInstance(DEFAULT_STAGE_CRITERIA[stage], StageExitCriteria)
            self.assertEqual(DEFAULT_STAGE_CRITERIA[stage].stage_name, stage)


class TestBackwardCompat(unittest.TestCase):

    def test_engine_without_stage_runs_normally(self):
        """Engine with no execution_stage must behave exactly as before."""
        rules = [{"id": "r1", "check_type": "min_height", "params": {"min_height": 2.5},
                  "selector": {"ifc_class": "IfcSpace"}, "severity": "major", "category": "spatial"}]
        el = Element(guid="s1", ifc_class="IfcSpace", aabb=AABB([0, 0, 0], [4, 4, 3]))
        engine = RuleEngine(rules)
        results = engine.execute(Context([el]))
        self.assertGreaterEqual(len(results), 1)
        self.assertIsNone(engine.stage_exit_result)


class TestMinStageFiltering(unittest.TestCase):

    def _rules(self):
        return [
            {"id": "r_concept",    "min_stage": "concept",    "check_type": "min_height",
             "params": {"min_height": 2.5}, "selector": {"ifc_class": "IfcSpace"},
             "severity": "major", "category": "spatial"},
            {"id": "r_schematic",  "min_stage": "schematic",  "check_type": "min_height",
             "params": {"min_height": 2.5}, "selector": {"ifc_class": "IfcSpace"},
             "severity": "major", "category": "spatial"},
            {"id": "r_submission", "min_stage": "submission",  "check_type": "min_height",
             "params": {"min_height": 2.5}, "selector": {"ifc_class": "IfcSpace"},
             "severity": "major", "category": "spatial"},
        ]

    def _ctx(self):
        return Context([Element(guid="s1", ifc_class="IfcSpace", aabb=AABB([0, 0, 0], [4, 4, 3]))])

    def test_concept_only_runs_concept_rules(self):
        ids = {r.rule_id for r in RuleEngine(self._rules(), execution_stage="concept").execute(self._ctx())}
        self.assertIn("r_concept", ids)
        self.assertNotIn("r_schematic", ids)
        self.assertNotIn("r_submission", ids)

    def test_schematic_runs_concept_and_schematic(self):
        ids = {r.rule_id for r in RuleEngine(self._rules(), execution_stage="schematic").execute(self._ctx())}
        self.assertIn("r_concept", ids)
        self.assertIn("r_schematic", ids)
        self.assertNotIn("r_submission", ids)

    def test_submission_runs_all_rules(self):
        ids = {r.rule_id for r in RuleEngine(self._rules(), execution_stage="submission").execute(self._ctx())}
        self.assertIn("r_concept", ids)
        self.assertIn("r_schematic", ids)
        self.assertIn("r_submission", ids)

    def test_rule_without_min_stage_runs_at_every_stage(self):
        rules = [{"id": "r_any", "check_type": "min_height", "params": {"min_height": 2.5},
                  "selector": {"ifc_class": "IfcSpace"}, "severity": "major", "category": "spatial"}]
        ctx = self._ctx()
        for stage in ["concept", "schematic", "submission"]:
            ids = {r.rule_id for r in RuleEngine(rules, execution_stage=stage).execute(ctx)}
            self.assertIn("r_any", ids, f"r_any must run at {stage}")


class TestStageExitCriteria(unittest.TestCase):

    def test_concept_criteria_are_lenient(self):
        """1/3 checkable = 33% >= 30% minimum → concept criteria met."""
        results = [
            RuleResult(rule_id="r1", status=STATUS_PASS),
            RuleResult(rule_id="r2", status=STATUS_INCONCLUSIVE),
            RuleResult(rule_id="r3", status=STATUS_INCONCLUSIVE),
        ]
        ev = DEFAULT_STAGE_CRITERIA["concept"].evaluate(results)
        self.assertTrue(ev["met"])

    def test_submission_criteria_are_strict(self):
        """50% checkable with a critical failure → submission criteria not met."""
        results = [
            RuleResult(rule_id="r1", status=STATUS_PASS),
            RuleResult(rule_id="r2", status=STATUS_FAIL, severity="critical"),
            RuleResult(rule_id="r3", status=STATUS_INCONCLUSIVE),
            RuleResult(rule_id="r4", status=STATUS_INCONCLUSIVE),
        ]
        ev = DEFAULT_STAGE_CRITERIA["submission"].evaluate(results)
        self.assertFalse(ev["met"])
        self.assertFalse(ev["checkability_met"])
        self.assertFalse(ev["critical_met"])

    def test_engine_populates_stage_exit_result(self):
        rules = [{"id": "r1", "check_type": "min_height", "params": {"min_height": 2.5},
                  "selector": {"ifc_class": "IfcSpace"}, "severity": "major", "category": "spatial"}]
        engine = RuleEngine(rules, execution_stage="concept")
        engine.execute(Context([Element(guid="s1", ifc_class="IfcSpace", aabb=AABB([0, 0, 0], [4, 4, 3]))]))
        self.assertIsNotNone(engine.stage_exit_result)
        self.assertIn("met", engine.stage_exit_result)
        self.assertIn("checkability_pct", engine.stage_exit_result)


class TestStageDemotedMessaging(unittest.TestCase):

    def _rules(self):
        return [{"id": "r1", "check_type": "min_height", "params": {"min_height": 2.5},
                 "requires_properties": ["height_exact"],
                 "selector": {"ifc_class": "IfcSpace"}, "severity": "major", "category": "spatial"}]

    def _ctx(self):
        return Context([Element(guid="s1", ifc_class="IfcSpace", aabb=AABB([0, 0, 0], [4, 4, 3]))])

    def test_concept_adds_stage_demoted_marker(self):
        r = RuleEngine(self._rules(), execution_stage="concept").execute(self._ctx())[0]
        self.assertEqual(r.status, STATUS_INCONCLUSIVE)
        self.assertTrue(r.details.get("stage_demoted"))
        self.assertIn("advisory", r.message.lower())

    def test_submission_does_not_add_stage_demoted_marker(self):
        r = RuleEngine(self._rules(), execution_stage="submission").execute(self._ctx())[0]
        self.assertEqual(r.status, STATUS_INCONCLUSIVE)
        self.assertIsNone(r.details.get("stage_demoted"))


if __name__ == "__main__":
    unittest.main()
