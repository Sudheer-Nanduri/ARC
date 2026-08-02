# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""Scope-aware BCF exporter.

Writes a ``.bcfzip`` archive containing one JSON issue per finding. The
payload is scope-aware: a class- or model-scope result emits a single issue
whose ``element_guids`` carries every element the rule examined (BCF 2.1/3.0
``Components`` semantics), and ``anchor`` records the canonical sentinel.

This is the JSON-fallback path; when ``bcf-python`` is available, deployers
wire it on top of the same payload.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import List, Dict, Any


def _scope_for(result: Dict[str, Any]) -> str:
    s = result.get("scope")
    if s in ("element", "class", "model"):
        return s
    eid = str(result.get("element_id") or "")
    if eid.startswith("class:"):
        return "class"
    if eid == "model:" or eid.startswith("model:"):
        return "model"
    return "element"


def _element_guids(result: Dict[str, Any], scope: str) -> List[str]:
    """element_guids is a list, always populated; length 1 for element scope.

    For aggregate scope we use ``affected_element_ids`` if present, falling back
    to the raw element_id when nothing else is supplied.
    """
    if scope == "element":
        eid = result.get("element_id") or result.get("element_guid")
        return [str(eid)] if eid else []
    affected = result.get("affected_element_ids") or []
    if affected:
        return [str(x) for x in affected]
    eid = result.get("element_id")
    return [str(eid)] if eid else []


def export_bcf(results: List[Dict[str, Any]], filepath: str) -> None:
    """Export a scope-aware BCF ZIP archive.

    A class-scope finding referencing N stairs becomes one BCF issue with
    N components — not N issues. This matches BCF 2.1/3.0 ``Components``
    semantics and avoids fan-out on aggregate findings.
    """
    out = Path(filepath)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        index = {"issues": []}
        for i, r in enumerate(results, start=1):
            scope = _scope_for(r)
            guids = _element_guids(r, scope)
            issue_name = f"issues/issue_{i}.json"
            payload = {
                "id": r.get("rule_id") or f"issue_{i}",
                "element_guids": guids,
                # Backwards-compat alias for tools that still read element_guid
                "element_guid": guids[0] if guids else None,
                "scope": scope,
                "anchor": r.get("element_id"),
                "status": r.get("status") or ("PASS" if r.get("passed") else "FAIL"),
                "human_reason": r.get("human_reason"),
                "arrival_path": r.get("arrival_path"),
                "provider_route": r.get("provider_route"),
                "waiver": r.get("waiver"),
                "waiver_state": r.get("waiver_state"),
                "waiver_invalidation_reason": r.get("waiver_invalidation_reason"),
                "message": r.get("message"),
                "details": r.get("details", {}),
            }
            zf.writestr(issue_name, json.dumps(payload, indent=2))
            index["issues"].append({
                "file": issue_name, "id": payload["id"],
                "scope": scope, "component_count": len(guids),
            })

        zf.writestr("index.json", json.dumps(index, indent=2))
