# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for the public evidence and waiver contracts.

The approach-clearance rule requires positive obstruction evidence for FAIL,
evidence and waiver identity share one canonical rule version, the CLI accepts
waiver records, and property-sourced measurements are labelled ``reported``.
"""
import json
import subprocess
import sys
from pathlib import Path


from arc.core.context import Context
from arc.core.data_models import AABB, Element, WaiverRecord
from arc.core.rule_engine import RuleEngine, _canonical_rule_version
from arc.core.rule_loader import load_rules

RULES_DIR = str(Path(__file__).resolve().parents[2] / "arc" / "core" / "rules")


def _door(guid="door1", x=0.0, y=0.0, w=0.9, t=0.2):
    return Element(
        guid=guid, ifc_class="IfcDoor",
        aabb=AABB(min=[x, y, 0.0], max=[x + t, y + w, 2.1]),
    )


def _column(guid, x, y, size=0.4):
    return Element(
        guid=guid, ifc_class="IfcColumn",
        aabb=AABB(min=[x, y, 0.0], max=[x + size, y + size, 3.0]),
    )


def _run_acc01(elements):
    rules = [r for r in load_rules(RULES_DIR) if r["id"] == "nbc_acc_01"]
    assert rules, "nbc_acc_01 must be present in the bundled pack"
    engine = RuleEngine(rules, execution_stage="submission")
    ctx = Context(elements, project_id="TEST")
    return [r for r in engine.execute(ctx) if r.element_id == "door1"]


class TestApproachClearance:
    def test_normal_door_with_free_zones_passes(self):
        # A standard 0.9 m door with no obstruction evidence must not fail.
        results = _run_acc01([_door()])
        assert len(results) == 1
        assert results[0].status == "PASS"

    def test_obstruction_both_sides_fails(self):
        door = _door()
        # Columns intruding into the zones on both sides of the thin (x) axis
        blocker_a = _column("colA", x=0.5, y=0.2)   # beyond max-x face
        blocker_b = _column("colB", x=-0.9, y=0.2)  # beyond min-x face
        results = _run_acc01([door, blocker_a, blocker_b])
        assert results[0].status == "FAIL"
        assert set(results[0].details["blocking_elements"]) == {"colA", "colB"}

    def test_obstruction_one_side_routes_to_reviewer(self):
        door = _door()
        # Blocker only inside the far (pull-depth-only) band on one side:
        # x in [1.4, 1.8] is inside the 1.5 m pull zone but outside the 1.2 m
        # push zone -> blocked under one assignment only.
        blocker = _column("colA", x=1.45, y=0.2)
        results = _run_acc01([door, blocker])
        assert results[0].status == "HUMAN_REQUIRED"
        assert results[0].human_reason == "irregular_geometry"

    def test_square_footprint_routes_to_reviewer(self):
        square_door = Element(
            guid="door1", ifc_class="IfcDoor",
            aabb=AABB(min=[0, 0, 0], max=[1.0, 0.9, 2.1]),
        )
        results = _run_acc01([square_door])
        assert results[0].status == "HUMAN_REQUIRED"

    def test_host_wall_is_not_an_obstruction(self):
        door = _door()
        wall = Element(
            guid="wall1", ifc_class="IfcWall",
            aabb=AABB(min=[-0.05, -2.0, 0.0], max=[0.25, 3.0, 3.0]),
        )
        results = _run_acc01([door, wall])
        assert results[0].status == "PASS"


class TestCanonicalVersion:
    def test_precedence(self):
        assert _canonical_rule_version({"rule_version": "2.1.0", "version": "9"}) == "2.1.0"
        assert _canonical_rule_version({"version": "3.0"}) == "3.0"
        assert _canonical_rule_version({}) == "1.0"

    def test_evidence_and_waiver_agree(self):
        rules = load_rules(RULES_DIR)
        rule = next(r for r in rules if r["id"] == "NBC_SAFE_01")
        version = _canonical_rule_version(rule)

        rail = Element(
            guid="rail1", ifc_class="IfcRailing",
            aabb=AABB(min=[0, 0, 0], max=[2.0, 0.1, 0.9]),
            properties={"Height": 0.9},
        )
        waiver = WaiverRecord(
            waiver_id="WT", rule_id="NBC_SAFE_01",
            rule_pack_id=str(rule.get("rule_pack_id", "")),
            rule_version=version, element_id="rail1", project_id="TEST",
            granted_by="test authority",
        )
        engine = RuleEngine([rule], execution_stage="submission")
        ctx = Context([rail], project_id="TEST", waivers={"WT": waiver})
        results = [r for r in engine.execute(ctx) if r.element_id == "rail1"]
        assert results[0].status == "FAIL"
        # Waiver identity built from the evidence-visible version must apply
        assert results[0].waiver_state == "applied"
        assert results[0].evidence.rule_version == version


class TestCliWaivers:
    def test_cli_accepts_waiver_file(self, tmp_path):
        wfile = tmp_path / "waivers.json"
        wfile.write_text(json.dumps([{
            "waiver_id": "W-CLI-1", "rule_id": "SOME_RULE",
            "element_id": "e1", "project_id": "p1",
            "granted_by": "authority", "custom_field": "kept-in-extra",
        }]))
        out = tmp_path / "out"
        proc = subprocess.run(
            [sys.executable, "-m", "arc.core.cli", "--demo",
             "--rules", RULES_DIR, "--output", str(out),
             "--waivers", str(wfile)],
            capture_output=True, text=True,
            cwd=str(Path(__file__).resolve().parents[2]),
        )
        assert proc.returncode == 0, proc.stderr
        assert "Loaded 1 waiver record(s)" in proc.stdout


class TestConfidenceLabel:
    def test_property_measurement_labelled_reported(self):
        rules = [r for r in load_rules(RULES_DIR) if r["id"] == "NBC_SAFE_01"]
        rail = Element(
            guid="rail1", ifc_class="IfcRailing",
            aabb=AABB(min=[0, 0, 0], max=[2.0, 0.1, 3.0]),
            properties={"Height": 0.9},
        )
        engine = RuleEngine(rules, execution_stage="submission")
        results = [r for r in engine.execute(Context([rail], project_id="T"))
                   if r.element_id == "rail1"]
        r = results[0]
        assert r.measurement_source == "property"
        assert r.evidence.confidence_label == "reported"


class TestInvariants:
    """Every non-binary result is routed; every element result carries evidence."""

    def test_all_nonbinary_routed_and_evidenced(self):
        from arc.core.data_models import AABB as _AABB
        rules = load_rules(RULES_DIR)
        els = [
            # stair without geometry -> gate INCONCLUSIVE (evidence invariant)
            Element(guid="stair1", ifc_class="IfcStair"),
            # space without FireExit tags -> in-rule INCONCLUSIVE (routing fallback)
            Element(guid="space1", ifc_class="IfcSpace",
                    aabb=_AABB(min=[0, 0, 0], max=[4, 4, 3])),
        ]
        engine = RuleEngine(rules, execution_stage="submission")
        results = engine.execute(Context(els, project_id="T"))
        # NOT_APPLICABLE needs no actor (nothing to act on), and class/model
        # scope dormant rows are informational - the routing invariant covers
        # element-scope INCONCLUSIVE / HUMAN_REQUIRED / UNSUPPORTED.
        nonbinary = [r for r in results
                     if r.status not in ("PASS", "FAIL", "NOT_APPLICABLE")
                     and r.element_id and r.scope == "element"]
        assert nonbinary, "expected non-binary results in this setup"
        assert all(r.provider_route for r in nonbinary), \
            [(r.rule_id, r.status, r.details) for r in nonbinary if not r.provider_route]
        assert all(r.evidence is not None for r in results if r.element_id), \
            [(r.rule_id, r.status) for r in results if r.element_id and r.evidence is None]
