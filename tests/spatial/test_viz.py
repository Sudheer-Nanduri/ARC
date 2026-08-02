# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: GPL-3.0-or-later

"""Integration tests for the ARC visualization overhaul.

Tests cover:
- New JSON check types (clearance_zone, turning_circle)
- VizContext helper for Python rule authors
- Backward-compatible rule execution
- Viz descriptor generation
- Headless metadata output
"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arc.core.data_models import Element, AABB
from arc.core.context import Context
from arc.core.rule_engine import RuleEngine
from arc.spatial.visualizer import VizContext


# -- Fixtures -----------------------------------------------------------------

@pytest.fixture
def wide_door():
    """Door with 1.0m clear opening (AABB max horizontal = 1.0)."""
    return Element(guid="wide-door", ifc_class="IfcDoor",
                   aabb=AABB(min=[0, 0, 0], max=[1.0, 0.2, 2.1]))


@pytest.fixture
def narrow_door():
    """Door with 0.7m clear opening — should fail 0.9m min width."""
    return Element(guid="narrow-door", ifc_class="IfcDoor",
                   aabb=AABB(min=[0, 0, 0], max=[0.7, 0.15, 2.1]))


@pytest.fixture
def small_space():
    """Space too small for a 1.5m turning circle."""
    return Element(guid="small-space", ifc_class="IfcSpace",
                   aabb=AABB(min=[0, 0, 0], max=[1.2, 1.2, 3.0]))


@pytest.fixture
def big_space():
    """Space large enough for a 1.5m turning circle."""
    return Element(guid="big-space", ifc_class="IfcSpace",
                   aabb=AABB(min=[0, 0, 0], max=[2.0, 2.0, 3.0]))


@pytest.fixture
def door_with_column():
    """Door with a column in its clearance zone."""
    door = Element(guid="d1", ifc_class="IfcDoor",
                   aabb=AABB(min=[0, 0, 0], max=[1, 0.2, 2.1]))
    col = Element(guid="c1", ifc_class="IfcColumn",
                  aabb=AABB(min=[0.5, 0.3, 0], max=[0.8, 0.6, 3.0]))
    return door, col


# -- Legacy check types still work --------------------------------------------

class TestLegacyCheckTypes:
    def test_min_width_fail(self, narrow_door):
        ctx = Context([narrow_door])
        engine = RuleEngine([{
            "id": "test_width", "check_type": "min_width",
            "params": {"min_width": 0.9},
            "selector": {"ifc_class": "IfcDoor"},
            "severity": "critical", "category": "accessibility",
        }])
        results = engine.execute(ctx)
        assert len(results) == 1
        assert results[0].status == "FAIL"
        assert results[0].measured_value < 0.9

    def test_min_width_pass(self):
        door = Element(guid="big-door", ifc_class="IfcDoor",
                       aabb=AABB(min=[0, 0, 0], max=[1.2, 1.2, 2.1]))
        ctx = Context([door])
        engine = RuleEngine([{
            "id": "test_width", "check_type": "min_width",
            "params": {"min_width": 0.9},
            "selector": {"ifc_class": "IfcDoor"},
            "severity": "critical", "category": "accessibility",
        }])
        results = engine.execute(ctx)
        assert results[0].status == "PASS"
        assert results[0].measured_value >= 0.9

    def test_min_height(self):
        stair = Element(guid="s1", ifc_class="IfcStair",
                        aabb=AABB(min=[0, 0, 0], max=[2, 1, 2.5]))
        ctx = Context([stair])
        engine = RuleEngine([{
            "id": "test_ht", "check_type": "min_height",
            "params": {"min_height": 2.2},
            "selector": {"ifc_class": "IfcStair"},
            "severity": "critical", "category": "fire_egress",
        }])
        results = engine.execute(ctx)
        assert results[0].status == "PASS"
        assert results[0].measured_value == pytest.approx(2.5, abs=0.01)


# -- New: turning_circle check type ------------------------------------------

class TestTurningCircle:
    def test_fail_small_space(self, small_space):
        ctx = Context([small_space])
        engine = RuleEngine([{
            "id": "test_turn", "check_type": "turning_circle",
            "params": {"diameter_m": 1.5},
            "selector": {"ifc_class": "IfcSpace"},
            "severity": "major", "category": "accessibility",
        }])
        results = engine.execute(ctx)
        r = results[0]
        assert r.status == "FAIL"
        assert r.measured_value == pytest.approx(1.2, abs=0.01)
        assert r.expected_value == pytest.approx(1.5)
        # Must have viz descriptors
        viz = r.details.get("viz", [])
        assert len(viz) >= 1
        assert viz[0]["type"] == "turning_circle"
        assert viz[0]["radius"] == pytest.approx(0.75)

    def test_pass_big_space(self, big_space):
        ctx = Context([big_space])
        engine = RuleEngine([{
            "id": "test_turn", "check_type": "turning_circle",
            "params": {"diameter_m": 1.5},
            "selector": {"ifc_class": "IfcSpace"},
            "severity": "major", "category": "accessibility",
        }])
        results = engine.execute(ctx)
        assert results[0].status == "PASS"
        viz = results[0].details.get("viz", [])
        assert len(viz) >= 1
        assert viz[0]["status"] == "pass"

    def test_has_annotation_on_fail(self, small_space):
        ctx = Context([small_space])
        results = RuleEngine([{
            "id": "t", "check_type": "turning_circle",
            "params": {"diameter_m": 1.5},
            "selector": {"ifc_class": "IfcSpace"},
            "severity": "major", "category": "accessibility",
        }]).execute(ctx)
        viz = results[0].details.get("viz", [])
        types = [v["type"] for v in viz]
        assert "annotation" in types


# -- New: clearance_zone check type ------------------------------------------

class TestClearanceZone:
    def test_fail_with_blocker(self, door_with_column):
        door, col = door_with_column
        ctx = Context([door, col])
        engine = RuleEngine([{
            "id": "test_cz", "check_type": "clearance_zone",
            "params": {"padding_m": 0.5},
            "selector": {"ifc_class": "IfcDoor"},
            "severity": "major", "category": "accessibility",
        }])
        results = engine.execute(ctx)
        r = results[0]
        assert r.status == "FAIL"
        assert "c1" in r.details.get("blocking_elements", [])
        viz = r.details.get("viz", [])
        assert any(v["type"] == "clearance_zone" for v in viz)

    def test_pass_no_blocker(self):
        door = Element(guid="d2", ifc_class="IfcDoor",
                       aabb=AABB(min=[0, 0, 0], max=[1, 0.2, 2.1]))
        ctx = Context([door])
        engine = RuleEngine([{
            "id": "test_cz", "check_type": "clearance_zone",
            "params": {"padding_m": 0.5},
            "selector": {"ifc_class": "IfcDoor"},
            "severity": "major", "category": "accessibility",
        }])
        results = engine.execute(ctx)
        assert results[0].status == "PASS"

    def test_ignore_classes(self, door_with_column):
        """Column should be detected; wall should be ignored."""
        door, col = door_with_column
        wall = Element(guid="w1", ifc_class="IfcWall",
                       aabb=AABB(min=[-0.1, -0.1, 0], max=[1.1, 0.0, 3.0]))
        ctx = Context([door, col, wall])
        results = RuleEngine([{
            "id": "test_cz", "check_type": "clearance_zone",
            "params": {"padding_m": 0.5},
            "selector": {"ifc_class": "IfcDoor"},
            "severity": "major", "category": "accessibility",
        }]).execute(ctx)
        blockers = results[0].details.get("blocking_elements", [])
        assert "c1" in blockers
        assert "w1" not in blockers


# -- VizContext helper -------------------------------------------------------

class TestVizContext:
    def test_all_methods(self):
        viz = VizContext()
        viz.clearance_zone({"min": [0, 0, 0], "max": [2, 2, 0]}, label="Zone")
        viz.turning_circle([1, 1, 0], radius=0.75, label="Turn")
        viz.probe([1, 1, 0], radius=0.375, height=1.2, label="Wheelchair")
        viz.dimension([0, 0, 0], [1, 0, 0], measured=0.85, required=0.9, label="Width")
        viz.annotate([1, 1, 1], "Test text")
        viz.sweep_path([[0, 0, 0], [1, 0, 0], [2, 0, 0]], probe_radius=0.5)
        viz.highlight({"min": [0, 0, 0], "max": [1, 1, 1]})
        viz.bbox({"min": [0, 0, 0], "max": [1, 1, 1]}, color="blocker")
        viz.marker([0.5, 0.5, 0.5], label="Point")
        items = viz.to_list()
        assert len(items) == 9

    def test_chaining(self):
        """VizContext methods should return self for fluent chaining."""
        items = (
            VizContext()
            .clearance_zone({"min": [0, 0, 0], "max": [1, 1, 0]})
            .annotate([0, 0, 0], "Hi")
            .to_list()
        )
        assert len(items) == 2

    def test_types_correct(self):
        """Each method should produce the correct type field."""
        viz = VizContext()
        viz.clearance_zone({"min": [0, 0, 0], "max": [1, 1, 0]})
        viz.turning_circle([0, 0, 0])
        viz.probe([0, 0, 0])
        viz.dimension([0, 0, 0], [1, 0, 0])
        viz.annotate([0, 0, 0], "x")
        viz.sweep_path([[0, 0, 0], [1, 0, 0]])
        expected = ["clearance_zone", "turning_circle", "probe_cylinder",
                    "dimension_line", "annotation", "sweep_path"]
        actual = [d["type"] for d in viz.to_list()]
        assert actual == expected


# -- Python rule loading -----------------------------------------------------

class TestPythonRules:
    def test_all_rules_load(self):
        from arc.core.rule_loader import load_python_rules
        rules_dir = str(Path(__file__).resolve().parent.parent.parent / "arc" / "core" / "rules" / "python_rules")
        py_rules = load_python_rules(rules_dir)
        assert len(py_rules) >= 6
        ids = {r["id"] for r in py_rules}
        assert "nbc_acc_01" in ids
        assert "nbc_acc_02" in ids
        assert "dcr_fire_01" in ids
        assert "dcr_fire_03" in ids
        assert "corridor_width" in ids

    def test_rules_produce_viz(self):
        """Execute nbc_acc_01 (wheelchair approach) and verify viz descriptors."""
        door = Element(guid="d1", ifc_class="IfcDoor",
                       aabb=AABB(min=[0, 0, 0], max=[0.8, 0.15, 2.1]))
        ctx = Context([door])
        from arc.core.rule_loader import load_python_rules
        rules_dir = str(Path(__file__).resolve().parent.parent.parent / "arc" / "core" / "rules" / "python_rules")
        py_rules = load_python_rules(rules_dir)
        acc_rule = [r for r in py_rules if r["id"] == "nbc_acc_01"]
        assert len(acc_rule) == 1
        engine = RuleEngine(acc_rule)
        results = engine.execute(ctx)
        assert len(results) >= 1
        for r in results:
            if r.status == "FAIL":
                viz = r.details.get("viz", [])
                assert len(viz) >= 1, "nbc_acc_01 FAIL should return viz descriptors"


# -- Headless metadata -------------------------------------------------------

class TestHeadlessMetadata:
    def test_write_metadata(self):
        from arc.spatial.visualizer import create_volume_metadata
        with tempfile.TemporaryDirectory() as tmpdir:
            result_dict = {
                "rule_id": "test_rule",
                "element_id": "e1",
                "status": "FAIL",
                "passed": False,
            }
            path = create_volume_metadata(result_dict, out_dir=tmpdir)
            assert path.exists()
            data = json.loads(path.read_text())
            assert data["rule_id"] == "test_rule"
            assert data["element_id"] == "e1"

    def test_show_compliance_volumes_headless(self):
        """In headless mode (no bpy), should write JSON metadata only."""
        from arc.spatial.visualizer import show_compliance_volumes
        with tempfile.TemporaryDirectory() as tmpdir:
            results = [
                {"rule_id": "r1", "element_id": "e1", "status": "FAIL", "passed": False},
                {"rule_id": "r2", "element_id": "e2", "status": "PASS", "passed": True},
            ]
            paths = show_compliance_volumes(results, out_dir=tmpdir)
            assert len(paths) == 1  # Only FAIL results get metadata


# -- Color palette -----------------------------------------------------------

class TestColorPalette:
    def test_all_semantic_colors_defined(self):
        from arc.spatial.visualizer import COLORS
        expected = {"fail", "blocker", "clearance", "probe", "path",
                    "marker", "dimension", "annotation", "link",
                    "highlight", "pass"}
        assert expected.issubset(set(COLORS.keys()))

    def test_colors_are_rgba_tuples(self):
        from arc.spatial.visualizer import COLORS
        for key, rgba in COLORS.items():
            assert len(rgba) == 4, f"Color {key} should be (R, G, B, A)"
            assert all(0.0 <= v <= 1.0 for v in rgba), f"Color {key} values out of range"
