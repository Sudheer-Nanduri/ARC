# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""Report generator producing structured JSON, HTML, and styled PDF outputs."""
from __future__ import annotations

import json
from html import escape
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any


def _result_status(result: Dict[str, Any]) -> str:
    return (
        result.get("status")
        or ("PASS" if result.get("passed") else "FAIL" if result.get("passed") is False else "INCONCLUSIVE")
    )


# Provider P-cards: short labels for the hand-off cards in
# the gap-routing framework. Used to add an explainer next to the route chip
# in the Coverage Gaps table.
_PROVIDER_ROUTE_LABEL = {
    "P1": "Rule author — rule definition / check type",
    "P2": "External data provider — zoning, plot boundary, etc.",
    "P3": "BIM manager — geometry / export issue",
    "P4": "BIM manager — data tagging / enrichment",
    "P6": "Model author — upstream readiness (IDS / clash)",
    "P7": "Simulation provider — performance evidence",
    "P8": "Rule author — check type package",
    "P10": "Reviewer / compliance officer — human decision",
    "P1/P8": "Rule author — rule definition or check type package",
}


def _humanise(snake: Any) -> str:
    """Convert a snake_case / lowercase identifier into a human-friendly label.

    Examples: ``"fire_egress"`` → ``"Fire Egress"``, ``"BIM_manager"`` →
    ``"BIM Manager"``, ``"data_tagging_gap"`` → ``"Data tagging gap"``.
    Acronyms ALL-CAPS in the source (BIM, NBC) are preserved verbatim.
    """
    if snake is None:
        return ""
    text = str(snake).replace("_", " ").strip()
    if not text:
        return ""
    words = text.split()
    out = []
    for i, w in enumerate(words):
        if w.isupper() and len(w) <= 4:  # short ALL-CAPS likely an acronym
            out.append(w)
        elif i == 0:
            out.append(w[:1].upper() + w[1:].lower())
        else:
            out.append(w.lower())
    return " ".join(out)


def _format_route_length_m(value: Any) -> str:
    """Format a route length (in metres) for display.

    Returns ``"—"`` (em-dash) when the value is missing — otherwise
    ``"{value} m"``. Avoids confusing strings like ``"None m"`` when no
    route analyses were performed.
    """
    if value is None:
        return "—"
    try:
        return f"{float(value):.2f} m"
    except (TypeError, ValueError):
        return f"{value} m"


def _build_rule_summaries(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counts: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"pass": 0, "fail": 0, "inconclusive": 0, "total": 0}
    )
    for r in results:
        rid = r.get("rule_id", "unknown")
        counts[rid]["total"] += 1
        status = _result_status(r)
        if status == "PASS":
            counts[rid]["pass"] += 1
        elif status == "FAIL":
            counts[rid]["fail"] += 1
        else:
            counts[rid]["inconclusive"] += 1
    return [
        {
            "rule_id": rid,
            "total_checked": v["total"],
            "pass_count": v["pass"],
            "fail_count": v["fail"],
            "inconclusive_count": v["inconclusive"],
        }
        for rid, v in sorted(counts.items())
    ]


def _build_model_summary(
    results: List[Dict[str, Any]],
    rules: List[Dict[str, Any]] | None = None,
    elements: List[Any] | None = None,
) -> Dict[str, Any]:
    """Compute the report's model summary along the four named axes.

    Aggregate-scope rows (class/model) feed only ``aggregate_counts``
    and are excluded from the pair/element axes so no count mixes axes.
    """
    pair_counts = {
        "pass": 0, "fail": 0, "inconclusive": 0,
        "human_required": 0, "not_applicable": 0, "unsupported": 0,
    }
    aggregate_counts = {
        "pass": 0, "fail": 0, "inconclusive": 0,
        "human_required": 0, "not_applicable": 0, "unsupported": 0,
    }
    waiver_breakdown = {
        "open_fails": 0, "waived_fails": 0,
        "invalid_waivers": 0, "superseded_waivers": 0,
    }
    human_reason_breakdown: Dict[str, int] = {}
    rule_status_seen: Dict[str, set] = {}
    aggregate_only_rule_ids: set = set()
    element_status_seen: Dict[str, set] = {}
    element_ids = set()
    rule_ids = set()

    for r in results:
        eid = r.get("element_id")
        if eid:
            element_ids.add(eid)
        rid = r.get("rule_id")
        if rid:
            rule_ids.add(rid)
        status = _result_status(r)
        scope = r.get("scope", "element") or "element"
        if scope in ("class", "model") and rid:
            aggregate_only_rule_ids.add(rid)
        bucket = {
            "PASS": "pass", "FAIL": "fail", "INCONCLUSIVE": "inconclusive",
            "HUMAN_REQUIRED": "human_required",
            "NOT_APPLICABLE": "not_applicable",
            "UNSUPPORTED": "unsupported",
        }.get(status, "inconclusive")

        if scope in ("class", "model"):
            aggregate_counts[bucket] += 1
            continue

        pair_counts[bucket] += 1
        if status == "FAIL":
            if r.get("waiver_state") == "applied":
                waiver_breakdown["waived_fails"] += 1
            else:
                waiver_breakdown["open_fails"] += 1
        if r.get("waiver_state") == "invalid":
            waiver_breakdown["invalid_waivers"] += 1
        elif r.get("waiver_state") == "superseded":
            waiver_breakdown["superseded_waivers"] += 1
        hr = r.get("human_reason")
        if hr:
            human_reason_breakdown[hr] = human_reason_breakdown.get(hr, 0) + 1
        if rid:
            rule_status_seen.setdefault(rid, set()).add(status)
        if eid:
            element_status_seen.setdefault(eid, set()).add(status)

    checked = pair_counts["pass"] + pair_counts["fail"]
    compliance_score = round(pair_counts["pass"] / checked, 4) if checked else 0.0

    total_elements = len(elements) if elements is not None else len(element_ids)
    total_rules = len(rules) if rules is not None else len(rule_ids)

    # Rule axis: a rule is "evaluated" if it produced ≥1 per-element verdict.
    # If its only output is a class/model-scope marker it counts as
    # "not applicable" (the selector matched nothing in this model).
    # Anything else in the pack is truly "skipped" (no result at all).
    evaluated = set(rule_status_seen.keys())
    not_applicable_rule_ids = aggregate_only_rule_ids - evaluated
    rule_counts = {
        "evaluated": len(evaluated),
        "not_applicable": len(not_applicable_rule_ids),
        "any_fail": sum(1 for v in rule_status_seen.values() if "FAIL" in v),
        "any_human_required": sum(
            1 for v in rule_status_seen.values() if "HUMAN_REQUIRED" in v
        ),
    }
    if rules:
        all_ids = {r.get("id") for r in rules if isinstance(r, dict) and r.get("id")}
        rule_counts["skipped"] = len(all_ids - evaluated - not_applicable_rule_ids)
    rule_axis_score = (
        round((rule_counts["evaluated"] - rule_counts["any_fail"]) / rule_counts["evaluated"], 4)
        if rule_counts["evaluated"] else 0.0
    )

    # Element axis
    element_counts = {
        "touched": len(element_status_seen),
        "untouched": max((total_elements or 0) - len(element_status_seen), 0),
        "any_fail": sum(1 for v in element_status_seen.values() if "FAIL" in v),
        "any_human_required": sum(
            1 for v in element_status_seen.values() if "HUMAN_REQUIRED" in v
        ),
    }

    return {
        "total_elements": total_elements,
        "total_rules": total_rules,
        "elements_with_results": len(element_ids),
        "rules_with_results": len(rule_ids),
        # Pair axis (the historical totals - preserved for backward compat)
        "pass_count": pair_counts["pass"],
        "fail_count": pair_counts["fail"],
        "inconclusive_count": pair_counts["inconclusive"],
        "human_required_count": pair_counts["human_required"],
        "not_applicable_count": pair_counts["not_applicable"],
        "unsupported_count": pair_counts["unsupported"],
        "compliance_score": compliance_score,
        # Dimensional summary
        "pair_counts": dict(pair_counts),
        "rule_counts": rule_counts,
        "rule_axis_score": rule_axis_score,
        "element_counts": element_counts,
        "aggregate_counts": dict(aggregate_counts),
        "waiver_breakdown": dict(waiver_breakdown),
        "human_reason_breakdown": dict(human_reason_breakdown),
    }


def _build_coverage_matrix(
    results: List[Dict[str, Any]],
    rules: List[Dict[str, Any]] | None = None,
    elements: List[Any] | None = None,
    stage: str | None = None,
) -> Dict[str, Any] | None:
    """Compliance coverage matrix: per-rule × element-population view.

    Reports each rule in the pack (including those that matched no elements),
    the size of its target class in the model, how many elements were selected
    after the full selector, and the 6-status breakdown. Also produces a
    corpus-level rollup and a two-layer checkability summary (per-rule and
    per-element).
    """
    if rules is None or elements is None:
        return None

    try:
        from .data_models import STAGE_ORDER
    except Exception:
        STAGE_ORDER = {"concept": 0, "schematic": 1, "submission": 2}
    current_stage_order = STAGE_ORDER.get(stage or "concept", 0)

    # Group results by rule_id
    rule_results: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in results:
        rid = r.get("rule_id")
        if rid:
            rule_results[rid].append(r)

    # Element class counts and a class -> element lookup
    class_counts: Dict[str, int] = defaultdict(int)
    elements_by_class: Dict[str, List[Any]] = defaultdict(list)
    for el in elements:
        cls = getattr(el, "ifc_class", None) or "Unknown"
        class_counts[cls] += 1
        elements_by_class[cls].append(el)

    def _apply_selector(selector: Dict[str, Any]) -> tuple[int, int]:
        """Return (in_model_for_class, selected_after_full_filter)."""
        if not selector:
            return len(elements), len(elements)
        ifc_class = selector.get("ifc_class") or selector.get("type") or "*"
        if ifc_class == "*":
            class_pool = list(elements)
            in_model = len(elements)
        else:
            key = ifc_class.lower()
            class_pool = [
                e for e in elements
                if getattr(e, "ifc_class", "").lower() == key
                or getattr(e, "ifc_class", "") == ifc_class
            ]
            in_model = len(class_pool)
        prop_filter = selector.get("properties", {})
        if not prop_filter:
            return in_model, in_model
        selected = 0
        for e in class_pool:
            props = getattr(e, "properties", {}) or {}
            if all(str(props.get(k, "")).lower() == str(v).lower() for k, v in prop_filter.items()):
                selected += 1
        return in_model, selected

    per_rule: List[Dict[str, Any]] = []
    rules_evaluated = 0
    rules_skipped = 0
    rules_not_applicable = 0
    rules_stage_filtered = 0
    rules_executable = 0  # evaluated and produced ≥1 PASS or FAIL (i.e. not all blocked)

    for rule in rules:
        rid = rule.get("id", "<unnamed>")
        selector = rule.get("selector", {}) or {}
        ifc_class = selector.get("ifc_class") or selector.get("type") or "*"
        prop_filter = selector.get("properties", {}) or {}
        in_model, selected = _apply_selector(selector)

        min_stage = rule.get("min_stage", "concept")
        stage_filtered = STAGE_ORDER.get(min_stage, 0) > current_stage_order

        results_for_rule = rule_results.get(rid, [])
        per_element_results = [
            r for r in results_for_rule
            if (r.get("scope", "element") or "element") == "element"
        ]
        status_counts = {
            "pass": 0, "fail": 0, "inconclusive": 0,
            "human_required": 0, "not_applicable": 0, "unsupported": 0,
        }
        for r in results_for_rule:
            s = _result_status(r)
            if s == "PASS":
                status_counts["pass"] += 1
            elif s == "FAIL":
                status_counts["fail"] += 1
            elif s == "HUMAN_REQUIRED":
                status_counts["human_required"] += 1
            elif s == "NOT_APPLICABLE":
                status_counts["not_applicable"] += 1
            elif s == "UNSUPPORTED":
                status_counts["unsupported"] += 1
            else:
                status_counts["inconclusive"] += 1

        checked = status_counts["pass"] + status_counts["fail"]
        if stage_filtered:
            rule_status = "stage_filtered"
            rules_stage_filtered += 1
        elif not per_element_results:
            # No per-element verdict produced. Either the rule emitted only
            # aggregate (class/model) findings ⇒ "not applicable" to any
            # specific element in this model, or it emitted nothing at all
            # ⇒ truly skipped.
            if results_for_rule:
                rule_status = "not_applicable"
                rules_not_applicable += 1
            else:
                rule_status = "skipped"
                rules_skipped += 1
        else:
            rule_status = "evaluated"
            rules_evaluated += 1
            if checked > 0:
                rules_executable += 1

        per_rule.append({
            "rule_id": rid,
            "title": rule.get("title") or rule.get("description", ""),
            "target_class": ifc_class,
            "property_filter": prop_filter or None,
            "category": rule.get("category", "general"),
            "severity": rule.get("severity", "major"),
            "min_stage": min_stage,
            "source": rule.get("source") or rule.get("clause_ref"),
            "in_model": in_model,
            "selected": selected,
            "result_count": len(results_for_rule),
            "rule_status": rule_status,
            "scope": rule.get("scope", "element"),
            "status_counts": status_counts,
        })

    # Sort: evaluated first, then not-applicable, then skipped, then stage_filtered; within each by rule_id
    status_order = {"evaluated": 0, "not_applicable": 1, "skipped": 2, "stage_filtered": 3}
    per_rule.sort(key=lambda r: (status_order.get(r["rule_status"], 9), r["rule_id"]))

    # Elements touched by any rule (real elements only - class:/model: sentinels
    # are aggregate-scope markers, not elements in the model).
    touched_ids = {
        r["element_id"] for r in results
        if r.get("element_id")
        and not str(r["element_id"]).startswith(("class:", "model:"))
    }
    elements_touched = len(touched_ids)

    # Two-layer checkability
    active_rules = len(rules) - rules_stage_filtered
    per_rule_checkability = (
        round(rules_executable / active_rules, 4) if active_rules else 0.0
    )
    total_pairs = len(results)
    checkable_pairs = sum(
        1 for r in results if _result_status(r) in ("PASS", "FAIL")
    )
    per_element_checkability = (
        round(checkable_pairs / total_pairs, 4) if total_pairs else 0.0
    )

    return {
        "stage": stage,
        "corpus_summary": {
            "rules_in_pack": len(rules),
            "rules_active_at_stage": active_rules,
            "rules_evaluated": rules_evaluated,
            "rules_executable": rules_executable,
            "rules_not_applicable": rules_not_applicable,
            "rules_skipped": rules_skipped,
            "rules_stage_filtered": rules_stage_filtered,
            "elements_in_model": len(elements),
            "elements_touched": elements_touched,
            "elements_untouched": len(elements) - elements_touched,
            "element_class_counts": dict(sorted(class_counts.items())),
            "rule_element_pairs": total_pairs,
            "checkable_pairs": checkable_pairs,
        },
        "two_layer_checkability": {
            "per_rule_layer": {
                "executable_rules": rules_executable,
                "active_rules": active_rules,
                "ratio": per_rule_checkability,
            },
            "per_element_layer": {
                "checkable_pairs": checkable_pairs,
                "total_pairs": total_pairs,
                "ratio": per_element_checkability,
            },
        },
        "per_rule": per_rule,
    }


def _build_category_summaries(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counts: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {
            "pass": 0, "fail": 0, "inconclusive": 0,
            "human_required": 0, "not_applicable": 0, "unsupported": 0, "total": 0,
        }
    )
    for r in results:
        category = r.get("category", "general")
        counts[category]["total"] += 1
        status = _result_status(r)
        if status == "PASS":
            counts[category]["pass"] += 1
        elif status == "FAIL":
            counts[category]["fail"] += 1
        elif status == "HUMAN_REQUIRED":
            counts[category]["human_required"] += 1
        elif status == "NOT_APPLICABLE":
            counts[category]["not_applicable"] += 1
        elif status == "UNSUPPORTED":
            counts[category]["unsupported"] += 1
        else:
            counts[category]["inconclusive"] += 1
    return [
        {
            "category": category,
            "total_checked": values["total"],
            "pass_count": values["pass"],
            "fail_count": values["fail"],
            "inconclusive_count": values["inconclusive"],
            "human_required_count": values["human_required"],
            "not_applicable_count": values["not_applicable"],
            "unsupported_count": values["unsupported"],
        }
        for category, values in sorted(counts.items())
    ]


def _build_severity_summaries(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counts = Counter(r.get("severity", "major") for r in results)
    return [
        {"severity": severity, "count": count}
        for severity, count in sorted(counts.items(), key=lambda item: item[0])
    ]


def _top_failed_rules(results: List[Dict[str, Any]], limit: int = 10) -> List[Dict[str, Any]]:
    counts = Counter(r.get("rule_id", "unknown") for r in results if _result_status(r) == "FAIL")
    return [{"rule_id": rid, "fail_count": count} for rid, count in counts.most_common(limit)]


def _is_aggregate_anchor(eid: Any) -> bool:
    """Sentinel detection: class/model-scope results use class:<key> / model:."""
    if not isinstance(eid, str):
        return False
    return eid.startswith("class:") or eid == "model:" or eid.startswith("model:")


def _top_failed_elements(results: List[Dict[str, Any]], limit: int = 10) -> List[Dict[str, Any]]:
    # top_failed_elements is element-scope only; filter aggregate
    # sentinels (class:/model:) out - they are not "failed elements".
    counts = Counter(
        r.get("element_id", "unknown") for r in results
        if _result_status(r) == "FAIL"
        and r.get("scope", "element") == "element"
        and not _is_aggregate_anchor(r.get("element_id"))
    )
    return [{"element_id": eid, "fail_count": count} for eid, count in counts.most_common(limit)]


def _display_model_name(metadata: Dict[str, Any]) -> str:
    model_name = metadata.get("model_name")
    if model_name:
        return str(model_name)
    model_path = metadata.get("model_path")
    if model_path:
        return Path(str(model_path)).name
    return str(metadata.get("source") or "Unknown model")


def _pretty_timestamp(iso_value: str | None) -> str:
    if not iso_value:
        return "n/a"
    try:
        dt = datetime.fromisoformat(iso_value)
    except Exception:
        return str(iso_value)
    return dt.strftime("%d %b %Y, %H:%M %Z").strip()


def _build_advanced_insights(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    blocking_results = []
    blocker_counter: Counter[str] = Counter()
    route_results = []
    route_lengths = []
    measurement_counter: Counter[str] = Counter()

    for result in results:
        details = result.get("details", {}) or {}
        measurement_method = details.get("measurement_method")
        if measurement_method:
            measurement_counter[str(measurement_method)] += 1

        blockers = details.get("blocking_elements")
        if isinstance(blockers, list) and blockers:
            blocking_results.append(result)
            blocker_counter.update(str(blocker) for blocker in blockers)

        path = details.get("path")
        if isinstance(path, list) and len(path) >= 2:
            route_results.append(result)
            path_length = details.get("path_length_m") or details.get("approx_distance_m")
            try:
                route_lengths.append(float(path_length))
            except Exception:
                pass

    return {
        "spatial_conflict_count": len(blocking_results),
        "route_analysis_count": len(route_results),
        "unique_blocking_elements": len(blocker_counter),
        "top_blocking_elements": [
            {"element_id": element_id, "impact_count": count}
            for element_id, count in blocker_counter.most_common(10)
        ],
        "route_length_summary_m": {
            "count": len(route_lengths),
            "max": round(max(route_lengths), 3) if route_lengths else None,
            "avg": round(sum(route_lengths) / len(route_lengths), 3) if route_lengths else None,
        },
        "measurement_methods": [
            {"measurement_method": method, "count": count}
            for method, count in measurement_counter.most_common()
        ],
        "flagship_findings": _build_flagship_findings(blocking_results, route_results),
    }


def _build_flagship_findings(
    blocking_results: List[Dict[str, Any]],
    route_results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for result in blocking_results[:5]:
        details = result.get("details", {}) or {}
        findings.append({
            "type": "spatial_conflict",
            "rule_id": result.get("rule_id"),
            "element_id": result.get("element_id"),
            "message": result.get("message"),
            "blocking_elements": details.get("blocking_elements", [])[:6],
        })
    for result in route_results[:5]:
        details = result.get("details", {}) or {}
        findings.append({
            "type": "topology_route",
            "rule_id": result.get("rule_id"),
            "element_id": result.get("element_id"),
            "message": result.get("message"),
            "path": details.get("path", [])[:10],
            "path_length_m": details.get("path_length_m") or details.get("approx_distance_m"),
        })
    return findings


def _build_evidence_summary(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate evidence provenance across all results."""
    geo_tiers: Dict[str, int] = defaultdict(int)
    confidence_labels: Dict[str, int] = defaultdict(int)
    sources: Dict[str, int] = defaultdict(int)
    has_evidence = 0
    for r in results:
        ev = r.get("evidence")
        if ev:
            has_evidence += 1
            gt = ev.get("geometry_tier") or "none"
            geo_tiers[gt] += 1
            cl = ev.get("confidence_label") or "unknown"
            confidence_labels[cl] += 1
            ms = ev.get("measurement_source") or "unknown"
            sources[ms] += 1
    return {
        "results_with_evidence": has_evidence,
        "results_total": len(results),
        "geometry_tier_distribution": dict(geo_tiers),
        "confidence_distribution": dict(confidence_labels),
        "measurement_source_distribution": dict(sources),
    }


def _build_payload(
    results: List[Dict[str, Any]],
    metadata: Dict[str, Any] | None = None,
    coverage_gaps: List[Dict[str, Any]] | None = None,
    clause_ledger: List[Dict[str, Any]] | None = None,
    rules: List[Dict[str, Any]] | None = None,
    elements: List[Any] | None = None,
) -> Dict[str, Any]:
    metadata = dict(metadata or {})
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone()
    advanced_insights = _build_advanced_insights(results)
    # Accept both the new "stage" key and the legacy "mode" key, in that order.
    stage = metadata.get("stage") or metadata.get("mode")
    coverage_matrix = _build_coverage_matrix(results, rules=rules, elements=elements, stage=stage)
    payload = {
        "version": 6,
        "title": "ARC Compliance Report",
        "timestamp_utc": now_utc.isoformat(),
        "timestamp_local": now_local.isoformat(),
        "generated_at_display": _pretty_timestamp(now_local.isoformat()),
        "model_details": {
            "model_name": _display_model_name(metadata),
            "model_path": metadata.get("model_path"),
            "source": metadata.get("source"),
            "stage": metadata.get("stage") or metadata.get("mode"),
            "run_context": metadata.get("run_context") or metadata.get("scope"),
            "rule_preset": metadata.get("rule_preset"),
            "rules_path": metadata.get("rules_path"),
        },
        "metadata": metadata,
        "model_summary": _build_model_summary(results, rules=rules, elements=elements),
        "category_summaries": _build_category_summaries(results),
        "severity_summaries": _build_severity_summaries(results),
        "rule_summaries": _build_rule_summaries(results),
        "top_failed_rules": _top_failed_rules(results),
        "top_failed_elements": _top_failed_elements(results),
        "advanced_insights": advanced_insights,
        "evidence_summary": _build_evidence_summary(results),
        "element_results": results,
    }
    if coverage_matrix is not None:
        payload["coverage_matrix"] = coverage_matrix
    if coverage_gaps is not None:
        payload["coverage_gaps"] = coverage_gaps
    if clause_ledger is not None:
        payload["clause_coverage"] = clause_ledger
    return payload


def generate_json_report(
    results: List[Dict[str, Any]],
    outpath: str,
    metadata: Dict[str, Any] | None = None,
    coverage_gaps: List[Dict[str, Any]] | None = None,
    clause_ledger: List[Dict[str, Any]] | None = None,
    rules: List[Dict[str, Any]] | None = None,
    elements: List[Any] | None = None,
) -> str:
    out = Path(outpath)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = _build_payload(
        results, metadata=metadata,
        coverage_gaps=coverage_gaps, clause_ledger=clause_ledger,
        rules=rules, elements=elements,
    )
    out.write_text(json.dumps(payload, indent=2), encoding="utf8")
    return str(out)


def _status_color(status: str) -> str:
    return {
        "PASS": "#16A34A",
        "FAIL": "#DC2626",
        "INCONCLUSIVE": "#D97706",
        "HUMAN_REQUIRED": "#7C3AED",
        "NOT_APPLICABLE": "#9CA3AF",
        "UNSUPPORTED": "#EA580C",
        "BLOCKER": "#C2410C",
    }.get(status, "#475569")


# Display labels for the six status enum values. The enum stays in UPPER_SNAKE
# (PASS / FAIL / INCONCLUSIVE / HUMAN_REQUIRED / NOT_APPLICABLE / UNSUPPORTED)
# in JSON so integrators see a stable contract; this lookup is for human-facing
# display in HTML, PDF, and the status bar legend.
_STATUS_DISPLAY = {
    "PASS": "Pass",
    "FAIL": "Fail",
    "INCONCLUSIVE": "Inconclusive",
    "HUMAN_REQUIRED": "Needs human review",
    "NOT_APPLICABLE": "Not applicable",
    "UNSUPPORTED": "Unsupported",
}


def _status_display(status: str) -> str:
    return _STATUS_DISPLAY.get(status, status.replace("_", " ").title())


def _html_status_bar(summary: Dict[str, Any]) -> str:
    total = max(
        summary["pass_count"] + summary["fail_count"]
        + summary["inconclusive_count"] + summary.get("human_required_count", 0)
        + summary.get("not_applicable_count", 0) + summary.get("unsupported_count", 0),
        1,
    )
    parts = [
        ("PASS", summary["pass_count"]),
        ("FAIL", summary["fail_count"]),
        ("INCONCLUSIVE", summary["inconclusive_count"]),
        ("HUMAN_REQUIRED", summary.get("human_required_count", 0)),
        ("NOT_APPLICABLE", summary.get("not_applicable_count", 0)),
        ("UNSUPPORTED", summary.get("unsupported_count", 0)),
    ]
    spans = []
    for status, count in parts:
        width = max((count / total) * 100.0, 0.0)
        spans.append(
            f'<div class="status-segment" style="width:{width:.2f}%;background:{_status_color(status)}" '
            f'title="{escape(_status_display(status))}: {count}"></div>'
        )
    return "".join(spans)


def _html_category_rows(category_summaries: List[Dict[str, Any]]) -> str:
    rows = []
    max_fail = max((item["fail_count"] for item in category_summaries), default=1)
    for item in sorted(category_summaries, key=lambda value: value["fail_count"], reverse=True):
        fail_width = (item["fail_count"] / max_fail) * 100.0 if max_fail else 0.0
        rows.append(
            "<tr>"
            f"<td>{escape(str(item['category']))}</td>"
            f"<td>{item['pass_count']}</td>"
            f"<td>{item['fail_count']}</td>"
            f"<td>{item['inconclusive_count']}</td>"
            f"<td><div class=\"mini-bar\"><div class=\"mini-bar-fill\" style=\"width:{fail_width:.2f}%\"></div></div></td>"
            "</tr>"
        )
    return "".join(rows)


def _html_category_breakdown(
    results: List[Dict[str, Any]],
    rules: List[Dict[str, Any]] | None = None,
) -> str:
    """Render Category > Rule > Status as collapsible dropdowns.

    Each rule node lists the GUIDs of the affected elements grouped by status.
    """
    STATUS_ORDER = [
        "PASS", "FAIL", "INCONCLUSIVE",
        "HUMAN_REQUIRED", "NOT_APPLICABLE", "UNSUPPORTED",
    ]

    # rule meta lookup (titles, sources)
    rule_meta: Dict[str, Dict[str, Any]] = {}
    if rules:
        for rule in rules:
            rid = rule.get("id")
            if rid:
                rule_meta[rid] = rule

    # category -> rule_id -> status -> [element_ids]
    by_cat: Dict[str, Dict[str, Dict[str, List[str]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    # category -> rule_id metadata copy (so we can render categories where rules
    # have not produced any results yet - e.g., dormant rules)
    cat_rules_seen: Dict[str, set] = defaultdict(set)

    for r in results:
        cat = r.get("category", "general") or "general"
        rid = r.get("rule_id", "unknown") or "unknown"
        eid = r.get("element_id", "?") or "?"
        status = _result_status(r)
        by_cat[cat][rid][status].append(str(eid))
        cat_rules_seen[cat].add(rid)

    # Also surface dormant rules (in the pack but with zero results) under their
    # category, so the user sees the full set of rules per category.
    if rules:
        for rule in rules:
            rid = rule.get("id")
            cat = rule.get("category", "general") or "general"
            if rid and rid not in cat_rules_seen[cat]:
                by_cat[cat][rid]  # creates an empty entry via defaultdict
                cat_rules_seen[cat].add(rid)

    def _pill(status: str, count: int) -> str:
        color = _status_color(status)
        label = _status_display(status)
        return (
            f"<span class='pill' style='border-color:{color};color:{color};"
            f"background:{color}1A;'>{label}: {count}</span>"
        )

    def _chips(eids: List[str]) -> str:
        visible = eids[:60]
        chips = "".join(
            f"<code class='guid-chip' title='{escape(str(e))}'>{escape(str(e))}</code>"
            for e in visible
        )
        if len(eids) > len(visible):
            chips += f"<span class='muted guid-more'>+{len(eids) - len(visible)} more</span>"
        return chips

    blocks: List[str] = []
    for category in sorted(by_cat.keys()):
        rules_in_cat = by_cat[category]
        # Aggregate counts at category level
        cat_counts = {s: 0 for s in STATUS_ORDER}
        for _, by_status in rules_in_cat.items():
            for s, eids in by_status.items():
                if s in cat_counts:
                    cat_counts[s] += len(eids)
                else:
                    cat_counts["INCONCLUSIVE"] += len(eids)
        cat_total = sum(cat_counts.values())
        cat_pills = " ".join(
            _pill(s, cat_counts[s]) for s in STATUS_ORDER if cat_counts[s] > 0
        ) or "<span class='muted small'>no results yet</span>"

        # Inner rule blocks
        rule_blocks: List[str] = []
        for rid in sorted(rules_in_cat.keys()):
            by_status = rules_in_cat[rid]
            rule_total = sum(len(v) for v in by_status.values())
            meta = rule_meta.get(rid, {}) or {}
            title = meta.get("title") or meta.get("description") or ""
            source = meta.get("source") or meta.get("clause_ref") or ""

            rule_pills = " ".join(
                _pill(s, len(by_status.get(s, [])))
                for s in STATUS_ORDER if by_status.get(s)
            ) or "<span class='muted small'>skipped (no elements matched)</span>"

            status_blocks: List[str] = []
            for s in STATUS_ORDER:
                eids = by_status.get(s, [])
                if not eids:
                    continue
                color = _status_color(s)
                status_blocks.append(
                    "<details class='status-detail'>"
                    f"<summary><span class='status-dot' style='background:{color}'></span>"
                    f"<strong>{escape(_status_display(s))}</strong>"
                    f"<span class='count-badge'>{len(eids)}</span></summary>"
                    f"<div class='guid-list'>{_chips(eids)}</div>"
                    "</details>"
                )
            status_body = "".join(status_blocks) or (
                "<p class='muted small'>This rule produced no results in this run.</p>"
            )

            source_line = (
                f"<div class='rule-source muted small'>{escape(str(source))}</div>"
                if source else ""
            )
            title_line = (
                f" &mdash; <span class='muted'>{escape(str(title))}</span>"
                if title else ""
            )
            rule_blocks.append(
                "<details class='rule-detail'>"
                f"<summary><strong>{escape(str(rid))}</strong>{title_line}"
                f"<span class='count-badge'>{rule_total}</span>"
                f"<span class='pill-row'>{rule_pills}</span></summary>"
                f"<div class='rule-body'>{source_line}{status_body}</div>"
                "</details>"
            )

        blocks.append(
            "<details class='cat-detail' open>"
            f"<summary><strong>{escape(_humanise(category))}</strong>"
            f"<span class='count-badge'>{cat_total}</span>"
            f"<span class='pill-row'>{cat_pills}</span></summary>"
            f"<div class='cat-body'>{''.join(rule_blocks)}</div>"
            "</details>"
        )

    return "".join(blocks) or "<p class='muted'>No category data.</p>"


def _html_failure_cards(results: List[Dict[str, Any]]) -> str:
    failures = [result for result in results if _result_status(result) == "FAIL"]
    if not failures:
        return "<p class=\"muted\">No failures recorded.</p>"

    def _render_card(result: Dict[str, Any]) -> str:
        details = result.get("details", {}) or {}
        scope = result.get("scope", "element") or "element"
        scope_badge = (
            f"<span class='scope-badge scope-{scope}'>[{escape(scope)}]</span>"
            if scope != "element" else ""
        )
        waiver_state = result.get("waiver_state")
        waiver_pill = ""
        if waiver_state:
            waiver_pill = (
                f"<span class='chip chip-{'waived' if waiver_state == 'applied' else waiver_state}' "
                f"style='font-size:10.5px;padding:2px 8px;'>"
                f"waiver: {escape(waiver_state)}</span>"
            )
        blockers = details.get("blocking_elements") or []
        path = details.get("path") or []
        measured = result.get("measured_value")
        expected = result.get("expected_value")
        metric_line = ""
        if measured is not None or expected is not None:
            metric_line = (
                f"<p class=\"detail-line\"><strong>Measured:</strong> {escape(str(measured))} "
                f"<strong>Expected:</strong> {escape(str(expected))} "
                f"<strong>Delta:</strong> {escape(str(result.get('delta')))}</p>"
            )
        blockers_line = ""
        if blockers:
            blockers_line = (
                "<p class=\"detail-line\"><strong>Blocking elements:</strong> "
                f"{escape(', '.join(str(value) for value in blockers[:8]))}</p>"
            )
        path_line = ""
        if path:
            path_line = (
                "<p class=\"detail-line\"><strong>Route path:</strong> "
                f"{escape(' → '.join(str(value) for value in path[:10]))}</p>"
            )
        suggestion = details.get("suggestion") or "No corrective suggestion recorded."
        return (
            "<article class=\"failure-card\">"
            f"<div class=\"failure-header\"><span class=\"badge\" style=\"background:{_status_color('FAIL')}\">FAIL</span>"
            f"{scope_badge}"
            f"<strong>{escape(str(result.get('rule_id', '?')))}</strong>"
            f"{waiver_pill}</div>"
            f"<p class=\"detail-line\"><strong>Element:</strong> {escape(str(result.get('element_id', '?')))}</p>"
            f"<p class=\"detail-line\"><strong>Message:</strong> {escape(str(result.get('message', '')))}</p>"
            f"{metric_line}{blockers_line}{path_line}"
            f"<p class=\"detail-line\"><strong>Fix:</strong> {escape(str(suggestion))}</p>"
            "</article>"
        )

    # Group by rule_id so reports with many failures stay scannable.
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in failures:
        grouped[r.get("rule_id", "?")].append(r)

    # Sort rule groups by count (highest first), then by rule_id for stability.
    sorted_groups = sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0]))

    # Per-rule cap to keep the DOM bounded on very large models.
    PER_RULE_CAP = 50

    blocks: List[str] = []
    for rule_id, rule_failures in sorted_groups:
        shown = rule_failures[:PER_RULE_CAP]
        cards_html = "".join(_render_card(r) for r in shown)
        overflow = ""
        if len(rule_failures) > PER_RULE_CAP:
            overflow = (
                f"<p class=\"muted small\" style=\"margin-top:8px;\">"
                f"Showing first {PER_RULE_CAP} of {len(rule_failures)} failing elements for this rule. "
                f"Full list in <code>compliance_results.json</code>.</p>"
            )
        # Collapse all groups by default - the user scans the summary, then drills in.
        blocks.append(
            "<details class=\"failure-group\" style=\"margin-bottom:10px;\">"
            f"<summary style=\"cursor:pointer;padding:10px 14px;background:#fff5f5;"
            f"border:1px solid #fecaca;border-radius:10px;\">"
            f"<span class=\"badge\" style=\"background:{_status_color('FAIL')};margin-right:8px;\">FAIL</span>"
            f"<strong>{escape(str(rule_id))}</strong>"
            f"<span class=\"count-badge\" style=\"margin-left:10px;\">{len(rule_failures)}</span>"
            f"</summary>"
            f"<div class=\"failure-grid\" style=\"margin-top:10px;\">{cards_html}</div>"
            f"{overflow}"
            "</details>"
        )

    header = (
        f"<p class=\"muted small\" style=\"margin-bottom:10px;\">"
        f"{len(failures)} failures across {len(grouped)} rule(s). Click a rule to expand.</p>"
    )
    return header + "".join(blocks)


def _html_flagship_cards(flagship_findings: List[Dict[str, Any]]) -> str:
    cards = []
    for item in flagship_findings:
        blockers = item.get("blocking_elements") or []
        path = item.get("path") or []
        blocker_line = ""
        if blockers:
            blocker_line = (
                "<p class=\"muted\">Blockers: "
                f"{escape(', '.join(str(value) for value in blockers))}</p>"
            )
        path_line = ""
        if path:
            path_line = (
                "<p class=\"muted\">Path: "
                f"{escape(' -> '.join(str(value) for value in path))}</p>"
            )
        cards.append(
            "<article class=\"insight-card\">"
            f"<div class=\"insight-type\">{escape(str(item['type']).replace('_', ' ').title())}</div>"
            f"<h4>{escape(str(item.get('rule_id') or 'Unnamed rule'))}</h4>"
            f"<p>{escape(str(item.get('message') or ''))}</p>"
            f"<p class=\"muted\">Element: {escape(str(item.get('element_id') or 'n/a'))}</p>"
            f"{blocker_line}"
            f"{path_line}"
            "</article>"
        )
    return "".join(cards) or "<p class=\"muted\">No spatial or topological showcase findings were captured in this run.</p>"


def _html_dimensional_summary(summary: Dict[str, Any]) -> str:
    """Render the four-axis dimensional summary as a card grid."""
    pair = summary.get("pair_counts") or {}
    rule = summary.get("rule_counts") or {}
    element = summary.get("element_counts") or {}
    aggregate = summary.get("aggregate_counts") or {}

    def _row(label: str, value: Any, hint: str = "") -> str:
        sub = f"<div class='axis-hint'>{escape(hint)}</div>" if hint else ""
        return (
            f"<div class='axis-row'>"
            f"<span class='axis-label'>{escape(label)}</span>"
            f"<span class='axis-value'>{escape(str(value))}</span>"
            f"{sub}</div>"
        )

    def _axis_card(title: str, hint: str, rows_html: str, total: int) -> str:
        return (
            f"<article class='card span-3 axis-card'>"
            f"<h3>{escape(title)}</h3>"
            f"<p class='muted small'>{escape(hint)}</p>"
            f"<div class='axis-total'>{total}</div>"
            f"<div class='axis-rows'>{rows_html}</div>"
            "</article>"
        )

    pair_rows = _row("Pass", pair.get("pass", 0)) + _row("Fail", pair.get("fail", 0)) + \
        _row("Inconclusive", pair.get("inconclusive", 0)) + _row("Needs human review", pair.get("human_required", 0)) + \
        _row("Not applicable", pair.get("not_applicable", 0)) + _row("Unsupported", pair.get("unsupported", 0))
    pair_total = sum(pair.values()) if pair else 0

    rule_rows = _row("Evaluated", rule.get("evaluated", 0)) + \
        _row("Not applicable", rule.get("not_applicable", 0)) + \
        _row("Skipped", rule.get("skipped", 0)) + \
        _row("Any Fail", rule.get("any_fail", 0)) + \
        _row("Any needs human review", rule.get("any_human_required", 0))
    rule_total = rule.get("evaluated", 0) + rule.get("not_applicable", 0) + rule.get("skipped", 0)

    element_rows = _row("Checked", element.get("touched", 0)) + _row("Not checked", element.get("untouched", 0)) + \
        _row("Any Fail", element.get("any_fail", 0)) + _row("Any needs human review", element.get("any_human_required", 0))
    element_total = element.get("touched", 0) + element.get("untouched", 0)

    aggregate_rows = _row("Pass", aggregate.get("pass", 0)) + _row("Fail", aggregate.get("fail", 0)) + \
        _row("Inconclusive", aggregate.get("inconclusive", 0)) + _row("Not applicable", aggregate.get("not_applicable", 0))
    aggregate_total = sum(aggregate.values()) if aggregate else 0

    return (
        _axis_card("By rule × element", "One row per (rule, element) pair — the historical totals.", pair_rows, pair_total) +
        _axis_card("By rule", "One row per rule in the pack.", rule_rows, rule_total) +
        _axis_card("By element", "One row per element in the model.", element_rows, element_total) +
        _axis_card("Class & model findings", "Findings that apply at the class or whole-model level, not to one specific element.", aggregate_rows, aggregate_total)
    )


def _html_waiver_section(summary: Dict[str, Any], results: List[Dict[str, Any]]) -> str:
    """Render the waiver breakdown and per-waiver detail cards."""
    wb = summary.get("waiver_breakdown") or {}
    open_fails = wb.get("open_fails", 0)
    waived = wb.get("waived_fails", 0)
    invalid = wb.get("invalid_waivers", 0)
    superseded = wb.get("superseded_waivers", 0)

    if not any((waived, invalid, superseded)) and open_fails == 0:
        return (
            "<article class='card span-12'>"
            "<h2>Waivers and Deviations</h2>"
            "<p class='muted'>No waivers attached in this run.</p>"
            "</article>"
        )

    chip_grid = (
        f"<div class='waiver-chips'>"
        f"<div class='chip chip-open'><span class='chip-num'>{open_fails}</span>FAIL (open)</div>"
        f"<div class='chip chip-waived'><span class='chip-num'>{waived}</span>FAIL (waived)</div>"
        f"<div class='chip chip-invalid' title='No longer in force — past expiry date, rule version changed, occasion exhausted, or identity mismatch.'><span class='chip-num'>{invalid}</span>Waiver invalid</div>"
        f"<div class='chip chip-superseded' title='Underlying check now passes, so the waiver no longer applies.'><span class='chip-num'>{superseded}</span>Waiver superseded</div>"
        "</div>"
    )

    cards = []
    for r in results:
        w = r.get("waiver")
        if not isinstance(w, dict):
            continue
        state = r.get("waiver_state") or "unknown"
        invalidation_reason = r.get("waiver_invalidation_reason")
        state_class = {
            "applied": "applied", "invalid": "invalid", "superseded": "superseded",
        }.get(state, "other")
        state_display = state.upper()
        invalidation_line = (
            f"<p class='detail-line'><strong>Reason no longer in force:</strong> "
            f"{escape(str(invalidation_reason))}</p>" if invalidation_reason else ""
        )
        conditions = w.get("conditions") or []
        cond_line = (
            f"<p class='detail-line'><strong>Conditions:</strong> "
            f"{escape('; '.join(str(c) for c in conditions))}</p>" if conditions else ""
        )
        expiry = w.get("expires_at")
        expiry_line = (
            f"<p class='detail-line'><strong>Expires:</strong> {escape(str(expiry))}</p>"
            if expiry else ""
        )
        cards.append(
            f"<article class='waiver-card waiver-{state_class}'>"
            f"<div class='waiver-header'>"
            f"<span class='waiver-state'>{escape(state_display)}</span>"
            f"<strong>{escape(str(r.get('rule_id', '?')))}</strong>"
            f"<span class='muted small'>· {escape(str(r.get('element_id', '?')))}</span>"
            "</div>"
            f"<p class='detail-line'><strong>Granted by:</strong> {escape(str(w.get('granted_by', '')))}</p>"
            f"<p class='detail-line'><strong>Authority:</strong> {escape(str(w.get('authority_basis', '')))}</p>"
            f"<p class='detail-line'><strong>Rationale:</strong> {escape(str(w.get('rationale', '')))}</p>"
            f"<p class='detail-line'><strong>Occasion:</strong> {escape(str(w.get('occasion', '')))}</p>"
            f"{expiry_line}{cond_line}{invalidation_line}"
            "</article>"
        )

    cards_html = "".join(cards) or "<p class='muted'>No detailed waiver records to render.</p>"
    return (
        "<article class='card span-12'>"
        "<h2>Waivers and Deviations</h2>"
        "<p class='muted small'>Waivers are accepted deviations attached to FAIL results. "
        "The status never flips — a waived FAIL stays FAIL, with the waiver shown as evidence. "
        "An <strong>invalid</strong> waiver is no longer in force (past its expiry date, rule version changed, occasion exhausted, or identity mismatch). "
        "A <strong>superseded</strong> waiver is one whose underlying check now passes, so the waiver no longer applies.</p>"
        f"{chip_grid}"
        f"<div class='waiver-grid'>{cards_html}</div>"
        "</article>"
    )


def _html_human_reason_section(summary: Dict[str, Any]) -> str:
    """Render the human-reason breakdown — informational only."""
    breakdown = summary.get("human_reason_breakdown") or {}
    if not breakdown:
        return ""
    rows = "".join(
        f"<tr><td>{escape(str(k))}</td><td>{int(v)}</td></tr>"
        for k, v in sorted(breakdown.items(), key=lambda kv: -kv[1])
    )
    return (
        "<article class='card span-6'>"
        "<h2>Human-Required Breakdown by Reason</h2>"
        "<p class='muted small'>Informational only — counts are not additive with the status totals.</p>"
        "<table class='data-table'>"
        "<thead><tr><th>Reason</th><th>Count</th></tr></thead>"
        f"<tbody>{rows}</tbody>"
        "</table>"
        "</article>"
    )


def _html_coverage_gap_rows(coverage_gaps: List[Dict[str, Any]]) -> str:
    if not coverage_gaps:
        return "<tr><td colspan='7' class='muted'>No coverage gaps recorded.</td></tr>"
    rows = []
    for g in coverage_gaps:
        affected = list(g.get("affected_elements") or [])
        if affected:
            visible = affected[:12]
            chips = "".join(
                f"<code class='guid-chip' title='{escape(str(e))}'>{escape(str(e))}</code>"
                for e in visible
            )
            if len(affected) > len(visible):
                chips += (
                    f"<span class='muted guid-more'>+{len(affected) - len(visible)} more</span>"
                )
            guid_cell = f"<div class='guid-list'>{chips}</div>"
        else:
            guid_cell = "<span class='muted'>&mdash;</span>"
        provider = g.get("provider_route") or ""
        provider_label = _PROVIDER_ROUTE_LABEL.get(provider, "")
        provider_cell = (
            f"<code class='route-chip' title='{escape(provider_label)}'>{escape(str(provider))}</code>"
            f"<div class='muted small'>{escape(provider_label)}</div>"
            if provider_label else
            f"<code class='route-chip'>{escape(str(provider))}</code>"
        )
        rows.append(
            "<tr>"
            f"<td>{escape(str(g.get('rule_id', '')))}</td>"
            f"<td>{escape(_humanise(g.get('gap_type', '')))}</td>"
            f"<td>{len(affected)}</td>"
            f"<td>{guid_cell}</td>"
            f"<td>{escape(str(g.get('suggested_action', '')))}</td>"
            f"<td>{escape(_humanise(g.get('responsible_actor', '')))}</td>"
            f"<td>{provider_cell}</td>"
            "</tr>"
        )
    return "".join(rows)


def _html_coverage_matrix_section(matrix: Dict[str, Any] | None) -> str:
    """Render the per-rule coverage matrix + two-layer checkability summary."""
    if not matrix:
        return ""
    cs = matrix.get("corpus_summary", {})
    tl = matrix.get("two_layer_checkability", {})
    per_rule_layer = tl.get("per_rule_layer", {})
    per_element_layer = tl.get("per_element_layer", {})

    def _pct(ratio: float) -> str:
        return f"{ratio * 100:.0f}%" if ratio else "0%"

    cards = (
        f'<div class="metric" title="Total rules loaded for this run.">'
        f'<div class="label">Rules in pack</div>'
        f'<div class="value">{cs.get("rules_in_pack", 0)}</div></div>'
        f'<div class="metric" title="Rules that produced at least one per-element verdict.">'
        f'<div class="label">Rules evaluated</div>'
        f'<div class="value">{cs.get("rules_evaluated", 0)}</div></div>'
        f'<div class="metric" title="Rules whose selector matched no elements in this model — they only emitted a class- or model-scope marker.">'
        f'<div class="label">Rules not applicable</div>'
        f'<div class="value">{cs.get("rules_not_applicable", 0)}</div></div>'
        f'<div class="metric" title="Rules that produced no result of any kind in this run.">'
        f'<div class="label">Rules skipped</div>'
        f'<div class="value">{cs.get("rules_skipped", 0)}</div></div>'
        f'<div class="metric" title="Distinct elements the rule pack evaluated, out of total elements in the model.">'
        f'<div class="label">Elements checked</div>'
        f'<div class="value">{cs.get("elements_touched", 0)} / {cs.get("elements_in_model", 0)}</div></div>'
    )

    layer_line = (
        f'<p class="detail-line"><strong>Per-rule checkability:</strong> '
        f'{per_rule_layer.get("executable_rules", 0)} of '
        f'{per_rule_layer.get("active_rules", 0)} active rules executable '
        f'({_pct(per_rule_layer.get("ratio", 0))}). '
        f'<strong>Per-element checkability:</strong> '
        f'{per_element_layer.get("checkable_pairs", 0)} of '
        f'{per_element_layer.get("total_pairs", 0)} rule–element pairs fully checkable '
        f'({_pct(per_element_layer.get("ratio", 0))}).</p>'
    )

    rows = []
    for r in matrix.get("per_rule", []):
        sc = r.get("status_counts", {})
        target = escape(str(r.get("target_class", "*")))
        prop = r.get("property_filter")
        if prop:
            target += " · " + escape(", ".join(f"{k}={v}" for k, v in prop.items()))
        rule_state = r.get("rule_status")
        status_label = {
            "evaluated": "evaluated",
            "not_applicable": "not applicable",
            "skipped": "skipped",
            "stage_filtered": "stage-filtered",
        }.get(rule_state, rule_state or "")
        state_titles = {
            "evaluated":
                "Rule ran on at least one real element and produced per-element verdicts.",
            "not_applicable":
                "Rule's selector matched no elements in this model — no per-element verdicts. "
                "Any count in the status columns below is a single class- or model-level marker, not per-element verdicts.",
            "skipped":
                "Rule produced no result of any kind in this run.",
            "stage_filtered":
                "Rule is deferred until a later design stage and was not run.",
        }
        state_title = state_titles.get(rule_state, "")
        # Scope marker - class/model-scope rules render with a visible badge
        # so a reviewer cannot mistake them for per-element findings.
        scope = r.get("scope") or "element"
        scope_badge = (
            f"<span class='scope-badge scope-{scope}'>[{escape(scope)}]</span> "
            if scope != "element" else ""
        )

        in_model = r.get("in_model", 0)
        selected = r.get("selected", 0)
        # Show "Selected" as "N / In-model" when a property filter narrowed
        # the population. Highlight the drop visually so a row like
        # "146 -> 0" doesn't read as "145 elements went missing".
        if prop and in_model and selected < in_model:
            drop_note = ""
            if selected == 0:
                target_class_name = escape(str(r.get("target_class", "*")))
                prop_text = escape(str(prop))
                drop_note = (
                    "<br/><span class='muted small' "
                    f"title='No element of class {target_class_name} matched the property filter {prop_text}.'>"
                    "filter matched none</span>"
                )
            selected_cell = (
                f"<td title='{selected} of {in_model} matched the full selector "
                f"(class + property filter).' style='color:#92400E;font-weight:600;'>"
                f"{selected} / {in_model}{drop_note}</td>"
            )
        else:
            selected_cell = f"<td>{selected}</td>"

        # When the rule is not_applicable but emitted a class- or model-level
        # marker, dim those marker counts and italicise so the reviewer doesn't
        # read them as per-element verdicts.
        is_aggregate_marker = rule_state == "not_applicable" and any(sc.values())
        if is_aggregate_marker:
            def _cell(n: int, key: str) -> str:
                if n == 0:
                    return f"<td>{n}</td>"
                return (
                    f"<td style='font-style:italic;color:#92400E;' "
                    f"title='Class/model-level marker — not a per-element verdict.'>"
                    f"{n}<sup>ᵃ</sup></td>"
                )
        else:
            def _cell(n: int, key: str) -> str:
                return f"<td>{n}</td>"

        rows.append(
            "<tr>"
            f"<td>{scope_badge}<strong>{escape(str(r.get('rule_id', '')))}</strong><br/>"
            f"<span style='color:#6b7280;font-size:12px'>{escape(str(r.get('source') or ''))}</span></td>"
            f"<td>{target}</td>"
            f"<td>{in_model}</td>"
            f"{selected_cell}"
            f"{_cell(sc.get('pass', 0), 'pass')}"
            f"{_cell(sc.get('fail', 0), 'fail')}"
            f"{_cell(sc.get('inconclusive', 0), 'inconclusive')}"
            f"{_cell(sc.get('human_required', 0), 'human_required')}"
            f"{_cell(sc.get('unsupported', 0), 'unsupported')}"
            f"<td title='{escape(state_title)}'>{escape(status_label)}</td>"
            "</tr>"
        )

    glossary = (
        '<details class="muted small" open style="margin-top:14px;">'
        '<summary style="cursor:pointer;font-weight:600;">How to read this table</summary>'
        '<p style="margin:8px 0 4px;">Each row shows a single rule and how it fared against this model. The columns trace what happened, left to right:</p>'
        '<ol style="margin:4px 0 8px 18px;line-height:1.55;">'
        '<li><strong>In model</strong> — count of elements of the rule&#39;s target class present in the IFC '
        '(e.g. 146 <code>IfcSpace</code> entities).</li>'
        '<li><strong>Selected</strong> — of those, how many also matched the rule&#39;s property filter '
        '(e.g. <code>SpaceType=Corridor</code>). When this drops below "In model", the cell is highlighted — '
        'the difference is filtered out by the property filter, not missing.</li>'
        '<li><strong>Pass / Fail / Inc / Hum / Uns</strong> — what verdict the rule gave each <em>Selected</em> element. '
        'When <strong>State</strong> is <em>not applicable</em>, any non-zero number here is a single class- or model-level '
        'marker (rendered <span style="color:#92400E;font-style:italic;">italic with <sup>ᵃ</sup></span>), not per-element verdicts.</li>'
        '<li><strong>State</strong> — whether the rule actually ran on real elements:</li>'
        '</ol>'
        '<ul style="margin:0 0 0 36px;line-height:1.55;">'
        '<li><strong>evaluated</strong> — produced at least one per-element verdict.</li>'
        '<li><strong>not applicable</strong> — selector matched nothing (In model or Selected was 0). '
        'A single class-level marker may be emitted to flag a data tagging gap.</li>'
        '<li><strong>skipped</strong> — produced no result at all this run.</li>'
        '<li><strong>stage-filtered</strong> — deferred until a later design stage, not run now.</li>'
        '</ul>'
        '<p style="margin:8px 0 0;">The four headline metric cards above (Rules in pack / evaluated / not applicable / skipped) '
        'are the rule-axis rollup of the State column. <strong>Elements checked</strong> is the count of distinct elements '
        'the rule pack actually verdicted.</p>'
        '</details>'
    )
    return (
        '<article class="card span-12">'
        '<h2>Compliance Coverage Matrix</h2>'
        f'<div class="metrics">{cards}</div>'
        f'{layer_line}'
        '<table class="data-table">'
        '<thead><tr>'
        '<th title="Rule identifier">Rule</th>'
        '<th title="Whether the rule ran on real elements in this model">State</th>'
        '<th title="Target class of the rule">Target</th>'
        '<th title="Elements of the target class in the model">In model</th>'
        '<th title="Elements after full selector (incl. property filter)">Selected</th>'
        '<th title="Compliant">Pass</th>'
        '<th title="Non-compliant">Fail</th>'
        '<th title="Inconclusive — verdict could not be reached, usually due to missing data">Inc</th>'
        '<th title="Needs human review">Hum</th>'
        '<th title="Unsupported — check type or geometry tier not implemented in this build">Uns</th>'
        '</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table>'
        f'{glossary}'
        '</article>'
    )


def _html_clause_coverage_rows(clause_ledger: List[Dict[str, Any]]) -> str:
    if not clause_ledger:
        return "<tr><td colspan='4' class='muted'>No clause coverage data.</td></tr>"
    rows = []
    for cl in clause_ledger:
        automatable = "Yes" if cl.get("automatable") else "No"
        rule_ids = ", ".join(cl.get("rule_ids") or []) or "—"
        rationale = cl.get("rationale") or ""
        rows.append(
            "<tr>"
            f"<td>{escape(str(cl.get('clause_ref', '')))}</td>"
            f"<td>{escape(rule_ids)}</td>"
            f"<td>{escape(automatable)}</td>"
            f"<td>{escape(rationale)}</td>"
            "</tr>"
        )
    return "".join(rows)


_STATUS_COLOURS_SVG = {
    "PASS": "#16A34A",
    "FAIL": "#DC2626",
    "INCONCLUSIVE": "#D97706",
    "HUMAN_REQUIRED": "#7C3AED",
    "NOT_APPLICABLE": "#9CA3AF",
    "UNSUPPORTED": "#EA580C",
}


def _html_status_distribution_chart(summary: Dict[str, Any]) -> str:
    """Stacked-bar SVG of pair-axis status counts."""
    pair = summary.get("pair_counts") or {}
    order = ["PASS", "FAIL", "INCONCLUSIVE", "HUMAN_REQUIRED", "NOT_APPLICABLE", "UNSUPPORTED"]
    counts = {k: int(pair.get(k.lower(), 0)) for k in order}
    total = sum(counts.values())
    if total == 0:
        return ""
    width, height = 760, 56
    x = 0.0
    segs = []
    for k in order:
        v = counts[k]
        if v == 0:
            continue
        w = (v / total) * width
        colour = _STATUS_COLOURS_SVG.get(k, "#94A3B8")
        segs.append(
            f'<g><rect x="{x:.1f}" y="0" width="{w:.1f}" height="{height}" '
            f'fill="{colour}"><title>{k} {v}</title></rect>'
            + (
                f'<text x="{x + w / 2:.1f}" y="{height / 2 + 4}" '
                f'text-anchor="middle" font-family="Segoe UI, Arial" '
                f'font-size="12" fill="#ffffff" font-weight="600">{v}</text>'
                if w > 28 else ''
            )
            + '</g>'
        )
        x += w
    legend = " &nbsp; ".join(
        f'<span style="display:inline-flex;align-items:center;gap:6px;">'
        f'<span style="display:inline-block;width:10px;height:10px;background:{_STATUS_COLOURS_SVG[k]};border-radius:2px;"></span>'
        f'{k.replace("_", " ").title()} ({counts[k]})</span>'
        for k in order if counts[k] > 0
    )
    return (
        '<article class="card span-12">'
        '<h2>Result distribution</h2>'
        '<p class="muted small">Pair-axis counts across the rule pack and model. '
        'One block per status, width proportional to count.</p>'
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        'preserveAspectRatio="none" role="img" aria-label="Result distribution bar">'
        f'{"".join(segs)}</svg>'
        f'<div class="small" style="margin-top:10px;">{legend}</div>'
        '</article>'
    )


def _html_routing_flow_chart(
    summary: Dict[str, Any],
    coverage_gaps: List[Dict[str, Any]],
) -> str:
    """Flow visualisation: statuses on the left flow to provider routes on the right."""
    pair = summary.get("pair_counts") or {}
    provider_counts: Dict[str, int] = {}
    for g in coverage_gaps:
        p = g.get("provider_route") or "unrouted"
        n = len(g.get("affected_elements") or []) or 1
        provider_counts[p] = provider_counts.get(p, 0) + n

    hr_total = int(pair.get("human_required", 0))
    if hr_total > 0:
        provider_counts["P10"] = provider_counts.get("P10", 0) + hr_total

    if not provider_counts:
        return ""

    width, height = 760, 240
    left_x, right_x = 60, width - 60 - 130
    box_w = 130
    statuses = [
        ("INCONCLUSIVE", int(pair.get("inconclusive", 0))),
        ("HUMAN_REQUIRED", hr_total),
    ]
    statuses = [s for s in statuses if s[1] > 0]
    if not statuses:
        return ""

    n_left = len(statuses)
    left_h = (height - 20) / max(n_left, 1)
    left_positions = []
    for i, (st, n) in enumerate(statuses):
        cy = 10 + i * left_h + left_h / 2
        left_positions.append((st, n, cy))

    providers_sorted = sorted(provider_counts.items(), key=lambda kv: -kv[1])
    n_right = len(providers_sorted)
    right_h = (height - 20) / max(n_right, 1)
    right_positions = []
    for i, (prov, n) in enumerate(providers_sorted):
        cy = 10 + i * right_h + right_h / 2
        right_positions.append((prov, n, cy))

    actor_for_provider: Dict[str, str] = {}
    for g in coverage_gaps:
        p = g.get("provider_route") or "unrouted"
        actor_for_provider.setdefault(p, g.get("responsible_actor") or "")
    if hr_total > 0:
        actor_for_provider.setdefault("P10", "reviewer")

    inc_total = int(pair.get("inconclusive", 0))
    inc_provider_counts: Dict[str, int] = {}
    for g in coverage_gaps:
        gt = g.get("gap_type") or ""
        if gt in ("data_missing", "geometry_limited", "external_missing",
                  "data_tagging_gap", "readiness_issue", "performance_gap"):
            p = g.get("provider_route") or "unrouted"
            inc_provider_counts[p] = inc_provider_counts.get(p, 0) + len(g.get("affected_elements") or [])

    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        'role="img" aria-label="Routing flow">'
    ]

    def _link(x1, y1, x2, y2, count, total_for_status, colour):
        if total_for_status == 0:
            return ""
        thickness = max(2.0, 28.0 * (count / total_for_status))
        return (
            f'<path d="M {x1},{y1} C {(x1+x2)/2},{y1} {(x1+x2)/2},{y2} {x2},{y2}" '
            f'stroke="{colour}" stroke-width="{thickness:.1f}" fill="none" '
            f'stroke-opacity="0.55"><title>{count} findings</title></path>'
        )

    inc_y = next((y for s, n, y in left_positions if s == "INCONCLUSIVE"), None)
    hr_y = next((y for s, n, y in left_positions if s == "HUMAN_REQUIRED"), None)

    for prov, n, cy in right_positions:
        if inc_y is not None and inc_provider_counts.get(prov, 0) > 0:
            parts.append(_link(left_x + box_w, inc_y, right_x, cy,
                               inc_provider_counts[prov], inc_total,
                               _STATUS_COLOURS_SVG["INCONCLUSIVE"]))
        if hr_y is not None and prov == "P10" and hr_total > 0:
            parts.append(_link(left_x + box_w, hr_y, right_x, cy,
                               hr_total, hr_total,
                               _STATUS_COLOURS_SVG["HUMAN_REQUIRED"]))

    for st, n, cy in left_positions:
        colour = _STATUS_COLOURS_SVG.get(st, "#94A3B8")
        parts.append(
            f'<rect x="{left_x}" y="{cy - 22}" width="{box_w}" height="44" '
            f'fill="{colour}" rx="8" />'
            f'<text x="{left_x + box_w / 2}" y="{cy - 4}" text-anchor="middle" '
            f'fill="#ffffff" font-family="Segoe UI, Arial" font-size="12" font-weight="600">'
            f'{st.replace("_", " ").title()}</text>'
            f'<text x="{left_x + box_w / 2}" y="{cy + 14}" text-anchor="middle" '
            f'fill="#ffffff" font-family="Segoe UI, Arial" font-size="13" font-weight="700">'
            f'{n}</text>'
        )

    for prov, n, cy in right_positions:
        actor = actor_for_provider.get(prov, "")
        parts.append(
            f'<rect x="{right_x}" y="{cy - 22}" width="{box_w}" height="44" '
            f'fill="#0F766E" rx="8" />'
            f'<text x="{right_x + box_w / 2}" y="{cy - 4}" text-anchor="middle" '
            f'fill="#ffffff" font-family="Segoe UI, Arial" font-size="12" font-weight="600">'
            f'{escape(prov)}</text>'
            f'<text x="{right_x + box_w / 2}" y="{cy + 14}" text-anchor="middle" '
            f'fill="#ccfbf1" font-family="Segoe UI, Arial" font-size="11">'
            f'{escape(actor)[:18]}</text>'
        )

    parts.append('</svg>')
    return (
        '<article class="card span-12">'
        '<h2>Routing flow</h2>'
        '<p class="muted small">Where non-checkable findings get routed. Link thickness is '
        'proportional to the number of findings on each path; left blocks are statuses, '
        'right blocks are provider categories (P-cards) with their actor label.</p>'
        + "".join(parts) +
        '</article>'
    )


def generate_html_report(
    results: List[Dict[str, Any]],
    outpath: str,
    metadata: Dict[str, Any] | None = None,
    coverage_gaps: List[Dict[str, Any]] | None = None,
    clause_ledger: List[Dict[str, Any]] | None = None,
    rules: List[Dict[str, Any]] | None = None,
    elements: List[Any] | None = None,
) -> str:
    out = Path(outpath)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = _build_payload(results, metadata=metadata,
                             coverage_gaps=coverage_gaps, clause_ledger=clause_ledger,
                             rules=rules, elements=elements)
    summary = payload["model_summary"]
    model_details = payload["model_details"]
    advanced = payload["advanced_insights"]

    # Two-layer checkability - split per-rule vs per-element so they can be
    # surfaced as separate hero tiles. Fall back to a derived per-element
    # ratio when the coverage matrix is unavailable (e.g., no rule pack
    # supplied), and report per-rule as n/a in that case.
    coverage_matrix = payload.get("coverage_matrix") or {}
    two_layer = coverage_matrix.get("two_layer_checkability") or {}
    per_rule_layer = two_layer.get("per_rule_layer") or {}
    per_element_layer = two_layer.get("per_element_layer") or {}

    per_rule_exec = per_rule_layer.get("executable_rules", 0)
    per_rule_active = per_rule_layer.get("active_rules", 0)
    per_rule_ratio = per_rule_layer.get("ratio")

    per_element_checkable = per_element_layer.get("checkable_pairs")
    per_element_total = per_element_layer.get("total_pairs")
    per_element_ratio = per_element_layer.get("ratio")

    # Fallback for per-element when no coverage matrix was built.
    if per_element_checkable is None:
        per_element_checkable = summary["pass_count"] + summary["fail_count"]
    if per_element_total is None:
        per_element_total = (
            summary["pass_count"] + summary["fail_count"]
            + summary["inconclusive_count"] + summary.get("human_required_count", 0)
            + summary.get("not_applicable_count", 0) + summary.get("unsupported_count", 0)
        )
    if per_element_ratio is None:
        per_element_ratio = (
            per_element_checkable / per_element_total if per_element_total else 0.0
        )

    def _fmt_ratio(r):
        if r is None:
            return "n/a"
        try:
            return f"{float(r) * 100:.1f}%"
        except Exception:
            return "n/a"

    per_rule_ratio_pct = _fmt_ratio(per_rule_ratio) if per_rule_active else "n/a"
    per_element_ratio_pct = _fmt_ratio(per_element_ratio)

    top_rule_items = "".join(
        f"<li><strong>{escape(str(item['rule_id']))}</strong><span>{item['fail_count']} failures</span></li>"
        for item in payload["top_failed_rules"]
    ) or "<li><strong>No failing rules</strong><span>0 failures</span></li>"
    top_element_items = "".join(
        f"<li><strong>{escape(str(item['element_id']))}</strong><span>{item['fail_count']} failures</span></li>"
        for item in payload["top_failed_elements"]
    ) or "<li><strong>No failing elements</strong><span>0 failures</span></li>"
    blocker_items = "".join(
        f"<li><strong>{escape(str(item['element_id']))}</strong><span>{item['impact_count']} impacts</span></li>"
        for item in advanced["top_blocking_elements"]
    ) or "<li><strong>No blocker elements recorded</strong><span>0 impacts</span></li>"
    flagship_cards = _html_flagship_cards(advanced["flagship_findings"])

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>ARC Compliance Report</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root {{
      --bg: #f3f4f6;
      --surface: #ffffff;
      --ink: #111827;
      --muted: #6b7280;
      --line: #dbe2ea;
      --accent: #0f766e;
      --accent-soft: #ccfbf1;
      --danger-soft: #fee2e2;
      --shadow: 0 12px 30px rgba(15, 23, 42, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Arial, sans-serif;
      background: linear-gradient(180deg, #eff6ff 0%, var(--bg) 22%, #eef2ff 100%);
      color: var(--ink);
    }}
    .page {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 28px;
    }}
    .hero {{
      background: linear-gradient(135deg, #ffffff 0%, #f8fafc 48%, #ecfeff 100%);
      border: 1px solid var(--line);
      border-radius: 24px;
      padding: 28px;
      box-shadow: var(--shadow);
    }}
    .hero h1 {{
      margin: 0 0 8px;
      font-size: 34px;
      line-height: 1.1;
    }}
    .hero p {{
      margin: 4px 0;
      color: var(--muted);
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 18px;
      margin-top: 20px;
    }}
    .card {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 20px;
      padding: 20px;
      box-shadow: var(--shadow);
    }}
    .span-12 {{ grid-column: span 12; }}
    .span-8 {{ grid-column: span 8; }}
    .span-6 {{ grid-column: span 6; }}
    .span-4 {{ grid-column: span 4; }}
    .span-3 {{ grid-column: span 3; }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 14px;
      margin-top: 18px;
    }}
    .metric {{
      background: #f8fafc;
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 14px;
    }}
    .metric .label {{
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .metric .value {{
      margin-top: 8px;
      font-size: 28px;
      font-weight: 700;
    }}
    .metric .metric-sub {{
      margin-top: 6px;
      font-size: 12px;
      color: var(--muted);
      line-height: 1.4;
    }}
    .small {{ font-size: 12px; }}
    /* GUID chips */
    .guid-list {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      max-width: 100%;
    }}
    .guid-chip {{
      display: inline-block;
      font-family: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
      font-size: 11.5px;
      padding: 2px 6px;
      border-radius: 6px;
      background: #f1f5f9;
      border: 1px solid #e2e8f0;
      color: #0f172a;
      max-width: 280px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      vertical-align: middle;
    }}
    .guid-more {{
      font-size: 12px;
      padding: 2px 6px;
      align-self: center;
    }}
    /* Pills + count badges for category tree */
    .pill {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 11.5px;
      font-weight: 600;
      border: 1px solid transparent;
      letter-spacing: 0.02em;
    }}
    .pill-row {{
      display: inline-flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-left: 10px;
      vertical-align: middle;
    }}
    .count-badge {{
      display: inline-block;
      margin-left: 10px;
      padding: 1px 8px;
      border-radius: 999px;
      background: #e2e8f0;
      color: #0f172a;
      font-size: 11.5px;
      font-weight: 700;
      vertical-align: middle;
    }}
    .status-dot {{
      display: inline-block;
      width: 9px;
      height: 9px;
      border-radius: 999px;
      margin-right: 8px;
      vertical-align: middle;
    }}
    /* Category > Rule > Status dropdown tree */
    .category-tree details {{
      background: #ffffff;
      border: 1px solid var(--line);
      border-radius: 12px;
      margin-bottom: 10px;
      overflow: hidden;
    }}
    .category-tree details summary {{
      list-style: none;
      cursor: pointer;
      padding: 10px 14px;
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 6px;
      background: #f8fafc;
      user-select: none;
    }}
    .category-tree details summary::-webkit-details-marker {{ display: none; }}
    .category-tree details summary::before {{
      content: "▸";
      display: inline-block;
      width: 14px;
      color: var(--muted);
      transition: transform 0.15s ease;
    }}
    .category-tree details[open] > summary::before {{
      transform: rotate(90deg);
    }}
    .category-tree .cat-detail > summary {{
      background: linear-gradient(90deg, #ecfeff 0%, #f8fafc 100%);
      font-size: 15px;
    }}
    .category-tree .cat-body {{
      padding: 10px 14px 14px 14px;
    }}
    .category-tree .rule-detail {{
      margin-top: 8px;
      border: 1px solid #e5e7eb;
    }}
    .category-tree .rule-detail > summary {{
      background: #ffffff;
      font-size: 13.5px;
    }}
    .category-tree .rule-body {{
      padding: 10px 14px 12px 14px;
      background: #fafbfc;
    }}
    .category-tree .rule-source {{
      margin-bottom: 8px;
    }}
    .category-tree .status-detail {{
      margin-top: 6px;
      border: 1px solid #eef2f7;
      background: #ffffff;
    }}
    .category-tree .status-detail > summary {{
      background: #ffffff;
      font-size: 13px;
      padding: 8px 12px;
    }}
    .category-tree .status-detail > .guid-list {{
      padding: 10px 12px 12px 12px;
    }}
    /* Coverage gaps table: keep GUID cell from blowing out the row */
    .coverage-gaps-table td .guid-list {{
      max-height: 120px;
      overflow-y: auto;
    }}
    .status-bar {{
      margin-top: 16px;
      display: flex;
      height: 18px;
      background: #e5e7eb;
      border-radius: 999px;
      overflow: hidden;
    }}
    .status-segment {{ height: 100%; }}
    .legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: 12px;
      color: var(--muted);
      font-size: 13px;
    }}
    .legend span::before {{
      content: "";
      display: inline-block;
      width: 10px;
      height: 10px;
      border-radius: 999px;
      margin-right: 6px;
      vertical-align: middle;
      background: currentColor;
    }}
    .meta-table, .data-table {{
      width: 100%;
      border-collapse: collapse;
    }}
    .meta-table td, .data-table td, .data-table th {{
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }}
    .meta-table td:first-child {{
      width: 160px;
      color: var(--muted);
      font-weight: 600;
    }}
    .data-table th {{
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
      background: #f8fafc;
    }}
    .mini-bar {{
      width: 100%;
      height: 8px;
      background: #e5e7eb;
      border-radius: 999px;
      overflow: hidden;
    }}
    .mini-bar-fill {{
      height: 100%;
      background: linear-gradient(90deg, #f59e0b, #dc2626);
      border-radius: 999px;
    }}
    .list-card ul {{
      list-style: none;
      padding: 0;
      margin: 0;
    }}
    .list-card li {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 0;
      border-bottom: 1px solid var(--line);
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 56px;
      padding: 4px 10px;
      border-radius: 999px;
      color: white;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.04em;
    }}
    .insight-grid, .failure-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 14px;
    }}
    .insight-card, .failure-card {{
      background: #fcfcfd;
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 16px;
    }}
    .insight-type {{
      color: var(--accent);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .failure-header {{
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 8px;
    }}
    .detail-line {{
      margin: 7px 0;
      line-height: 1.5;
    }}
    .muted {{
      color: var(--muted);
    }}
    /* Dimensional axis cards */
    .axis-card {{
      background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
    }}
    .axis-card h3 {{ margin: 0 0 4px; font-size: 14px; }}
    .axis-total {{
      font-size: 28px;
      font-weight: 700;
      margin: 8px 0 6px;
    }}
    .axis-rows {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 2px 8px;
      font-size: 12px;
    }}
    .axis-row {{
      display: contents;
    }}
    .axis-row .axis-label {{ color: var(--muted); }}
    .axis-row .axis-value {{ font-weight: 600; text-align: right; }}
    /* Waiver section */
    .waiver-chips {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin: 12px 0;
    }}
    .chip {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 12px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: #ffffff;
      font-size: 12px;
      font-weight: 600;
    }}
    .chip-num {{
      display: inline-block;
      min-width: 22px;
      text-align: center;
      padding: 1px 6px;
      border-radius: 999px;
      background: #e2e8f0;
      color: #0f172a;
    }}
    .chip-open    {{ border-color: #DC2626; color: #DC2626; }}
    .chip-waived  {{ border-color: #7C3AED; color: #7C3AED; }}
    .chip-invalid    {{ border-color: #D97706; color: #D97706; }}
    .chip-superseded {{ border-color: #9CA3AF; color: #6B7280; }}
    .waiver-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 12px;
      margin-top: 8px;
    }}
    .waiver-card {{
      background: #ffffff;
      border: 1px solid var(--line);
      border-left: 4px solid #9CA3AF;
      border-radius: 12px;
      padding: 12px 14px;
    }}
    .waiver-applied    {{ border-left-color: #7C3AED; }}
    .waiver-invalid    {{ border-left-color: #D97706; }}
    .waiver-superseded {{ border-left-color: #9CA3AF; }}
    .waiver-header {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 8px;
      flex-wrap: wrap;
    }}
    .waiver-state {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      background: #f1f5f9;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.05em;
    }}
    .waiver-applied .waiver-state    {{ background: #ede9fe; color: #7C3AED; }}
    .waiver-invalid .waiver-state    {{ background: #fef3c7; color: #92400E; }}
    .waiver-superseded .waiver-state {{ background: #f3f4f6; color: #6b7280; }}
    /* Routing route chip & scope badge */
    .route-chip {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 6px;
      background: #ecfeff;
      color: #0e7490;
      font-family: ui-monospace, monospace;
      font-size: 11.5px;
      font-weight: 700;
    }}
    .scope-badge {{
      display: inline-block;
      padding: 1px 6px;
      border-radius: 6px;
      font-size: 10.5px;
      font-weight: 700;
      letter-spacing: 0.04em;
      margin-right: 4px;
      vertical-align: middle;
    }}
    .scope-class {{ background: #dbeafe; color: #1e40af; }}
    .scope-model {{ background: #fae8ff; color: #86198f; }}
    @media (max-width: 1180px) {{
      .metrics {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
      .axis-card {{ grid-column: span 6; }}
    }}
    @media (max-width: 900px) {{
      .span-8, .span-6, .span-4, .span-3 {{ grid-column: span 12; }}
      .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <section class="hero">
      <h1>ARC Compliance Report</h1>
      <p>{escape(str(model_details.get("model_name") or "Unknown model"))}</p>
      <p>Generated {escape(str(payload["generated_at_display"]))} | Source: {escape(str(model_details.get("source") or "n/a"))}</p>
      <div class="metrics">
        <div class="metric" title="Compliance Score = PASS / (PASS + FAIL). Inconclusive, Human-required, Not-applicable and Unsupported results are excluded from both numerator and denominator.">
          <div class="label">Compliance Score</div>
          <div class="value">{summary["compliance_score"] * 100:.1f}%</div>
          <div class="metric-sub">{summary["pass_count"]} PASS / {summary["pass_count"] + summary["fail_count"]} decided<br/><span class="muted small">= PASS &divide; (PASS + FAIL)</span></div>
        </div>
        <div class="metric"><div class="label">Failing Checks</div><div class="value">{summary["fail_count"]}</div></div>
        <div class="metric" title="Per-rule checkability: of the rules active at the current design stage, the share that produced at least one PASS or FAIL.">
          <div class="label">Per-rule Checkability</div>
          <div class="value">{per_rule_ratio_pct}</div>
          <div class="metric-sub muted small">{per_rule_exec} of {per_rule_active} active rules executable</div>
        </div>
        <div class="metric" title="Per-element checkability: of every rule-element pair evaluated, the share that resolved to PASS or FAIL (the remaining pairs need data, judgement, or are NA/unsupported).">
          <div class="label">Per-element Checkability</div>
          <div class="value">{per_element_ratio_pct}</div>
          <div class="metric-sub muted small">{per_element_checkable} of {per_element_total} rule&ndash;element pairs decided</div>
        </div>
        <div class="metric"><div class="label">Coverage Gaps</div><div class="value">{len(coverage_gaps or [])}</div></div>
      </div>
      <div class="status-bar">{_html_status_bar(summary)}</div>
      <div class="legend">
        <span style="color:{_status_color('PASS')}">{_status_display('PASS')} {summary["pass_count"]}</span>
        <span style="color:{_status_color('FAIL')}">{_status_display('FAIL')} {summary["fail_count"]}</span>
        <span style="color:{_status_color('INCONCLUSIVE')}">{_status_display('INCONCLUSIVE')} {summary["inconclusive_count"]}</span>
        <span style="color:{_status_color('HUMAN_REQUIRED')}">{_status_display('HUMAN_REQUIRED')} {summary.get("human_required_count", 0)}</span>
        <span style="color:{_status_color('NOT_APPLICABLE')}">{_status_display('NOT_APPLICABLE')} {summary.get("not_applicable_count", 0)}</span>
        <span style="color:{_status_color('UNSUPPORTED')}">{_status_display('UNSUPPORTED')} {summary.get("unsupported_count", 0)}</span>
      </div>
    </section>

    <section class="grid">
      {_html_status_distribution_chart(summary)}

      {_html_coverage_matrix_section(payload.get("coverage_matrix"))}

      {_html_routing_flow_chart(summary, coverage_gaps or [])}

      <article class="card span-12">
        <h2>Coverage Gaps</h2>
        <p class="muted small">Every non-checkable case is named here with its responsible actor and resolved provider route (P-card). No silent skips.</p>
        <table class="data-table coverage-gaps-table">
          <thead>
            <tr><th>Rule ID</th><th>Gap Type</th><th>Count</th><th>Affected Element GUIDs</th><th>Suggested Action</th><th>Responsible Actor</th><th>Provider</th></tr>
          </thead>
          <tbody>{_html_coverage_gap_rows(coverage_gaps or [])}</tbody>
        </table>
      </article>

      {_html_human_reason_section(summary)}

      {_html_waiver_section(summary, results)}

      <article class="card span-12 category-breakdown">
        <details open>
          <summary><h2 style="display:inline-block;margin:0;">Category Breakdown</h2></summary>
          <p class="muted small">Rules are grouped by category. Expand a category to see its rules, then expand a rule to see the affected element GUIDs by status.</p>
          <div class="category-tree">{_html_category_breakdown(results, rules=rules)}</div>
        </details>
      </article>

      <article class="card span-12 axis-grid">
        <details>
          <summary><h2 style="display:inline-block;margin:0;">Findings, four views of the same run</h2></summary>
          <p class="muted small">Counts on four independent axes. No number is the sum of two others by accident. Class- and model-level findings are kept separate from per-element ones.</p>
          <div class="grid">{_html_dimensional_summary(summary)}</div>
        </details>
      </article>

      <article class="card span-12">
        <details>
          <summary><h2 style="display:inline-block;margin:0;">Detailed Failures</h2></summary>
          {_html_failure_cards(results)}
        </details>
      </article>

      <article class="card span-12">
        <details>
          <summary><h2 style="display:inline-block;margin:0;">Top Findings and Blockers</h2></summary>
          <div class="grid">
            <div class="span-4 list-card">
              <h3>Top Failed Rules</h3>
              <ul>{top_rule_items}</ul>
            </div>
            <div class="span-4 list-card">
              <h3>Top Failed Elements</h3>
              <ul>{top_element_items}</ul>
            </div>
            <div class="span-4 list-card">
              <h3>Most Frequent Blockers</h3>
              <ul>{blocker_items}</ul>
            </div>
          </div>
        </details>
      </article>

      <article class="card span-12">
        <details>
          <summary><h2 style="display:inline-block;margin:0;">Flagship Spatial and Topology Findings</h2></summary>
          <div class="insight-grid">{flagship_cards}</div>
        </details>
      </article>

      <article class="card span-12">
        <details>
          <summary><h2 style="display:inline-block;margin:0;">Clause Coverage</h2></summary>
          <table class="data-table">
            <thead>
              <tr><th>Clause Reference</th><th>Rule IDs</th><th>Automatable</th><th>Rationale</th></tr>
            </thead>
            <tbody>{_html_clause_coverage_rows(clause_ledger or [])}</tbody>
          </table>
        </details>
      </article>

      <article class="card span-12">
        <details>
          <summary><h2 style="display:inline-block;margin:0;">Run Metadata</h2></summary>
          <div class="grid">
            <div class="span-6">
              <h3>Model Details</h3>
              <table class="meta-table">
                <tr><td>Model</td><td>{escape(str(model_details.get("model_name") or "n/a"))}</td></tr>
                <tr><td>Stage</td><td>{escape(str(model_details.get("stage") or "n/a"))}</td></tr>
                <tr><td>Run context</td><td>{escape(str(model_details.get("run_context") or "n/a"))}</td></tr>
                <tr><td>Rule Preset</td><td>{escape(str(model_details.get("rule_preset") or "n/a"))}</td></tr>
                <tr><td>Model Path</td><td>{escape(str(model_details.get("model_path") or "n/a"))}</td></tr>
                <tr><td>Rules Path</td><td>{escape(str(model_details.get("rules_path") or "n/a"))}</td></tr>
              </table>
            </div>
            <div class="span-6">
              <h3>Advanced Insights</h3>
              <table class="meta-table">
                <tr><td>Unique blockers</td><td>{advanced["unique_blocking_elements"]}</td></tr>
                <tr><td>Route analyses</td><td>{advanced["route_analysis_count"]}</td></tr>
                <tr><td>Longest route</td><td>{_format_route_length_m(advanced["route_length_summary_m"]["max"])}</td></tr>
                <tr><td>Average route</td><td>{_format_route_length_m(advanced["route_length_summary_m"]["avg"])}</td></tr>
                <tr><td>Measurement methods</td><td>{escape(", ".join(item["measurement_method"] for item in advanced["measurement_methods"][:5]) or "n/a")}</td></tr>
              </table>
            </div>
          </div>
        </details>
      </article>
    </section>
  </main>
</body>
</html>
"""
    out.write_text(html, encoding="utf8")
    return str(out)


def _status_chart(summary: Dict[str, Any]):
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    from reportlab.graphics.shapes import Drawing, String
    from reportlab.lib import colors

    drawing = Drawing(420, 180)
    chart = VerticalBarChart()
    chart.x = 40
    chart.y = 30
    chart.height = 110
    chart.width = 320
    chart.data = [[
        summary["pass_count"],
        summary["fail_count"],
        summary["inconclusive_count"],
        summary.get("human_required_count", 0),
        summary.get("not_applicable_count", 0),
        summary.get("unsupported_count", 0),
    ]]
    chart.categoryAxis.categoryNames = ["PASS", "FAIL", "INCONCL.", "HUMAN", "N/A", "UNSUPP."]
    chart.valueAxis.valueMin = 0
    chart.barWidth = 35
    chart.groupSpacing = 20
    chart.bars[0].fillColor = colors.HexColor("#4CAF50")
    drawing.add(chart)
    drawing.add(String(40, 150, "Status Distribution", fontSize=12, fillColor=colors.HexColor("#1f2937")))
    return drawing


def _category_chart(category_summaries: List[Dict[str, Any]]):
    from reportlab.graphics.charts.barcharts import HorizontalBarChart
    from reportlab.graphics.shapes import Drawing, String
    from reportlab.lib import colors

    top_categories = sorted(category_summaries, key=lambda item: item["fail_count"], reverse=True)[:6]
    if not top_categories:
        return None

    drawing = Drawing(420, 220)
    chart = HorizontalBarChart()
    chart.x = 110
    chart.y = 25
    chart.height = 140
    chart.width = 240
    chart.data = [[item["fail_count"] for item in top_categories]]
    chart.categoryAxis.categoryNames = [item["category"] for item in top_categories]
    chart.valueAxis.valueMin = 0
    chart.barWidth = 16
    chart.groupSpacing = 8
    chart.bars[0].fillColor = colors.HexColor("#E74C3C")
    drawing.add(chart)
    drawing.add(String(20, 185, "Top Failure Categories", fontSize=12, fillColor=colors.HexColor("#1f2937")))
    return drawing


def generate_pdf_report(
    results: List[Dict[str, Any]],
    outpath: str,
    metadata: Dict[str, Any] | None = None,
    coverage_gaps: List[Dict[str, Any]] | None = None,
    clause_ledger: List[Dict[str, Any]] | None = None,
    rules: List[Dict[str, Any]] | None = None,
    elements: List[Any] | None = None,
) -> str:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            SimpleDocTemplate,
            Spacer,
            Paragraph,
            Table,
            TableStyle,
            PageBreak,
        )
    except Exception as exc:
        # reportlab >= 4 imports PIL from reportlab.lib.utils, so a reportlab
        # install without pillow fails here even though `import reportlab`
        # itself succeeds. Report the module that actually went missing.
        raise RuntimeError(
            f"PDF output unavailable: {exc}. "
            "Install both reportlab and pillow to generate PDFs."
        ) from exc

    payload = _build_payload(results, metadata=metadata,
                             coverage_gaps=coverage_gaps, clause_ledger=clause_ledger,
                             rules=rules, elements=elements)
    summary = payload["model_summary"]
    model_details = payload["model_details"]
    category_summaries = payload["category_summaries"]
    top_failed_rules = payload["top_failed_rules"]
    top_failed_elements = payload["top_failed_elements"]
    advanced_insights = payload["advanced_insights"]
    coverage_matrix = payload.get("coverage_matrix") or {}

    out = Path(outpath)
    out.parent.mkdir(parents=True, exist_ok=True)

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="ARC_Title",
        parent=styles["Title"],
        fontSize=24,
        leading=28,
        textColor=colors.HexColor("#111827"),
        spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        name="ARC_Subtitle",
        parent=styles["Normal"],
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#4b5563"),
    ))
    styles.add(ParagraphStyle(
        name="ARC_Section",
        parent=styles["Heading2"],
        fontSize=14,
        leading=18,
        textColor=colors.HexColor("#0f172a"),
        spaceBefore=10,
        spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        name="ARC_Body",
        parent=styles["BodyText"],
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#1f2937"),
    ))

    story = []
    story.append(Paragraph("ARC Compliance Report", styles["ARC_Title"]))
    story.append(Paragraph(
        f"{model_details['model_name']} | Generated {payload['timestamp_local']}",
        styles["ARC_Subtitle"],
    ))
    story.append(Spacer(1, 8))

    overview_table = Table(
        [
            ["Model", model_details["model_name"], "Stage", model_details.get("stage") or "n/a"],
            ["Source", model_details.get("source") or "n/a", "Run context", model_details.get("run_context") or "n/a"],
            ["Model Path", model_details.get("model_path") or "n/a", "Rule Preset", model_details.get("rule_preset") or "n/a"],
            ["Rules Path", model_details.get("rules_path") or "n/a", "Compliance", f"{summary['compliance_score'] * 100:.1f}%"],
        ],
        colWidths=[28 * mm, 72 * mm, 24 * mm, 56 * mm],
    )
    overview_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E2E8F0")),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("PADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(overview_table)
    story.append(Spacer(1, 10))

    story.append(Paragraph("Executive Summary", styles["ARC_Section"]))
    summary_table = Table(
        [
            ["PASS", summary["pass_count"], "FAIL", summary["fail_count"]],
            ["INCONCLUSIVE", summary["inconclusive_count"], "HUMAN_REQUIRED", summary.get("human_required_count", 0)],
            ["NOT_APPLICABLE", summary.get("not_applicable_count", 0), "UNSUPPORTED", summary.get("unsupported_count", 0)],
            ["Total Rules", summary["total_rules"], "Total Elements", summary["total_elements"]],
            ["Coverage Gaps", len(coverage_gaps or []), "", ""],
        ],
        colWidths=[35 * mm, 25 * mm, 35 * mm, 25 * mm],
    )
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E2E8F0")),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("PADDING", (0, 0), (-1, -1), 6),
        ("TEXTCOLOR", (0, 0), (0, 0), colors.HexColor("#16A34A")),
        ("TEXTCOLOR", (2, 0), (2, 0), colors.HexColor("#DC2626")),
        ("TEXTCOLOR", (0, 1), (0, 1), colors.HexColor("#D97706")),
        ("TEXTCOLOR", (2, 1), (2, 1), colors.HexColor("#7C3AED")),
        ("TEXTCOLOR", (0, 2), (0, 2), colors.HexColor("#9CA3AF")),
        ("TEXTCOLOR", (2, 2), (2, 2), colors.HexColor("#EA580C")),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 8))
    story.append(_status_chart(summary))

    # --- Coverage Matrix (lead diagnostic section) ---
    if coverage_matrix:
        tl = coverage_matrix.get("two_layer_checkability", {})
        per_rule_layer = tl.get("per_rule_layer", {})
        per_elem_layer = tl.get("per_element_layer", {})
        story.append(Spacer(1, 10))
        story.append(Paragraph("Compliance Coverage Matrix", styles["ARC_Section"]))
        story.append(Paragraph(
            f"Per-rule checkability: {per_rule_layer.get('executable_rules', 0)} of "
            f"{per_rule_layer.get('active_rules', 0)} active rules executable "
            f"({(per_rule_layer.get('ratio') or 0) * 100:.0f}%). "
            f"Per-element checkability: {per_elem_layer.get('checkable_pairs', 0)} of "
            f"{per_elem_layer.get('total_pairs', 0)} rule&ndash;element pairs fully checkable "
            f"({(per_elem_layer.get('ratio') or 0) * 100:.0f}%).",
            styles["ARC_Subtitle"],
        ))
        matrix_rows = [["Rule", "Target", "In", "Sel", "P", "F", "I", "H", "U", "State"]]
        for r in coverage_matrix.get("per_rule", []):
            sc = r.get("status_counts", {})
            target = str(r.get("target_class", "*"))
            prop = r.get("property_filter")
            if prop:
                target += " · " + ", ".join(f"{k}={v}" for k, v in prop.items())
            matrix_rows.append([
                str(r.get("rule_id", "")),
                target,
                r.get("in_model", 0),
                r.get("selected", 0),
                sc.get("pass", 0),
                sc.get("fail", 0),
                sc.get("inconclusive", 0),
                sc.get("human_required", 0),
                sc.get("unsupported", 0),
                str(r.get("rule_status", "")),
            ])
        matrix_table = Table(
            matrix_rows,
            colWidths=[28 * mm, 38 * mm, 10 * mm, 10 * mm, 8 * mm, 8 * mm, 8 * mm, 8 * mm, 8 * mm, 22 * mm],
        )
        matrix_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E2E8F0")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("PADDING", (0, 0), (-1, -1), 3),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (2, 0), (8, -1), "RIGHT"),
        ]))
        story.append(matrix_table)

    # --- Dimensional Summary ---
    pair = summary.get("pair_counts") or {}
    rule_counts = summary.get("rule_counts") or {}
    element_counts = summary.get("element_counts") or {}
    aggregate = summary.get("aggregate_counts") or {}
    story.append(Spacer(1, 10))
    story.append(Paragraph("Findings — four views of the same run", styles["ARC_Section"]))
    story.append(Paragraph(
        "The same findings counted four different ways; each row uses its own denominator "
        "so no number is the sum of two others by accident. Class- and model-level findings "
        "are kept separate from per-element ones.",
        styles["ARC_Subtitle"],
    ))
    dim_rows = [
        ["View", "Headline counts"],
        ["By rule × element",
         f"Pass {pair.get('pass', 0)} · Fail {pair.get('fail', 0)} · "
         f"Inconclusive {pair.get('inconclusive', 0)} · Needs human review {pair.get('human_required', 0)} · "
         f"Not applicable {pair.get('not_applicable', 0)} · Unsupported {pair.get('unsupported', 0)}"],
        ["By rule",
         f"Evaluated {rule_counts.get('evaluated', 0)} · Skipped {rule_counts.get('skipped', 0)} · "
         f"Any Fail {rule_counts.get('any_fail', 0)} · Any needs human review {rule_counts.get('any_human_required', 0)}"],
        ["By element",
         f"Checked {element_counts.get('touched', 0)} · Not checked {element_counts.get('untouched', 0)} · "
         f"Any Fail {element_counts.get('any_fail', 0)} · Any needs human review {element_counts.get('any_human_required', 0)}"],
        ["Class & model findings",
         f"Pass {aggregate.get('pass', 0)} · Fail {aggregate.get('fail', 0)} · "
         f"Inconclusive {aggregate.get('inconclusive', 0)} · Not applicable {aggregate.get('not_applicable', 0)}"],
    ]
    dim_table = Table(dim_rows, colWidths=[55 * mm, 125 * mm])
    dim_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E2E8F0")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("PADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(dim_table)

    # --- Waiver breakdown ---
    wb = summary.get("waiver_breakdown") or {}
    waiver_results = [r for r in results if r.get("waiver")]
    if any(wb.get(k, 0) for k in ("waived_fails", "invalid_waivers", "superseded_waivers")) or wb.get("open_fails", 0):
        story.append(Spacer(1, 10))
        story.append(Paragraph("Waivers and Deviations", styles["ARC_Section"]))
        story.append(Paragraph(
            "The status never flips — a waived FAIL stays FAIL with the waiver record shown as evidence. "
            "An invalid waiver is no longer in force (past expiry date, rule version changed, occasion exhausted, or identity mismatch). "
            "A superseded waiver is one whose underlying check now passes, so the waiver no longer applies.",
            styles["ARC_Subtitle"],
        ))
        waiver_chip_rows = [
            ["FAIL (open)", wb.get("open_fails", 0),
             "FAIL (waived)", wb.get("waived_fails", 0)],
            ["Waiver invalid", wb.get("invalid_waivers", 0),
             "Waiver superseded", wb.get("superseded_waivers", 0)],
        ]
        waiver_chip_table = Table(waiver_chip_rows, colWidths=[40 * mm, 25 * mm, 40 * mm, 25 * mm])
        waiver_chip_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E2E8F0")),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("PADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(waiver_chip_table)
        if waiver_results:
            story.append(Spacer(1, 6))
            for r in waiver_results[:20]:
                w = r.get("waiver") or {}
                state = (r.get("waiver_state") or "?").upper()
                invalidation_reason = r.get("waiver_invalidation_reason") or ""
                lines = [
                    f"<b>{escape(str(r.get('rule_id', '?')))}</b> "
                    f"({escape(state)}) · Element {escape(str(r.get('element_id', '?')))}",
                    f"Granted by: {escape(str(w.get('granted_by', '')))} | "
                    f"Authority: {escape(str(w.get('authority_basis', '')))}",
                    f"Rationale: {escape(str(w.get('rationale', '')))}",
                    f"Occasion: {escape(str(w.get('occasion', '')))} | "
                    f"Expires: {escape(str(w.get('expires_at', 'n/a')))}",
                ]
                if invalidation_reason:
                    lines.append(f"Reason no longer in force: {escape(str(invalidation_reason))}")
                story.append(Paragraph("<br/>".join(lines), styles["ARC_Body"]))
                story.append(Spacer(1, 4))

    # --- Human-required reason breakdown ---
    hr = summary.get("human_reason_breakdown") or {}
    if hr:
        story.append(Spacer(1, 10))
        story.append(Paragraph("Human-Required Breakdown by Reason", styles["ARC_Section"]))
        story.append(Paragraph(
            "Informational only — not additive with status totals.",
            styles["ARC_Subtitle"],
        ))
        hr_rows = [["Reason", "Count"]] + [
            [k, v] for k, v in sorted(hr.items(), key=lambda kv: -kv[1])
        ]
        hr_table = Table(hr_rows, colWidths=[120 * mm, 30 * mm])
        hr_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDE9FE")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E2E8F0")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("PADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(hr_table)

    story.append(Spacer(1, 8))
    insight_table = Table(
        [
            ["Spatial conflict findings", advanced_insights["spatial_conflict_count"], "Route analyses", advanced_insights["route_analysis_count"]],
            ["Unique blocker elements", advanced_insights["unique_blocking_elements"], "Longest route", _format_route_length_m(advanced_insights["route_length_summary_m"]["max"])],
        ],
        colWidths=[45 * mm, 25 * mm, 35 * mm, 40 * mm],
    )
    insight_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E2E8F0")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("PADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(insight_table)

    category_chart = _category_chart(category_summaries)
    if category_chart is not None:
        story.append(Spacer(1, 8))
        story.append(category_chart)

    story.append(PageBreak())
    story.append(Paragraph("Category Breakdown", styles["ARC_Section"]))
    category_rows = [["Category", "Pass", "Fail", "Inconclusive", "Total"]]
    for item in category_summaries:
        category_rows.append([
            item["category"],
            item["pass_count"],
            item["fail_count"],
            item["inconclusive_count"],
            item["total_checked"],
        ])
    category_table = Table(category_rows, colWidths=[48 * mm, 18 * mm, 18 * mm, 28 * mm, 18 * mm])
    category_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E2E8F0")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("PADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(category_table)
    story.append(Spacer(1, 10))

    story.append(Paragraph("Top Findings", styles["ARC_Section"]))
    top_rule_rows = [["Rule", "Failures"]] + [
        [item["rule_id"], item["fail_count"]] for item in (top_failed_rules or [{"rule_id": "None", "fail_count": 0}])
    ]
    top_rule_table = Table(top_rule_rows, colWidths=[110 * mm, 20 * mm])
    top_rule_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#FDE68A")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#F59E0B")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#FDE68A")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("PADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(top_rule_table)
    story.append(Spacer(1, 8))

    top_element_rows = [["Element", "Failures"]] + [
        [item["element_id"], item["fail_count"]] for item in (top_failed_elements or [{"element_id": "None", "fail_count": 0}])
    ]
    top_element_table = Table(top_element_rows, colWidths=[110 * mm, 20 * mm])
    top_element_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DBEAFE")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#60A5FA")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#BFDBFE")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("PADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(top_element_table)

    story.append(PageBreak())
    story.append(Paragraph("Detailed Failures", styles["ARC_Section"]))
    failures = [r for r in results if _result_status(r) == "FAIL"]
    if not failures:
        story.append(Paragraph("No failures were recorded in this run.", styles["ARC_Body"]))
    else:
        for idx, result in enumerate(failures[:30], start=1):
            details = result.get("details", {}) or {}
            measured = result.get("measured_value")
            expected = result.get("expected_value")
            suggestion = details.get("suggestion") or "No suggestion provided."
            lines = [
                f"<b>{idx}. {result.get('rule_id', '?')}</b> | Element: {result.get('element_id', '?')}",
                f"Status: {result.get('status', 'FAIL')} | Severity: {result.get('severity', 'major')} | Category: {result.get('category', 'general')}",
                f"Message: {result.get('message', '')}",
            ]
            if measured is not None or expected is not None:
                lines.append(f"Measured: {measured} | Expected: {expected} | Delta: {result.get('delta')}")
            lines.append(f"Suggestion: {suggestion}")
            blockers = details.get("blocking_elements")
            if blockers:
                lines.append(f"Blocking Elements: {', '.join(str(v) for v in blockers[:8])}")
            story.append(Paragraph("<br/>".join(lines), styles["ARC_Body"]))
            story.append(Spacer(1, 6))
        if len(failures) > 30:
            story.append(Paragraph(
                f"Report truncated detailed failures to the first 30 items out of {len(failures)} total failures.",
                styles["ARC_Subtitle"],
            ))

    # --- Coverage Gaps ---
    if coverage_gaps:
        story.append(PageBreak())
        story.append(Paragraph("Coverage Gaps", styles["ARC_Section"]))
        gap_rows = [["Rule ID", "Gap Type", "Affected", "Suggested Action", "Actor", "Route"]]
        for g in (coverage_gaps or [])[:40]:
            affected = ", ".join(str(e) for e in (g.get("affected_elements") or [])[:3])
            gap_rows.append([
                g.get("rule_id", ""),
                g.get("gap_type", ""),
                affected,
                g.get("suggested_action", "")[:60],
                g.get("responsible_actor", ""),
                g.get("provider_route", ""),
            ])
        gap_table = Table(gap_rows, colWidths=[26 * mm, 22 * mm, 20 * mm, 54 * mm, 20 * mm, 14 * mm])
        gap_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#FDE68A")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E2E8F0")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("PADDING", (0, 0), (-1, -1), 4),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(gap_table)

    # --- Clause Coverage ---
    if clause_ledger:
        story.append(Spacer(1, 12))
        story.append(Paragraph("Clause Coverage", styles["ARC_Section"]))
        clause_rows = [["Clause Reference", "Rule IDs", "Automatable", "Rationale"]]
        for cl in (clause_ledger or []):
            rule_ids = ", ".join(cl.get("rule_ids") or []) or "\u2014"
            automatable = "Yes" if cl.get("automatable") else "No"
            rationale = (cl.get("rationale") or "")[:80]
            clause_rows.append([cl.get("clause_ref", ""), rule_ids, automatable, rationale])
        clause_table = Table(clause_rows, colWidths=[52 * mm, 32 * mm, 20 * mm, 52 * mm])
        clause_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DBEAFE")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E2E8F0")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("PADDING", (0, 0), (-1, -1), 4),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(clause_table)

    def _on_page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#6B7280"))
        canvas.drawRightString(doc.pagesize[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(out),
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=18 * mm,
        bottomMargin=16 * mm,
    )
    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    return str(out)
