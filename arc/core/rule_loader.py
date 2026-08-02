# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""Rule loader utilities.

Provides JSON rule loading, Python rule discovery, and clause coverage
ledger loading for an ARC rule-pack folder. Python rules can optionally
expose a module-level ``RULE_METADATA`` dict; bundled rules also fall
back to a small metadata map so selectors, categories, and severities
stay aligned with the UI workflow.
"""
import ast
import json
from collections import defaultdict
from pathlib import Path
from typing import List, Dict, Any, Optional

from .data_models import ClauseCoverage, RulePackManifest, OverrideRecord


_BUNDLED_PY_RULE_METADATA: Dict[str, Dict[str, Any]] = {
    "corridor_width": {
        "selector": {"ifc_class": "IfcSpace", "properties": {"LongName": "LOBBY"}},
        "severity": "major",
        "category": "spatial",
    },
    "dcr_fire_01": {
        "selector": {"ifc_class": "IfcStair"},
        "severity": "critical",
        "category": "fire_egress",
    },
    "dcr_fire_03": {
        "selector": {"ifc_class": "IfcSpace"},
        "severity": "critical",
        "category": "fire_egress",
    },

    "nbc_acc_01": {
        "selector": {"ifc_class": "IfcDoor"},
        "severity": "critical",
        "category": "accessibility",
    },
    "nbc_acc_02": {
        "selector": {"ifc_class": "IfcSpace"},
        "severity": "major",
        "category": "accessibility",
    },
    "nbc_vent_01": {
        "selector": {"ifc_class": "IfcSpace"},
        "severity": "major",
        "category": "ventilation",
    },
    "window_vent_ratio": {
        "selector": {"ifc_class": "IfcWindow"},
        "severity": "major",
        "category": "ventilation",
    },
}


def _load_python_rule_metadata(path: Path) -> Dict[str, Any]:
    """Read optional module metadata without executing the rule file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except Exception:
        return {}

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "RULE_METADATA":
                try:
                    metadata = ast.literal_eval(node.value)
                except Exception:
                    return {}
                return metadata if isinstance(metadata, dict) else {}
    return {}


def load_json_rules(path: str) -> List[Dict[str, Any]]:
    """Load JSON rules from a directory or single file.

    Returns a list of rule dictionaries. Invalid JSON files are skipped.
    """
    p = Path(path)
    rules: List[Dict[str, Any]] = []
    if p.is_dir():
        for f in sorted(p.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    rules.extend(data)
                elif isinstance(data, dict):
                    rules.append(data)
            except Exception:
                # ignore invalid rule files
                continue
    else:
        # single file
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                rules = data
            elif isinstance(data, dict):
                rules = [data]
        except Exception:
            rules = []
    return rules


def load_python_rules(path: str) -> List[Dict[str, Any]]:
    """Discover simple Python rule files under a directory.

    For each `.py` file found, returns a descriptor dict with keys:
      - id: filename stem
      - language: "python"
      - path: absolute path to the file

    This is intentionally lightweight; the rule engine is responsible for
    reading and validating the file content and executing it in a sandbox.
    """
    p = Path(path)
    rules: List[Dict[str, Any]] = []
    if not p.exists() or not p.is_dir():
        return rules
    for f in sorted(p.glob("*.py")):
        try:
            rid = f.stem
            rule = {"id": rid, "language": "python", "path": str(f)}
            metadata = dict(_BUNDLED_PY_RULE_METADATA.get(rid, {}))
            metadata.update(_load_python_rule_metadata(f))
            rule.update(metadata)
            rules.append(rule)
        except Exception:
            continue
    return rules


def load_rules(root_dir: str) -> List[Dict[str, Any]]:
    """Load JSON and Python rules from an ARC rule-pack folder.

    Looks for `json_rules/` and `python_rules/` subfolders. If a
    ``pack_manifest.json`` sits at the root, its ``pack_id`` and ``version``
    are attached to every loaded rule so waiver identity matching
    has the full 5-tuple to compare against.
    """
    root = Path(root_dir)
    rules: List[Dict[str, Any]] = []
    json_dir = root / "json_rules"
    py_dir = root / "python_rules"
    json_rules: List[Dict[str, Any]] = []
    py_rules: List[Dict[str, Any]] = []
    if json_dir.exists():
        json_rules = load_json_rules(str(json_dir))
    if py_dir.exists():
        py_rules = load_python_rules(str(py_dir))

    # Attach rule_pack_id + rule_version from the pack manifest so the engine
    # and waiver identity have them on every rule dict.
    manifest_path = root / "pack_manifest.json"
    manifest = load_rule_pack_manifest(str(manifest_path)) if manifest_path.exists() else None
    if manifest is not None:
        for rule in json_rules + py_rules:
            rule.setdefault("rule_pack_id", manifest.pack_id)
            rule.setdefault("rule_version", str(rule.get("version", manifest.version)))

    # Prefer Python rules when JSON and Python use the same rule id with only
    # case differences; also suppress explicit legacy/demo checks.
    merged: Dict[str, Dict[str, Any]] = {}
    for rule in json_rules:
        rid = str(rule.get("id", "")).strip()
        if not rid:
            continue
        key = rid.lower()
        merged[key] = rule

    for rule in py_rules:
        rid = str(rule.get("id", "")).strip()
        if not rid:
            continue
        key = rid.lower()
        merged[key] = rule

    for rule in json_rules + py_rules:
        rid = str(rule.get("id", "")).strip()
        key = rid.lower()
        if not rid:
            continue
        chosen = merged.get(key)
        if chosen is rule and chosen not in rules:
            rules.append(chosen)

    return rules


# ---------------------------------------------------------------------------
# Clause-to-Rule Coverage Ledger
# ---------------------------------------------------------------------------


def load_clause_ledger(path: str) -> List[ClauseCoverage]:
    """Load a clause coverage ledger from a JSON file.

    Expected format: JSON array of objects with keys:
      clause_ref, rule_ids, automatable, rationale
    """
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        return [
            ClauseCoverage(
                clause_ref=entry.get("clause_ref", ""),
                rule_ids=entry.get("rule_ids", []),
                automatable=entry.get("automatable", True),
                rationale=entry.get("rationale"),
            )
            for entry in data
            if isinstance(entry, dict) and entry.get("clause_ref")
        ]
    except Exception:
        return []


def build_clause_ledger_from_rules(rules: List[Dict[str, Any]]) -> List[ClauseCoverage]:
    """Auto-generate a clause ledger by grouping rules by their ``source`` field.

    Rules without a ``source`` field are skipped. All generated entries are
    marked ``automatable=True`` since they have implementing rules.
    """
    source_to_rules: Dict[str, List[str]] = defaultdict(list)
    for rule in rules:
        source = rule.get("source", "").strip()
        rid = rule.get("id", "").strip()
        if source and rid:
            source_to_rules[source].append(rid)
    return [
        ClauseCoverage(clause_ref=source, rule_ids=rids, automatable=True)
        for source, rids in sorted(source_to_rules.items())
    ]


# ---------------------------------------------------------------------------
# Rule Pack Manifest
# ---------------------------------------------------------------------------

def load_rule_pack_manifest(path: str) -> Optional[RulePackManifest]:
    """Load a rule pack manifest from a JSON file."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return RulePackManifest(
            pack_id=data.get("pack_id", ""),
            version=data.get("version", "1.0.0"),
            jurisdiction=data.get("jurisdiction", ""),
            author=data.get("author", ""),
            description=data.get("description", ""),
            required_check_types=data.get("required_check_types", []),
            governance_status=data.get("governance_status", "draft"),
            effective_date=data.get("effective_date"),
            superseded_date=data.get("superseded_date"),
            rule_ids=data.get("rule_ids", []),
        )
    except Exception:
        return None


def validate_pack_check_types(
    manifest: RulePackManifest,
    registered_check_types: List[str],
) -> List[str]:
    """Return list of required check types that are NOT registered."""
    registered = set(registered_check_types)
    return [ct for ct in manifest.required_check_types if ct not in registered]


# ---------------------------------------------------------------------------
# Override Detection
# ---------------------------------------------------------------------------

def detect_overrides(
    json_rules: List[Dict[str, Any]],
    py_rules: List[Dict[str, Any]],
) -> List[OverrideRecord]:
    """Detect rule ID collisions between JSON and Python rules.

    Returns override records for every ID that appears in both sets.
    Python rules win by convention (they are more specific implementations).
    """

    json_ids = {str(r.get("id", "")).lower(): r for r in json_rules if r.get("id")}
    overrides: List[OverrideRecord] = []

    for rule in py_rules:
        rid = str(rule.get("id", "")).strip()
        key = rid.lower()
        if key in json_ids:
            json_rule = json_ids[key]
            has_explicit_reason = bool(rule.get("override_reason"))
            overrides.append(OverrideRecord(
                rule_id=rid,
                overridden_by=rid,
                reason=rule.get("override_reason", "Python rule overrides JSON rule with same ID"),
                override_type="explicit" if has_explicit_reason else "implicit",
                winning_authority=rule.get("authority", ""),
                losing_authority=json_rule.get("authority", ""),
            ))
    return overrides
