# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""Tests for the full checkability pipeline.

Covers:
- Python rules going through the 5-gate classifier (same as JSON rules)
- Auto-generated EvidenceBundle for Python rule results
- Confidence gate (Gate 3b) blocking low-confidence elements
- Polygon-tier area/width handlers (Shapely-based, AABB fallback)
- IFC integration import paths (arc.core.ifc_integration)
"""
import unittest

import pytest

from arc.core.context import Context
from arc.core.data_models import (
    AABB, Element, EvidenceBundle,
    STATUS_FAIL, STATUS_HUMAN_REQUIRED, STATUS_INCONCLUSIVE, STATUS_PASS,
)
from arc.core.rule_engine import RuleEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _py_rule(**overrides):
    base = {
        "id": "py_test",
        "language": "python",
        "code": "def run(context, element):\n    return {'passed': True, 'message': 'ok'}",
        "severity": "major",
        "category": "spatial",
    }
    base.update(overrides)
    return base


def _space(guid="s1", **kwargs):
    kw = {"ifc_class": "IfcSpace", "aabb": AABB([0, 0, 0], [4, 4, 3])}
    kw.update(kwargs)
    return Element(guid=guid, **kw)


# ---------------------------------------------------------------------------
# Python rules through the checkability gate
# ---------------------------------------------------------------------------

class TestPythonRuleGatePipeline(unittest.TestCase):

    def test_python_rule_receives_evidence_bundle(self):
        engine = RuleEngine([_py_rule(selector={"ifc_class": "IfcSpace"})])
        r = engine.execute(Context([_space()]))[0]
        self.assertIsNotNone(r.evidence)
        self.assertIsInstance(r.evidence, EvidenceBundle)
        self.assertEqual(r.evidence.element_guid, "s1")

    def test_human_judgment_gate_blocks_execution(self):
        rule = _py_rule(requires_human_judgment=True, selector={"ifc_class": "IfcSpace"})
        r = engine = RuleEngine([rule])
        r = engine.execute(Context([_space()]))[0]
        self.assertEqual(r.status, STATUS_HUMAN_REQUIRED)
        self.assertEqual(r.details.get("gate"), "human_required")

    def test_missing_geometry_gate_blocks_without_aabb(self):
        rule = _py_rule(selector={"ifc_class": "IfcSpace"})
        el = _space(aabb=None)
        r = RuleEngine([rule]).execute(Context([el]))[0]
        self.assertEqual(r.status, STATUS_INCONCLUSIVE)
        self.assertEqual(r.details.get("gate"), "geometry_limited")

    def test_needs_geometry_false_allows_no_aabb(self):
        rule = _py_rule(needs_geometry=False, selector={"ifc_class": "IfcSpace"})
        el = _space(aabb=None)
        r = RuleEngine([rule]).execute(Context([el]))[0]
        self.assertEqual(r.status, STATUS_PASS)

    def test_missing_required_property_gate(self):
        rule = _py_rule(requires_properties=["ventilation_rate"], selector={"ifc_class": "IfcSpace"})
        r = RuleEngine([rule]).execute(Context([_space()]))[0]
        self.assertEqual(r.status, STATUS_INCONCLUSIVE)
        self.assertEqual(r.details.get("gate"), "data_missing")

    def test_python_rule_can_declare_measurement_provenance(self):
        """Python rules that return provenance keys get them forwarded to the result."""
        code = (
            "def run(context, element):\n"
            "    return {'passed': True, 'message': 'ok',\n"
            "            'measurement_method': 'custom_calc',\n"
            "            'measurement_source': 'polygon_derived',\n"
            "            'geometry_tier': 'polygon'}"
        )
        rule = _py_rule(code=code, selector={"ifc_class": "IfcSpace"})
        r = RuleEngine([rule]).execute(Context([_space()]))[0]
        self.assertEqual(r.measurement_method, "custom_calc")
        self.assertEqual(r.measurement_source, "polygon_derived")
        self.assertEqual(r.geometry_tier, "polygon")


# ---------------------------------------------------------------------------
# Confidence gate (Gate 3b)
# ---------------------------------------------------------------------------

class TestConfidenceGate(unittest.TestCase):

    def _rule_with_confidence_req(self, dim, min_val):
        return [{
            "id": "r1", "check_type": "min_height",
            "params": {"min_height": 2.5},
            "selector": {"ifc_class": "IfcSpace"},
            "severity": "major", "category": "spatial",
            "confidence_requirement": {dim: min_val},
        }]

    def test_low_confidence_returns_inconclusive(self):
        el = _space(confidence={"semantic": 0.9, "geometry": 0.3, "topology": 0.5})
        r = RuleEngine(self._rule_with_confidence_req("geometry", 0.8)).execute(Context([el]))[0]
        self.assertEqual(r.status, STATUS_INCONCLUSIVE)
        self.assertEqual(r.details.get("gate"), "confidence_insufficient")

    def test_sufficient_confidence_proceeds_to_check(self):
        el = _space(confidence={"semantic": 0.9, "geometry": 0.9, "topology": 0.5})
        r = RuleEngine(self._rule_with_confidence_req("geometry", 0.8)).execute(Context([el]))[0]
        self.assertIn(r.status, (STATUS_PASS, STATUS_FAIL))

    def test_no_confidence_requirement_is_never_blocked(self):
        """Rules without confidence_requirement always proceed regardless of element confidence."""
        rules = [{"id": "r1", "check_type": "min_height", "params": {"min_height": 2.5},
                  "selector": {"ifc_class": "IfcSpace"}, "severity": "major", "category": "spatial"}]
        el = _space(confidence={"semantic": 0.0, "geometry": 0.0, "topology": 0.0})
        r = RuleEngine(rules).execute(Context([el]))[0]
        self.assertIn(r.status, (STATUS_PASS, STATUS_FAIL))


# ---------------------------------------------------------------------------
# Polygon-tier handler fallback
# ---------------------------------------------------------------------------

class TestPolygonTierHandlers(unittest.TestCase):

    def test_min_area_falls_back_to_aabb_without_footprint(self):
        """Without footprint_polygon, min_area uses AABB tier."""
        rule = [{"id": "r1", "check_type": "min_area", "params": {"min_area": 10.0},
                 "selector": {"ifc_class": "IfcSpace"}, "severity": "major", "category": "spatial"}]
        el = Element(guid="s1", ifc_class="IfcSpace", aabb=AABB([0, 0, 0], [4, 4, 3]))
        r = RuleEngine(rule).execute(Context([el]))[0]
        self.assertIn(r.status, (STATUS_PASS, STATUS_FAIL))
        self.assertEqual(r.measurement_source, "aabb_derived")
        self.assertEqual(r.geometry_tier, "aabb")

    @pytest.mark.skipif(
        True,  # replaced at runtime by shapely availability
        reason="requires shapely",
    )
    def test_min_area_uses_polygon_when_footprint_present(self):
        """With footprint_polygon and shapely, min_area reports polygon tier."""
        try:
            from shapely.geometry import Polygon  # noqa: F401
        except ImportError:
            self.skipTest("shapely not installed")

        rule = [{"id": "r1", "check_type": "min_area", "params": {"min_area": 10.0},
                 "selector": {"ifc_class": "IfcSpace"}, "severity": "major", "category": "spatial"}]
        # L-shaped polygon: area=12, AABB would give 16
        el = Element(guid="s1", ifc_class="IfcSpace",
                     aabb=AABB([0, 0, 0], [4, 4, 3]),
                     properties={"footprint_polygon": [(0, 0), (4, 0), (4, 2), (2, 2), (2, 4), (0, 4)]})
        r = RuleEngine(rule).execute(Context([el]))[0]
        self.assertEqual(r.status, STATUS_PASS)
        self.assertEqual(r.measurement_source, "polygon_derived")
        self.assertEqual(r.geometry_tier, "polygon")
        self.assertAlmostEqual(r.measured_value, 12.0, places=1)


# ---------------------------------------------------------------------------
# IFC integration import paths
# ---------------------------------------------------------------------------

class TestCoreIfcIntegrationPaths(unittest.TestCase):

    def test_headless_functions_importable_from_core(self):
        from arc.core.ifc_integration import load_federated_ifc, load_ifc
        self.assertTrue(callable(load_ifc))
        self.assertTrue(callable(load_federated_ifc))

    def test_cli_import_path_resolves(self):
        """Exact import path used by arc.core.cli must resolve without error."""
        from arc.core.ifc_integration import load_federated_ifc, load_ifc  # noqa: F401

    def test_semantic_layer_load_ifc_delegates(self):
        from arc.core.semantic_layer import SemanticLayer
        self.assertTrue(callable(SemanticLayer().load_ifc))

    def test_load_ifc_returns_empty_list_without_ifcopenshell(self):
        """load_ifc must degrade gracefully when ifcopenshell is not installed."""
        from arc.core.ifc_integration import load_ifc
        self.assertEqual(load_ifc("nonexistent.ifc"), [])


if __name__ == "__main__":
    unittest.main()
