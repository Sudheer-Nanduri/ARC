# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""Tests for engine capabilities: rule packs, authority ordering,
temporal filtering, contradiction detection, external data validation,
delta comparison, enrichment values, stage exit criteria, and tolerances.
"""

from arc.core.config import get_tolerance
from arc.core.data_models import (
    AUTHORITY_HIERARCHY, Contradiction, EnrichmentValue,
    OverrideRecord, RulePackManifest, RuleResult,
    STATUS_FAIL, STATUS_INCONCLUSIVE, STATUS_PASS,
    StageExitCriteria,
)
from arc.core.delta import compare_results
from arc.core.rule_engine import RuleEngine
from arc.core.rule_loader import (
    detect_overrides, load_rule_pack_manifest, validate_pack_check_types,
)
from arc.core.validators import validate_external_json, validate_geojson, validate_rule_schema


# ---------------------------------------------------------------------------
# Rule Pack Manifest
# ---------------------------------------------------------------------------

def test_pack_manifest_loads_correctly():
    manifest = load_rule_pack_manifest("arc/core/rules/pack_manifest.json")
    assert manifest is not None
    assert manifest.pack_id == "nbc_dcr_india_v1"
    assert manifest.jurisdiction == "India"
    assert len(manifest.required_check_types) > 0


def test_all_required_check_types_are_registered():
    manifest = load_rule_pack_manifest("arc/core/rules/pack_manifest.json")
    from arc.core.rule_engine import _CHECK_REGISTRY
    missing = validate_pack_check_types(manifest, list(_CHECK_REGISTRY.keys()))
    assert missing == [], f"Check types declared in manifest but not registered: {missing}"


def test_missing_check_types_reported():
    manifest = RulePackManifest(
        pack_id="test",
        required_check_types=["min_area", "nonexistent_check"],
    )
    missing = validate_pack_check_types(manifest, ["min_area", "min_width"])
    assert "nonexistent_check" in missing


# ---------------------------------------------------------------------------
# Override Detection
# ---------------------------------------------------------------------------

def test_implicit_override_detected():
    json_rules = [{"id": "rule_a", "check_type": "min_area"}]
    py_rules = [{"id": "rule_a", "language": "python", "path": "/tmp/rule_a.py"}]
    overrides = detect_overrides(json_rules, py_rules)
    assert len(overrides) == 1
    assert overrides[0].override_type == "implicit"


def test_explicit_override_detected():
    json_rules = [{"id": "rule_b"}]
    py_rules = [{"id": "rule_b", "override_reason": "Better implementation"}]
    overrides = detect_overrides(json_rules, py_rules)
    assert len(overrides) == 1
    assert overrides[0].override_type == "explicit"


# ---------------------------------------------------------------------------
# Authority Hierarchy
# ---------------------------------------------------------------------------

def test_authority_hierarchy_ordering():
    assert AUTHORITY_HIERARCHY.index("statute") < AUTHORITY_HIERARCHY.index("regulation")
    assert AUTHORITY_HIERARCHY.index("regulation") < AUTHORITY_HIERARCHY.index("best_practice")


def test_authority_rank_statute_beats_guideline():
    assert RuleEngine._authority_rank({"authority": "statute"}) < RuleEngine._authority_rank({"authority": "guideline"})


# ---------------------------------------------------------------------------
# Temporal Filtering
# ---------------------------------------------------------------------------

def test_temporal_filtering_active_and_superseded():
    rules = [
        {"id": "r1", "effective_date": "2020-01-01", "superseded_date": "2025-01-01",
         "check_type": "min_area", "selector": {"ifc_class": "IfcSpace"}, "params": {"min_area": 1}},
        {"id": "r2", "effective_date": "2024-01-01",
         "check_type": "min_area", "selector": {"ifc_class": "IfcSpace"}, "params": {"min_area": 2}},
        {"id": "r3",
         "check_type": "min_area", "selector": {"ifc_class": "IfcSpace"}, "params": {"min_area": 3}},
    ]
    # 2024-06-01: r1 active (not superseded yet), r2 active, r3 always active
    ids = {r["id"] for r in RuleEngine(rules, regulation_date="2024-06-01")._filter_temporal(rules)}
    assert ids == {"r1", "r2", "r3"}

    # 2026-01-01: r1 superseded
    ids2 = {r["id"] for r in RuleEngine(rules, regulation_date="2026-01-01")._filter_temporal(rules)}
    assert "r1" not in ids2
    assert "r2" in ids2
    assert "r3" in ids2


# ---------------------------------------------------------------------------
# Contradiction Detection
# ---------------------------------------------------------------------------

def test_contradiction_detected_same_element_same_category():
    results = [
        RuleResult(rule_id="r1", element_id="e1", status=STATUS_PASS, category="fire"),
        RuleResult(rule_id="r2", element_id="e1", status=STATUS_FAIL, category="fire"),
    ]
    contradictions = RuleEngine._detect_contradictions(results)
    assert len(contradictions) == 1
    assert contradictions[0].element_id == "e1"


def test_no_contradiction_across_different_categories():
    results = [
        RuleResult(rule_id="r1", element_id="e1", status=STATUS_PASS, category="fire"),
        RuleResult(rule_id="r2", element_id="e1", status=STATUS_FAIL, category="accessibility"),
    ]
    assert len(RuleEngine._detect_contradictions(results)) == 0


# ---------------------------------------------------------------------------
# External Data Validation
# ---------------------------------------------------------------------------

def test_geojson_feature_collection_is_valid():
    data = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {}}],
    }
    assert validate_geojson(data) == []


def test_geojson_invalid_type_produces_warning():
    assert len(validate_geojson({"type": "InvalidType"})) > 0


def test_geojson_non_dict_produces_warning():
    assert len(validate_geojson("not a dict")) > 0


def test_external_json_missing_required_key():
    warnings = validate_external_json({"a": 1}, required_keys=["a", "b"])
    assert any("b" in w for w in warnings)


def test_rule_schema_missing_check_type_warned():
    assert any("check_type" in w for w in validate_rule_schema({"id": "r1"}))


def test_rule_schema_complete_has_no_warnings():
    assert validate_rule_schema({
        "id": "r1", "check_type": "min_area",
        "selector": {"ifc_class": "IfcSpace"},
    }) == []


# ---------------------------------------------------------------------------
# Delta Comparison
# ---------------------------------------------------------------------------

def test_delta_new_failure():
    delta = compare_results(
        [{"rule_id": "r1", "element_id": "e1", "status": "PASS"}],
        [{"rule_id": "r1", "element_id": "e1", "status": "FAIL"}],
    )
    assert delta["summary"]["new_failure_count"] == 1
    assert delta["summary"]["resolved_failure_count"] == 0


def test_delta_resolved_failure():
    delta = compare_results(
        [{"rule_id": "r1", "element_id": "e1", "status": "FAIL"}],
        [{"rule_id": "r1", "element_id": "e1", "status": "PASS"}],
    )
    assert delta["summary"]["resolved_failure_count"] == 1
    assert delta["summary"]["new_failure_count"] == 0


def test_delta_status_change():
    delta = compare_results(
        [{"rule_id": "r1", "element_id": "e1", "status": "INCONCLUSIVE"}],
        [{"rule_id": "r1", "element_id": "e1", "status": "PASS"}],
    )
    assert delta["summary"]["status_change_count"] == 1


# ---------------------------------------------------------------------------
# Enrichment Value
# ---------------------------------------------------------------------------

def test_enrichment_value_serialises():
    ev = EnrichmentValue(value=42.0, source="ML_classifier", confidence=0.85)
    d = ev.to_dict()
    assert d["value"] == 42.0
    assert d["source"] == "ML_classifier"


# ---------------------------------------------------------------------------
# Stage Exit Criteria (standalone, no engine)
# ---------------------------------------------------------------------------

def test_custom_stage_exit_criteria_met():
    criteria = StageExitCriteria("dd", min_checkability_pct=50.0, max_unresolved_critical=2)
    results = [
        RuleResult(rule_id="r1", status=STATUS_PASS),
        RuleResult(rule_id="r2", status=STATUS_FAIL, severity="major"),
        RuleResult(rule_id="r3", status=STATUS_INCONCLUSIVE),
    ]
    ev = criteria.evaluate(results)
    assert ev["met"] is True
    assert ev["checkability_pct"] >= 50.0


def test_custom_stage_exit_criteria_not_met():
    criteria = StageExitCriteria("cd", min_checkability_pct=90.0, max_unresolved_critical=0)
    results = [
        RuleResult(rule_id="r1", status=STATUS_PASS),
        RuleResult(rule_id="r2", status=STATUS_FAIL, severity="critical"),
        RuleResult(rule_id="r3", status=STATUS_INCONCLUSIVE),
        RuleResult(rule_id="r4", status=STATUS_INCONCLUSIVE),
    ]
    assert criteria.evaluate(results)["met"] is False


# ---------------------------------------------------------------------------
# Cross-Discipline Tolerances
# ---------------------------------------------------------------------------

def test_known_discipline_pair_tolerance():
    assert get_tolerance("architectural", "structural") == 0.05


def test_default_tolerance_for_unknown_disciplines():
    assert get_tolerance("unknown_a", "unknown_b") == 0.01
