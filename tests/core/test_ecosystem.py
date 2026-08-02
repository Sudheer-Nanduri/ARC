# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""Tests for ecosystem features: extension discovery, check type registry,
rule registry scanning/searching, and federation import paths.
"""
from arc.core.extensions import discover_extensions, list_check_types
from arc.core.registry import RuleRegistry


def test_extension_discovery_does_not_crash():
    """Extension discovery must be safe even when no extensions are installed."""
    loaded = discover_extensions()
    assert isinstance(loaded, list)


def test_all_builtin_check_types_are_registered():
    """The 12 built-in check types from the engine docstring must all be present."""
    types = list_check_types()
    expected = {
        "min_area", "min_width", "min_height", "max_height",
        "min_dimensions_2d", "property_min", "property_max",
        "clearance_zone", "turning_circle", "ratio",
        "distance_to_nearest", "count_nearby",
    }
    missing = expected - set(types)
    assert not missing, f"Missing check types: {missing}"


def test_rule_registry_scan_finds_india_pack():
    reg = RuleRegistry.scan("arc/core/rules")
    assert len(reg.packs) >= 1
    assert any(p.pack_id == "nbc_dcr_india_v1" for p in reg.packs)


def test_rule_registry_search_by_jurisdiction():
    reg = RuleRegistry.scan("arc/core/rules")
    india = reg.search(jurisdiction="India")
    assert len(india) >= 1

    empty = reg.search(jurisdiction="Antarctica")
    assert len(empty) == 0


def test_rule_registry_list_packs_structure():
    reg = RuleRegistry.scan("arc/core/rules")
    packs = reg.list_packs()
    assert len(packs) >= 1
    assert "pack_id" in packs[0]
    assert "rules" in packs[0]


def test_federated_ifc_importable_from_spatial():
    """load_federated_ifc must be importable from arc.spatial for backward compat."""
    from arc.spatial.ifc_integration import load_federated_ifc
    assert callable(load_federated_ifc)


def test_federated_ifc_importable_from_core():
    """load_federated_ifc must also be importable directly from arc.core."""
    from arc.core.ifc_integration import load_federated_ifc
    assert callable(load_federated_ifc)
