# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""Regression test harness — golden snapshot comparison.

Runs the engine against the 8-element demo context and compares
rule_id + element_id + status against a stored snapshot.  The snapshot
is auto-created on first run and committed as a fixture.

Any change in rule count, selector logic, or check-type implementation
that alters the result set will fail here, making regressions explicit.
"""
import json
from pathlib import Path

import pytest

from arc.core.context import Context
from arc.core.data_models import AABB, Element
from arc.core.rule_engine import RuleEngine
from arc.core.rule_loader import load_json_rules, load_rules

# Snapshot lives in tests/fixtures/ - one level above this file (tests/core/)
FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"
SNAPSHOT_PATH = FIXTURE_DIR / "demo_results_snapshot.json"


def _demo_context() -> Context:
    return Context([
        Element(guid="p1",       ifc_class="IfcSpace",   aabb=AABB([0,0,0],[3.5,3.5,2.8]), properties={"area": 12.25, "SpaceType": "Parking"}),
        Element(guid="p2",       ifc_class="IfcSpace",   aabb=AABB([0,0,0],[2.0,2.0,2.8]), properties={"area": 4.0,   "SpaceType": "Parking"}),
        Element(guid="door1",    ifc_class="IfcDoor",    aabb=AABB([0,0,0],[1.0,0.2,2.1]), properties={"Width": 1.0}),
        Element(guid="stair1",   ifc_class="IfcStair",   aabb=AABB([0,0,0],[1.8,4.0,3.0]), properties={}),
        Element(guid="room1",    ifc_class="IfcSpace",   aabb=AABB([0,0,0],[4.0,3.5,2.8]), properties={"area": 14.0,  "SpaceType": "Habitable"}),
        Element(guid="kitchen1", ifc_class="IfcSpace",   aabb=AABB([0,0,0],[2.8,2.0,2.8]), properties={"area": 5.6,   "SpaceType": "Kitchen"}),
        Element(guid="bath1",    ifc_class="IfcSpace",   aabb=AABB([0,0,0],[1.5,1.8,2.4]), properties={"area": 2.7,   "SpaceType": "Bathroom"}),
        Element(guid="railing1", ifc_class="IfcRailing", aabb=AABB([0,0,0],[3.0,0.1,1.1]), properties={}),
    ])


def _strip_volatile(results):
    """Remove timestamps and version strings for stable snapshot comparison."""
    out = []
    for r in results:
        d = r.to_dict() if hasattr(r, "to_dict") else dict(r)
        d.pop("timestamp", None)
        if isinstance(d.get("evidence"), dict):
            d["evidence"].pop("timestamp", None)
            d["evidence"].pop("arc_version", None)
        out.append(d)
    out.sort(key=lambda x: (x.get("rule_id", ""), x.get("element_id", "")))
    return out


def test_demo_result_count():
    """Demo context should produce at least 15 results from the full rule pack."""
    rules = load_rules("arc/core/rules")
    results = RuleEngine(rules).execute(_demo_context())
    assert len(results) >= 15, f"Expected ≥15 results, got {len(results)}"


def test_demo_regression():
    """Rule-element-status triples must match the golden snapshot exactly."""
    rules = load_rules("arc/core/rules")
    engine = RuleEngine(rules)
    current = _strip_volatile(engine.execute(_demo_context()))

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    if not SNAPSHOT_PATH.exists():
        SNAPSHOT_PATH.write_text(json.dumps(current, indent=2, default=str), encoding="utf-8")
        pytest.skip("Golden snapshot created on first run — re-run to verify")

    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert len(current) == len(snapshot), (
        f"Result count changed: snapshot={len(snapshot)}, current={len(current)}"
    )
    for i, (cur, snap) in enumerate(zip(current, snapshot)):
        assert cur.get("rule_id") == snap.get("rule_id"), f"[{i}] rule_id mismatch"
        assert cur.get("element_id") == snap.get("element_id"), f"[{i}] element_id mismatch"
        assert cur.get("status") == snap.get("status"), (
            f"[{i}] status changed: {snap.get('rule_id')}+{snap.get('element_id')}: "
            f"{snap.get('status')} → {cur.get('status')}"
        )


def test_json_rules_all_have_evidence_bundles():
    """Every JSON rule result must carry an EvidenceBundle (Python rules manage their own)."""
    json_rules = load_json_rules("arc/core/rules/json_rules")
    results = RuleEngine(json_rules).execute(_demo_context())
    missing = [f"{r.rule_id}+{r.element_id}" for r in results if r.evidence is None]
    assert not missing, f"Missing evidence on: {missing}"
