# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""Tests for routing, deviation tracking, scope-aware execution, and waivers.

Contract reference: docs/specifications/data-model-schema.md §11 (routing and deviation).
One file covers all the focused scenarios listed there; each test below maps
to a plan bullet via its docstring.
"""
from __future__ import annotations

import json
import zipfile


from arc.core.context import Context
from arc.core.data_models import (
    AABB, Element, RuleResult, WaiverRecord, build_model_summary,
    ROUTING_REGISTRY, register_route, resolve_route,
    ROUTE_KIND_GATE, ROUTE_KIND_HUMAN_REASON, ROUTE_KIND_CONFIDENCE_DIM,
    STATUS_PASS, STATUS_FAIL, STATUS_INCONCLUSIVE,
    STATUS_HUMAN_REQUIRED, STATUS_NOT_APPLICABLE,
    WAIVER_STATE_APPLIED, WAIVER_STATE_INVALID, WAIVER_STATE_SUPERSEDED,
    WAIVER_INVALIDATION_RULE_VERSION_CHANGED, WAIVER_INVALIDATION_PAST_EXPIRY_DATE,
)
from arc.core.bcf_exporter import export_bcf
from arc.core.delta import compare_results
from arc.core.rule_engine import RuleEngine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _ctx_with_stairs(n: int, project_id: str = "proj_a") -> Context:
    elements = [
        Element(guid=f"s{i}", ifc_class="IfcStair",
                aabb=AABB([0, 0, 0], [1.8, 4.0, 3.0]),
                properties={"FireExitTagged": (i % 2 == 0)})
        for i in range(n)
    ]
    return Context(elements=elements, project_id=project_id, model_source="test.ifc")


def _door_ctx(low_semantic: bool = False) -> Context:
    el = Element(
        guid="d1", ifc_class="IfcDoor",
        aabb=AABB([0, 0, 0], [1.0, 0.2, 2.1]),
        properties={"Width": 1.0},
    )
    if low_semantic:
        el.confidence["semantic"] = 0.1
    return Context([el], project_id="proj_a", model_source="test.ifc")


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------

def test_class_scope_count_rule_emits_single_aggregate_result():
    rule = {
        "id": "stair_count",
        "selector": {"ifc_class": "IfcStair"},
        "scope": "class",
        "check_type": "count",
        "params": {"min_count": 1},
        "severity": "major", "category": "fire_egress",
    }
    results = RuleEngine([rule]).execute(_ctx_with_stairs(3))
    assert len(results) == 1
    r = results[0]
    assert r.scope == "class"
    assert r.element_id == "class:IfcStair"
    assert r.status == STATUS_PASS
    assert r.affected_element_ids == ["s0", "s1", "s2"]
    assert r.measured_value == 3.0


def test_class_scope_taxonomy_agnostic_for_non_ifc_classes():
    """Sentinel keys are opaque; engine matches on Element.ifc_class
    literally, regardless of IFC vocabulary."""
    elements = [Element(guid=f"w{i}", ifc_class="Wall") for i in range(2)]
    ctx = Context(elements, project_id="proj_a", model_source="test.json")
    rule = {
        "id": "wall_count",
        "selector": {"ifc_class": "Wall"},
        "scope": "class",
        "check_type": "count",
        "params": {"min_count": 1},
    }
    results = RuleEngine([rule]).execute(ctx)
    assert len(results) == 1
    assert results[0].element_id == "class:Wall"
    assert results[0].status == STATUS_PASS


def test_model_scope_rule_anchors_on_model_sentinel():
    rule = {
        "id": "all_stairs_tagged",
        "selector": {"ifc_class": "IfcStair"},
        "scope": "model",
        "check_type": "all_pass",
        "params": {"property": "FireExitTagged", "equals": True},
    }
    results = RuleEngine([rule]).execute(_ctx_with_stairs(3))
    assert len(results) == 1
    r = results[0]
    assert r.scope == "model"
    assert r.element_id == "model:"
    # Not every stair is tagged -> FAIL but with affected ids populated
    assert r.affected_element_ids == ["s0", "s1", "s2"]


# ---------------------------------------------------------------------------
# Dormant-rule split
# ---------------------------------------------------------------------------

def test_dormant_class_absent_emits_not_applicable():
    rule = {
        "id": "ramp_slope",
        "selector": {"ifc_class": "IfcRamp"},
        "check_type": "property_max",
        "params": {"property": "slope", "max_value": 0.0833},
    }
    results = RuleEngine([rule]).execute(_ctx_with_stairs(2))
    assert len(results) == 1
    r = results[0]
    assert r.status == STATUS_NOT_APPLICABLE
    assert r.element_id == "class:IfcRamp"
    assert r.scope == "class"


def test_dormant_property_filter_narrows_to_zero_emits_data_tagging_gap():
    rule = {
        "id": "corridor_width",
        "selector": {"ifc_class": "IfcStair", "properties": {"SpaceType": "Corridor"}},
        "check_type": "min_width",
        "params": {"min_width": 1.5},
    }
    results = RuleEngine([rule]).execute(_ctx_with_stairs(2))
    assert len(results) == 1
    r = results[0]
    assert r.status == STATUS_INCONCLUSIVE
    assert r.details.get("gate") == "data_tagging_gap"
    assert r.scope == "class"


# ---------------------------------------------------------------------------
# Routing resolution
# ---------------------------------------------------------------------------

def test_human_judgment_gate_records_human_reason_and_route():
    rule = {
        "id": "subj_review",
        "selector": {"ifc_class": "IfcDoor"},
        "check_type": "min_width",
        "params": {"min_width": 0.9},
        "requires_human_judgment": True,
    }
    results = RuleEngine([rule]).execute(_door_ctx())
    r = next(x for x in results if x.element_id == "d1")
    assert r.status == STATUS_HUMAN_REQUIRED
    assert r.human_reason == "subjective_rule"
    assert r.arrival_path == "gate:human_judgment"
    assert r.provider_route == "P10"  # subjective_rule -> P10 from registry


def test_rule_level_routing_override_wins_over_registry():
    rule = {
        "id": "subj_review",
        "selector": {"ifc_class": "IfcDoor"},
        "check_type": "min_width",
        "params": {"min_width": 0.9},
        "requires_human_judgment": True,
        "routing": {"primary": "P7", "fallback": "P10"},
    }
    results = RuleEngine([rule]).execute(_door_ctx())
    r = next(x for x in results if x.element_id == "d1")
    assert r.provider_route == "P7"


def test_low_semantic_confidence_routes_to_provider_not_human():
    """Low confidence emits INCONCLUSIVE and routes to a provider
    (P4 for semantic), never auto-promoted to HUMAN_REQUIRED."""
    rule = {
        "id": "door_width",
        "selector": {"ifc_class": "IfcDoor"},
        "check_type": "min_width",
        "params": {"min_width": 0.9},
        "confidence_requirement": {"semantic": 0.5},
    }
    results = RuleEngine([rule]).execute(_door_ctx(low_semantic=True))
    r = next(x for x in results if x.element_id == "d1")
    assert r.status == STATUS_INCONCLUSIVE
    assert r.details.get("gate") == "confidence_insufficient"
    assert r.provider_route == "P4"


# ---------------------------------------------------------------------------
# Waivers
# ---------------------------------------------------------------------------

def _door_failing_rule(rule_version: str = "1.0"):
    return {
        "id": "door_width",
        "selector": {"ifc_class": "IfcDoor"},
        "check_type": "min_width",
        "params": {"min_width": 2.0},  # forces FAIL on Width=1.0
        "rule_pack_id": "test_pack",
        "rule_version": rule_version,
    }


def _waiver(rule_version: str = "1.0", project_id: str = "proj_a",
            expires_at=None, occasion: str = "permanent") -> WaiverRecord:
    return WaiverRecord(
        waiver_id="w1",
        finding_key="test_pack:door_width:1.0:d1",
        rule_id="door_width",
        rule_pack_id="test_pack",
        rule_version=rule_version,
        element_id="d1",
        project_id=project_id,
        granted_by="compliance_officer_42",
        authority_basis="NBC 2016 §4.3.1(b)",
        rationale="Heritage building; 1.0m door retained per conservation order",
        conditions=["Add fire detector add-on"],
        occasion=occasion,
        expires_at=expires_at,
    )


def test_waiver_attaches_and_status_stays_fail():
    ctx = _door_ctx()
    ctx.waivers["w1"] = _waiver()
    results = RuleEngine([_door_failing_rule()]).execute(ctx)
    r = next(x for x in results if x.element_id == "d1")
    assert r.status == STATUS_FAIL  # status is never flipped
    assert r.waiver is not None
    assert r.waiver_state == WAIVER_STATE_APPLIED
    assert r.human_reason == "deviation_request"
    assert r.provider_route == "P10"  # deviation_request -> compliance officer


def test_waiver_invalid_when_rule_version_changes():
    ctx = _door_ctx()
    ctx.waivers["w1"] = _waiver(rule_version="1.0")
    results = RuleEngine([_door_failing_rule(rule_version="1.1")]).execute(ctx)
    r = next(x for x in results if x.element_id == "d1")
    assert r.status == STATUS_FAIL  # FAIL stays open
    assert r.waiver_state == WAIVER_STATE_INVALID
    assert r.waiver_invalidation_reason == WAIVER_INVALIDATION_RULE_VERSION_CHANGED
    # Engine never auto-emits HUMAN_REQUIRED for invalidation alone
    assert r.status != STATUS_HUMAN_REQUIRED


def test_waiver_invalid_when_past_expiry_date():
    ctx = _door_ctx()
    ctx.waivers["w1"] = _waiver(expires_at="2000-01-01T00:00:00+00:00")
    results = RuleEngine([_door_failing_rule()]).execute(ctx)
    r = next(x for x in results if x.element_id == "d1")
    assert r.waiver_state == WAIVER_STATE_INVALID
    assert r.waiver_invalidation_reason == WAIVER_INVALIDATION_PAST_EXPIRY_DATE
    assert r.status == STATUS_FAIL


def test_waiver_does_not_bind_across_projects():
    ctx = _door_ctx()  # project_id = "proj_a"
    ctx.waivers["w1"] = _waiver(project_id="proj_b")  # different project
    results = RuleEngine([_door_failing_rule()]).execute(ctx)
    r = next(x for x in results if x.element_id == "d1")
    # Identity mismatch -> not applied substantively; marked invalid informational
    assert r.waiver_state == WAIVER_STATE_INVALID
    assert r.status == STATUS_FAIL


def test_waiver_one_time_only_invalid_when_deployer_signals_consumption():
    """'one-time' / 'until_next_submission' waivers are
    exhausted only when the deployer marks them consumed; the engine has no
    independent run history."""
    rule = _door_failing_rule()
    waiver = _waiver(occasion="one-time")

    # First run: no consumption signal -> waiver applies
    ctx_first = _door_ctx()
    ctx_first.waivers["w1"] = waiver
    r1 = next(x for x in RuleEngine([rule]).execute(ctx_first) if x.element_id == "d1")
    assert r1.waiver_state == WAIVER_STATE_APPLIED

    # Second run: deployer marks the waiver consumed -> waiver invalidated
    ctx_second = _door_ctx()
    ctx_second.waivers["w1"] = waiver
    ctx_second.metadata["consumed_waiver_ids"] = ["w1"]
    r2 = next(x for x in RuleEngine([rule]).execute(ctx_second) if x.element_id == "d1")
    assert r2.waiver_state == WAIVER_STATE_INVALID


def test_waiver_superseded_when_new_result_passes():
    """A waiver bound to a PASS result is rendered as superseded — informational only."""
    ctx = _door_ctx()
    ctx.waivers["w1"] = _waiver()
    passing_rule = {
        "id": "door_width",
        "selector": {"ifc_class": "IfcDoor"},
        "check_type": "min_width",
        "params": {"min_width": 0.5},  # passes Width=1.0
        "rule_pack_id": "test_pack",
        "rule_version": "1.0",
    }
    results = RuleEngine([passing_rule]).execute(ctx)
    r = next(x for x in results if x.element_id == "d1")
    assert r.status == STATUS_PASS
    assert r.waiver_state == WAIVER_STATE_SUPERSEDED


# ---------------------------------------------------------------------------
# Dimensional summary
# ---------------------------------------------------------------------------

def test_dimensional_summary_keeps_axes_independent():
    """Aggregate-scope rows must not be folded into pair_counts."""
    element_rule = {
        "id": "door_width", "selector": {"ifc_class": "IfcDoor"},
        "check_type": "min_width", "params": {"min_width": 0.5},
    }
    class_rule = {
        "id": "stair_count", "selector": {"ifc_class": "IfcDoor"},
        "scope": "class", "check_type": "count", "params": {"min_count": 1},
    }
    ctx = _door_ctx()
    results = RuleEngine([element_rule, class_rule]).execute(ctx)
    summary = build_model_summary(results, ctx.elements, [element_rule, class_rule])
    d = summary.to_dict()
    # pair_counts contains the element-scope row only
    assert d["pair_counts"]["pass"] == 1
    # aggregate_counts contains the class-scope row only - never folded into pair
    assert d["aggregate_counts"]["pass"] == 1
    assert d["pair_counts"]["pass"] + d["aggregate_counts"]["pass"] == 2


# ---------------------------------------------------------------------------
# BCF multi-component
# ---------------------------------------------------------------------------

def test_bcf_class_scope_renders_one_issue_with_many_components(tmp_path):
    rule = {
        "id": "stair_tagged", "selector": {"ifc_class": "IfcStair"},
        "scope": "class", "check_type": "any_pass",
        "params": {"property": "FireExitTagged", "equals": True},
    }
    results = RuleEngine([rule]).execute(_ctx_with_stairs(4))
    out = tmp_path / "out.bcfzip"
    export_bcf([r.to_dict() for r in results], str(out))
    with zipfile.ZipFile(out) as zf:
        index = json.loads(zf.read("index.json"))
        issues = index["issues"]
        # One aggregate finding -> one issue with 4 components
        class_scope = [i for i in issues if i["scope"] == "class"]
        assert len(class_scope) == 1
        assert class_scope[0]["component_count"] == 4

        payload = json.loads(zf.read(class_scope[0]["file"]))
        assert payload["scope"] == "class"
        assert payload["anchor"] == "class:IfcStair"
        assert len(payload["element_guids"]) == 4


# ---------------------------------------------------------------------------
# Registry extensibility
# ---------------------------------------------------------------------------

def test_register_route_adds_new_category_and_resolves(monkeypatch):
    """Adding a new registry entry via the public API and exercising it end-to-end."""
    original = dict(ROUTING_REGISTRY)
    try:
        register_route(
            "custom_review",
            "Domain expert reviews the finding by hand",
            "domain_expert",
            "P9",
            ROUTE_KIND_HUMAN_REASON,
        )
        rule = {
            "id": "custom_rule",
            "selector": {"ifc_class": "IfcDoor"},
            "check_type": "min_width",
            "params": {"min_width": 0.9},
            "requires_human_judgment": True,
            "human_reason": "custom_review",
        }
        results = RuleEngine([rule]).execute(_door_ctx())
        r = next(x for x in results if x.element_id == "d1")
        assert r.human_reason == "custom_review"
        assert r.provider_route == "P9"
    finally:
        ROUTING_REGISTRY.clear()
        ROUTING_REGISTRY.update(original)


# ---------------------------------------------------------------------------
# Waiver-aware delta
# ---------------------------------------------------------------------------

def test_delta_treats_waiver_loss_as_newly_open():
    baseline = [{
        "rule_id": "door_width", "element_id": "d1",
        "status": "FAIL", "waiver_state": "applied",
    }]
    current = [{
        "rule_id": "door_width", "element_id": "d1",
        "status": "FAIL", "waiver_state": None,
    }]
    diff = compare_results(baseline, current)
    assert diff["summary"]["new_failure_count"] == 1
    assert diff["summary"]["newly_open_after_waiver_loss_count"] == 1


# ---------------------------------------------------------------------------
# Confirm authority dispute still uses the registry seed
# ---------------------------------------------------------------------------

def test_seeded_categories_present_in_registry():
    for cat in (
        "data_missing", "geometry_limited", "external_missing", "unsupported",
        "subjective_rule", "deviation_request", "contradiction_adjudication",
        "authority_dispute", "semantic_confidence_low", "geometry_confidence_low",
        "topology_confidence_low", "performance_gap", "readiness_issue",
        "data_tagging_gap",
    ):
        assert resolve_route(cat) is not None, f"missing seed: {cat}"
