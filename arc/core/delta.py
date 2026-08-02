# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""Delta comparison between two ARC result sets.

Compares a baseline results JSON against current results to identify:
- New failures (not in baseline)
- Resolved failures (in baseline but not current)
- Status changes (same rule+element, different status)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _result_key(r: Dict[str, Any]) -> Tuple[str, str]:
    """Unique key for a result: (rule_id, element_id).

    Sentinel ids (``class:<key>``, ``model:``) are compared like any other
    string — a class-scope finding's identity is its anchor.
    """
    return (r.get("rule_id", ""), r.get("element_id", ""))


def _is_open_fail(r: Dict[str, Any]) -> bool:
    """A FAIL with no in-force waiver — what 'open FAIL' means in the delta."""
    if r.get("status") != "FAIL":
        return False
    return r.get("waiver_state") != "applied"


def load_baseline(path: str) -> List[Dict[str, Any]]:
    """Load baseline results from a JSON report file."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data.get("element_results", [])
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def compare_results(
    baseline: List[Dict[str, Any]],
    current: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compare baseline vs current results.

    Returns:
        {
            "new_failures": [...],        # FAIL in current, not FAIL in baseline
            "resolved_failures": [...],   # FAIL in baseline, not FAIL in current
            "status_changes": [...],      # Same key, different status
            "summary": {
                "new_failure_count": int,
                "resolved_failure_count": int,
                "status_change_count": int,
                "baseline_total": int,
                "current_total": int,
            }
        }
    """
    base_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for r in baseline:
        key = _result_key(r)
        if key[0]:  # skip results without rule_id
            base_map[key] = r

    curr_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for r in current:
        key = _result_key(r)
        if key[0]:
            curr_map[key] = r

    new_failures = []
    resolved_failures = []
    status_changes = []
    newly_open_after_waiver_loss: List[Dict[str, Any]] = []

    # New failures: open-FAIL in current but not open-FAIL in baseline.
    # Waiver-aware diff: a finding waived in run A but no longer
    # waived in run B counts as a newly-open delta even if both are FAIL.
    for key, curr in curr_map.items():
        if not _is_open_fail(curr):
            continue
        base = base_map.get(key)
        if base is None or not _is_open_fail(base):
            entry = {
                "rule_id": key[0],
                "element_id": key[1],
                "current_status": curr.get("status", ""),
                "baseline_status": base.get("status") if base else "not_present",
                "message": curr.get("message", ""),
                "waiver_state_current": curr.get("waiver_state"),
                "waiver_state_baseline": base.get("waiver_state") if base else None,
            }
            new_failures.append(entry)
            if base is not None and base.get("status") == "FAIL" and base.get("waiver_state") == "applied":
                newly_open_after_waiver_loss.append(entry)

    # Resolved failures: open-FAIL in baseline but not open-FAIL in current.
    for key, base in base_map.items():
        if not _is_open_fail(base):
            continue
        curr = curr_map.get(key)
        if curr is None or not _is_open_fail(curr):
            resolved_failures.append({
                "rule_id": key[0],
                "element_id": key[1],
                "baseline_status": "FAIL",
                "current_status": curr.get("status") if curr else "not_present",
                "waiver_state_current": curr.get("waiver_state") if curr else None,
            })

    # Status changes: same key, different status
    for key in set(base_map.keys()) & set(curr_map.keys()):
        base_status = base_map[key].get("status", "")
        curr_status = curr_map[key].get("status", "")
        if base_status != curr_status:
            status_changes.append({
                "rule_id": key[0],
                "element_id": key[1],
                "baseline_status": base_status,
                "current_status": curr_status,
            })

    return {
        "new_failures": new_failures,
        "resolved_failures": resolved_failures,
        "status_changes": status_changes,
        "newly_open_after_waiver_loss": newly_open_after_waiver_loss,
        "summary": {
            "new_failure_count": len(new_failures),
            "resolved_failure_count": len(resolved_failures),
            "status_change_count": len(status_changes),
            "newly_open_after_waiver_loss_count": len(newly_open_after_waiver_loss),
            "baseline_total": len(baseline),
            "current_total": len(current),
        },
    }
