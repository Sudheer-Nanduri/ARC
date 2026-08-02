# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""Rule engine: dependency ordering, JSON check types, and sandboxed Python execution.

API Contract compliance:
- Public APIs never raise exceptions
- Rule failures return RuleResult(status="INCONCLUSIVE")
- Execution honours dependency ordering via Kahn's algorithm

Check types are registered via a plug-in registry.  Built-in types are
registered at module load time (see bottom of file).  External code can
add new types by calling ``register_check_type(name, handler, ...)``.

Built-in JSON check_type values:
    min_area            floor area (AABB x*y) >= min_area
    min_width           minimum horizontal dimension >= min_width
    min_height          AABB z-dimension >= min_height
    max_height          AABB z-dimension <= max_height
    min_dimensions_2d   both horizontal dims satisfy (min_dim1, min_dim2) sorted
    property_min        element property value >= min_value
    property_max        element property value <= max_value
    clearance_zone      expand AABB by padding, check for blocking elements
    turning_circle      verify min inscribed circle fits in element footprint
    ratio               ratio of two properties or geometry measures
    distance_to_nearest min distance to nearest element of target_class
    count_nearby        count elements of target_class within radius; check min/max
"""
from __future__ import annotations

import ast
import math
from typing import Any, Callable, Dict, List, Optional

from .data_models import (
    Element, RuleResult, EvidenceBundle, CoverageGap,
    Contradiction, OverrideRecord, AUTHORITY_HIERARCHY,
    STATUS_PASS, STATUS_FAIL, STATUS_INCONCLUSIVE,
    STATUS_HUMAN_REQUIRED, STATUS_NOT_APPLICABLE, STATUS_UNSUPPORTED,
    _VALID_STATUSES,
    ROUTING_REGISTRY, resolve_route, ROUTE_KIND_GATE, ROUTE_KIND_HUMAN_REASON,
    ROUTE_KIND_CONFIDENCE_DIM,
    WaiverRecord, WAIVER_STATE_APPLIED, WAIVER_STATE_INVALID, WAIVER_STATE_SUPERSEDED,
    WAIVER_INVALIDATION_RULE_VERSION_CHANGED, WAIVER_INVALIDATION_PAST_EXPIRY_DATE,
    WAIVER_INVALIDATION_OCCASION_EXHAUSTED, WAIVER_INVALIDATION_IDENTITY_MISMATCH,
    _now_iso,
)
from .. import __version__ as _arc_version
from .context import Context
from .geo_engine import (
    aabb_floor_area, aabb_min_horizontal_dim, aabb_max_horizontal_dim,
    aabb_height, aabb_dimensions,
    polygon_area, polygon_min_width,
)


FORBIDDEN_BUILTINS = {
    "open", "exec", "eval", "compile", "__import__",
    "globals", "locals", "vars", "input", "breakpoint",
}

def _canonical_rule_version(rule: Dict[str, Any]) -> str:
    """Single source of truth for a rule's version string.

    Used by BOTH evidence bundles and waiver identity matching so the two
    subsystems can never disagree about the same rule's version. Precedence:
    ``rule_version`` (seeded by the pack manifest loader) > ``version``
    (declared on the rule) > "1.0".
    """
    return str(rule.get("rule_version") or rule.get("version") or "1.0")


def _aabb_dict(element: Element) -> Optional[Dict[str, Any]]:
    if element.aabb is None:
        return None
    return {"min": list(element.aabb.min), "max": list(element.aabb.max)}


# ---------------------------------------------------------------------------
# Check-type registry
# ---------------------------------------------------------------------------
# Instead of a hardcoded if/elif chain, check types register themselves in
# this dict.  External code (plugins, future extensions) can add new types
# via ``register_check_type(name, handler, needs_context, needs_geometry)``.
#
# Handler signature:
#   (engine, params, element, common)               -> RuleResult   (no context)
#   (engine, params, element, context, common)       -> RuleResult   (with context)

class _CheckSpec:
    __slots__ = ("handler", "needs_context", "needs_geometry", "scope")
    def __init__(
        self,
        handler: Callable,
        needs_context: bool,
        needs_geometry: bool,
        scope: Optional[str] = None,
    ):
        self.handler = handler
        self.needs_context = needs_context
        self.needs_geometry = needs_geometry
        self.scope = scope  # None = element-scope; "class" / "model" for aggregate handlers

_CHECK_REGISTRY: Dict[str, _CheckSpec] = {}


def register_check_type(
    name: str,
    handler: Callable,
    needs_context: bool = False,
    needs_geometry: bool = True,
    scope: Optional[str] = None,
) -> None:
    """Register a new JSON check_type handler.

    Parameters
    ----------
    name:
        The ``check_type`` string used in JSON rule definitions.
    handler:
        Callable with signature ``(engine, params, element, common)`` or
        ``(engine, params, element, context, common)`` if *needs_context*.
    needs_context:
        True if the handler requires the execution Context (spatial index,
        topology, other elements).
    needs_geometry:
        True if the handler requires a valid AABB on the element.  Set to
        False for pure-property checks.
    """
    _CHECK_REGISTRY[name] = _CheckSpec(handler, needs_context, needs_geometry, scope)


# ---------------------------------------------------------------------------
# RuleEngine
# ---------------------------------------------------------------------------

class RuleEngine:
    def __init__(
        self,
        rules: Optional[List[Dict[str, Any]]] = None,
        regulation_date: Optional[str] = None,
        execution_stage: Optional[str] = None,
    ):
        self.rules: List[Dict[str, Any]] = rules or []
        self._compiled_python: Dict[str, Callable] = {}
        self.coverage_gaps: List[CoverageGap] = []
        self.contradictions: List[Contradiction] = []
        self.override_trail: List[OverrideRecord] = []
        self._regulation_date: Optional[str] = regulation_date
        self._execution_stage: Optional[str] = execution_stage
        self.stage_exit_result: Optional[dict] = None

    @classmethod
    def from_json_dir(cls, path: str) -> "RuleEngine":
        from .rule_loader import load_json_rules
        return cls(load_json_rules(path))

    @classmethod
    def from_rules_dir(cls, path: str) -> "RuleEngine":
        from .rule_loader import load_rules
        return cls(load_rules(path))

    # ------------------------------------------------------------------
    # Topological sort (Kahn's algorithm)
    # ------------------------------------------------------------------

    def _toposort(self) -> List[Dict[str, Any]]:
        id2rule = {r["id"]: r for r in self.rules}
        deps = {rid: set(r.get("depends_on", [])) for rid, r in id2rule.items()}
        for rid, dset in deps.items():
            for d in list(dset):
                if d not in id2rule:
                    raise ValueError(f"Rule '{rid}' depends on unknown rule '{d}'")
        order: List[str] = []
        no_deps = [rid for rid, dset in deps.items() if not dset]
        while no_deps:
            n = no_deps.pop(0)
            order.append(n)
            for m in list(deps.keys()):
                if n in deps.get(m, set()):
                    deps[m].remove(n)
                    if not deps[m]:
                        no_deps.append(m)
            deps.pop(n, None)
        if deps:
            remaining = set(id2rule.keys()) - set(order)
            raise ValueError(f"Dependency cycle among: {remaining}")
        return [id2rule[rid] for rid in order]

    # ------------------------------------------------------------------
    # Element selector
    # ------------------------------------------------------------------

    def _select_elements(self, selector: Dict[str, Any], context: Context) -> List[Element]:
        if not selector:
            return list(context.elements)

        # Match by ifc_class (spec) or legacy type string
        ifc_class = selector.get("ifc_class") or selector.get("type")
        candidates = list(context.elements)
        if ifc_class:
            key = ifc_class.lower()
            candidates = [
                e for e in candidates
                if e.ifc_class.lower() == key or e.ifc_class == ifc_class
            ]

        # Optional property filter
        prop_filter: Dict[str, Any] = selector.get("properties", {})
        if prop_filter:
            filtered = []
            for e in candidates:
                match = all(
                    str(e.properties.get(k, "")).lower() == str(v).lower()
                    for k, v in prop_filter.items()
                )
                if match:
                    filtered.append(e)
            # If nothing matches the property filter, it means there are no elements
            # of this specific type in the model. Return empty to avoid false positives.
            candidates = filtered

        return candidates

    # ------------------------------------------------------------------
    # JSON rule execution
    # ------------------------------------------------------------------

    def _execute_json_rule(
        self, rule: Dict[str, Any], context: Context
    ) -> List[RuleResult]:
        rid = rule.get("id", "<unnamed>")
        selector = rule.get("selector", {})
        params = rule.get("params", {})
        check_type = rule.get("check_type", "")
        severity = rule.get("severity", "major")
        category = rule.get("category", "general")
        scope = rule.get("scope", "element")

        # Backward compat: if check_type is empty but params has min_area
        if not check_type and "min_area" in params:
            check_type = "min_area"

        elements = self._select_elements(selector, context)

        # Dormant-rule split: every rule appears in the result
        # stream. If the selector picked zero elements, emit one aggregate
        # result and stop. Skip this for explicitly model-scope rules - they
        # always want a single aggregate result and the handler will produce
        # it even when the selection is empty.
        if not elements and scope == "element":
            rr = self._emit_dormant_result(rule, context, severity, category)
            self._attach_aggregate_evidence(rr, rule)
            return [rr]

        # Class / model scope: one aggregate result, dispatched to a scope-aware
        # handler. The handler receives the full element list.
        if scope in ("class", "model"):
            rr = self._execute_aggregate_rule(
                rule, check_type, params, elements, severity, category, context,
            )
            self._attach_aggregate_evidence(rr, rule)
            return [rr]

        results: List[RuleResult] = []
        for el in elements:
            rr = self._check_element(rid, check_type, params, el, severity, category, context, rule)
            results.append(rr)

        return results

    # ------------------------------------------------------------------
    # Dormant rule emission
    # ------------------------------------------------------------------

    @staticmethod
    def _selector_target_class(selector: Dict[str, Any]) -> Optional[str]:
        if not selector:
            return None
        return selector.get("ifc_class") or selector.get("type")

    def _attach_aggregate_evidence(self, result: RuleResult, rule: Dict[str, Any]) -> None:
        """Attach a minimal evidence bundle to an aggregate-scope result so
        every JSON rule result keeps the evidence-bundle invariant."""
        if result.evidence is not None:
            return
        result.evidence = EvidenceBundle(
            rule_version=_canonical_rule_version(rule),
            rule_source=rule.get("source", ""),
            element_guid=result.element_id or "",
            geometry_tier=result.geometry_tier or "aggregate",
            measurement_method=result.measurement_method or "aggregate",
            measurement_source=result.measurement_source or "selection",
            confidence_label="counted",
            arc_version=_arc_version,
        )

    def _emit_dormant_result(
        self,
        rule: Dict[str, Any],
        context: Context,
        severity: str,
        category: str,
    ) -> RuleResult:
        """Emit one aggregate-scope result for a rule whose selector matches zero.

        Splits the two semantically-distinct cases:
          - target class absent in the model → NOT_APPLICABLE, no actor
          - class present but property filter narrowed to zero → INCONCLUSIVE
            with ``gap_type = "data_tagging_gap"``, routed to the BIM modeller
        """
        rid = rule.get("id", "<unnamed>")
        selector = rule.get("selector", {}) or {}
        target_class = self._selector_target_class(selector)
        if target_class:
            element_id = f"class:{target_class}"
            class_present = bool(context.elements_by_class(target_class))
        else:
            element_id = "model:"
            class_present = True  # no class filter; treat as model-scope

        prop_filter = selector.get("properties", {}) or {}
        if class_present and prop_filter:
            rr = RuleResult(
                rule_id=rid,
                element_id=element_id,
                status=STATUS_INCONCLUSIVE,
                scope="class" if target_class else "model",
                affected_element_ids=[],
                severity=severity,
                category=category,
                message=(
                    f"Selector matched no elements: {target_class or 'model'} "
                    f"present but no element satisfies {prop_filter}"
                ),
                details={
                    "gate": "data_tagging_gap",
                    "target_class": target_class,
                    "property_filter": prop_filter,
                },
            )
            self._attach_aggregate_evidence(rr, rule)
            return rr

        rr = RuleResult(
            rule_id=rid,
            element_id=element_id,
            status=STATUS_NOT_APPLICABLE,
            scope="class" if target_class else "model",
            affected_element_ids=[],
            severity=severity,
            category=category,
            message=(
                f"No {target_class} in model"
                if target_class else "Rule has no applicable elements"
            ),
            details={"target_class": target_class, "dormant_cause": "class_absent"},
        )
        self._attach_aggregate_evidence(rr, rule)
        return rr

    # ------------------------------------------------------------------
    # Aggregate-scope dispatch
    # ------------------------------------------------------------------

    def _execute_aggregate_rule(
        self,
        rule: Dict[str, Any],
        check_type: str,
        params: Dict[str, Any],
        elements: List[Element],
        severity: str,
        category: str,
        context: Context,
    ) -> RuleResult:
        """Dispatch a class- or model-scope rule via the check-type registry.

        Aggregate handlers register with ``scope="class"`` or ``"model"`` and
        receive the full element list, returning one ``RuleResult`` with
        ``scope`` set and ``affected_element_ids`` populated.
        """
        rid = rule.get("id", "<unnamed>")
        scope = rule.get("scope", "element")
        selector = rule.get("selector", {}) or {}
        target_class = self._selector_target_class(selector)
        if scope == "class" and target_class:
            anchor = f"class:{target_class}"
        elif scope == "model":
            anchor = "model:"
        else:
            anchor = "model:"

        spec = _CHECK_REGISTRY.get(check_type)
        common = dict(
            rule_id=rid,
            element_id=anchor,
            severity=severity,
            category=category,
            scope=scope,
            affected_element_ids=[el.guid for el in elements],
        )
        if spec is None or spec.scope is None:
            return RuleResult(
                **common,
                status=STATUS_UNSUPPORTED,
                message=f"check_type '{check_type}' is not registered as an aggregate handler",
                details={"gate": "unsupported", "check_type": check_type, "scope": scope},
            )
        try:
            if spec.needs_context:
                return spec.handler(self, params, elements, context, common)
            return spec.handler(self, params, elements, common)
        except Exception as exc:
            return RuleResult(
                **common,
                status=STATUS_INCONCLUSIVE,
                message=f"Aggregate check failed: {exc}",
                details={"exception": str(exc), "check_type": check_type},
            )

    # ------------------------------------------------------------------
    # Built-in aggregate check types
    # ------------------------------------------------------------------

    @staticmethod
    def _check_count(_engine, params, elements, common) -> RuleResult:
        min_count = int(params.get("min_count", 1))
        max_count = params.get("max_count")
        n = len(elements)
        ok = n >= min_count and (max_count is None or n <= max_count)
        return RuleResult(
            **common,
            status=STATUS_PASS if ok else STATUS_FAIL,
            passed=ok,
            measured_value=float(n),
            expected_value=float(min_count),
            message=f"count={n} (min={min_count}, max={max_count})",
            details={"count": n, "min_count": min_count, "max_count": max_count},
            measurement_method="aggregate_count",
            measurement_source="selection",
        )

    @staticmethod
    def _check_sum_property(_engine, params, elements, common) -> RuleResult:
        prop = params.get("property", "")
        min_total = params.get("min_total")
        max_total = params.get("max_total")
        total = 0.0
        missing: List[str] = []
        for el in elements:
            v = el.properties.get(prop)
            if v is None:
                missing.append(el.guid)
                continue
            try:
                total += float(v)
            except (TypeError, ValueError):
                missing.append(el.guid)
        if missing and not (min_total is None and max_total is None):
            return RuleResult(
                **common,
                status=STATUS_INCONCLUSIVE,
                message=f"Property {prop!r} missing or non-numeric on {len(missing)} element(s)",
                details={"gate": "data_missing", "missing_property": prop, "missing_on": missing[:20]},
            )
        ok = True
        if min_total is not None:
            ok = ok and total >= float(min_total)
        if max_total is not None:
            ok = ok and total <= float(max_total)
        return RuleResult(
            **common,
            status=STATUS_PASS if ok else STATUS_FAIL,
            passed=ok,
            measured_value=total,
            expected_value=float(min_total) if min_total is not None else None,
            message=f"sum({prop})={total} (min={min_total}, max={max_total})",
            details={"property": prop, "sum": total, "min_total": min_total, "max_total": max_total},
            measurement_method="aggregate_sum",
            measurement_source="property",
        )

    @staticmethod
    def _check_any_pass(_engine, params, elements, common) -> RuleResult:
        # Aggregate condition: at least one element passes a property predicate.
        prop = params.get("property", "")
        equals = params.get("equals")
        n_match = 0
        for el in elements:
            v = el.properties.get(prop)
            if equals is not None and str(v).lower() == str(equals).lower():
                n_match += 1
            elif equals is None and v not in (None, "", False):
                n_match += 1
        passed = n_match >= 1
        return RuleResult(
            **common,
            status=STATUS_PASS if passed else STATUS_FAIL,
            passed=passed,
            measured_value=float(n_match),
            expected_value=1.0,
            message=f"any_pass: {n_match} of {len(elements)} elements match {prop}={equals!r}",
            details={"property": prop, "equals": equals, "matched": n_match, "total": len(elements)},
            measurement_method="aggregate_any_pass",
            measurement_source="property",
        )

    @staticmethod
    def _check_all_pass(_engine, params, elements, common) -> RuleResult:
        prop = params.get("property", "")
        equals = params.get("equals")
        n_match = 0
        for el in elements:
            v = el.properties.get(prop)
            if equals is not None and str(v).lower() == str(equals).lower():
                n_match += 1
            elif equals is None and v not in (None, "", False):
                n_match += 1
        passed = n_match == len(elements) and len(elements) > 0
        return RuleResult(
            **common,
            status=STATUS_PASS if passed else STATUS_FAIL,
            passed=passed,
            measured_value=float(n_match),
            expected_value=float(len(elements)),
            message=f"all_pass: {n_match} of {len(elements)} elements match {prop}={equals!r}",
            details={"property": prop, "equals": equals, "matched": n_match, "total": len(elements)},
            measurement_method="aggregate_all_pass",
            measurement_source="property",
        )

    # ------------------------------------------------------------------
    # Checkability Gate - pre-evaluation classification per (rule, element)
    # ------------------------------------------------------------------

    def _checkability_gate(
        self,
        rule: Dict[str, Any],
        check_type: str,
        el: Element,
        context: Optional[Context],
        common: Dict[str, Any],
    ) -> Optional[RuleResult]:
        """Pre-classify a rule-element pair before running the actual check.

        Returns a RuleResult with an early status if the pair cannot be checked,
        or None if the pair passes all gates and should proceed to evaluation.

        For Python rules, pass check_type="python_rule" — Gate 1 (registry)
        is skipped and Gate 3 uses rule-declared ``needs_geometry`` (default True).
        """
        is_python = (check_type == "python_rule")

        # Gate 1: check type not registered (skip for Python rules)
        spec = _CHECK_REGISTRY.get(check_type)
        if spec is None and not is_python:
            return RuleResult(
                **common,
                status=STATUS_UNSUPPORTED,
                message=f"check_type '{check_type}' not registered",
                details={"gate": "unsupported"},
            )

        # Gate 2: rule requires human judgment
        if rule.get("requires_human_judgment", False):
            # Sub-reason is a registry-keyed string; defaults to
            # "subjective_rule". A rule may override via its own
            # `human_reason` field.
            reason = rule.get("human_reason") or "subjective_rule"
            return RuleResult(
                **common,
                status=STATUS_HUMAN_REQUIRED,
                message=f"Rule requires human judgment",
                details={"gate": "human_required", "human_reason": reason},
                human_reason=reason,
                arrival_path="gate:human_judgment",
            )

        # Gate 3: geometry required but missing
        needs_geom = rule.get("needs_geometry", True) if is_python else (spec.needs_geometry if spec else True)
        if el.aabb is None and needs_geom:
            return RuleResult(
                **common,
                status=STATUS_INCONCLUSIVE,
                message=f"Missing geometry: {el.guid} → cannot evaluate {check_type}",
                details={"gate": "geometry_limited", "missing": "aabb"},
            )

        # Gate 3b: confidence requirement not met
        confidence_req = rule.get("confidence_requirement")
        if confidence_req and isinstance(confidence_req, dict):
            for dim, min_val in confidence_req.items():
                actual = el.confidence.get(dim, 0.0)
                if actual < min_val:
                    return RuleResult(
                        **common,
                        status=STATUS_INCONCLUSIVE,
                        message=f"Confidence too low: {dim}={actual:.2f} < required {min_val:.2f}",
                        details={
                            "gate": "confidence_insufficient",
                            "dimension": dim,
                            "actual": actual,
                            "required": min_val,
                        },
                    )

        # Gate 4: required properties missing
        required_props = rule.get("requires_properties", [])
        if required_props:
            missing = [p for p in required_props if p not in el.properties]
            if missing:
                is_concept = (self._execution_stage == "concept")
                return RuleResult(
                    **common,
                    status=STATUS_INCONCLUSIVE,
                    message=(
                        f"Missing properties (concept stage — advisory): {', '.join(missing)}"
                        if is_concept else
                        f"Missing required properties: {', '.join(missing)}"
                    ),
                    details={
                        "gate": "data_missing",
                        "missing_properties": missing,
                        **({"stage_demoted": True} if is_concept else {}),
                    },
                )

        # Gate 5: required external data missing
        required_external = rule.get("requires_external", [])
        if required_external and context is not None:
            ext = getattr(context, "external_data", {}) or {}
            missing_ext = [e for e in required_external if e not in ext]
            if missing_ext:
                return RuleResult(
                    **common,
                    status=STATUS_INCONCLUSIVE,
                    message=f"Missing external data: {', '.join(missing_ext)}",
                    details={"gate": "external_missing", "missing_external": missing_ext},
                )

        # All gates passed - proceed to check
        return None

    @staticmethod
    def _build_evidence(
        rule: Dict[str, Any],
        el: Element,
        geometry_tier: Optional[str],
        measurement_method: Optional[str],
        measurement_source: Optional[str],
        assumptions: Optional[List[str]] = None,
    ) -> EvidenceBundle:
        """Construct an EvidenceBundle from rule and element context."""
        is_aabb = (measurement_source == "aabb_derived")
        method = measurement_method or ""
        source = measurement_source or ""
        # Default geometry_tier from measurement signals when a rule does not
        # set it explicitly. Most bundled checks operate on AABB geometry, so
        # AABB-derived or python_rule signals map to Tier 1; property-only
        # checks have no geometric tier and report 'property'.
        if not geometry_tier:
            if is_aabb or method.startswith("python_rule") or "aabb" in method.lower() or "bbox" in method.lower():
                geometry_tier = "aabb"
            elif source == "property" or method == "property_lookup":
                geometry_tier = "property"
            else:
                geometry_tier = "unknown"
        return EvidenceBundle(
            rule_version=_canonical_rule_version(rule),
            rule_source=rule.get("source", ""),
            element_guid=el.guid,
            geometry_tier=geometry_tier,
            measurement_method=method,
            measurement_source=source,
            confidence_label=(
                "approximate" if is_aabb or geometry_tier == "aabb"
                # Authored property values are unverified assertions from the
                # model author, not engine measurements - label them honestly.
                else "reported" if source == "property" or geometry_tier == "property"
                else "measured"
            ),
            assumptions=assumptions or (["AABB proxy used; actual geometry may differ"] if is_aabb else []),
            arc_version=_arc_version,
        )

    def _check_element(
        self,
        rule_id: str,
        check_type: str,
        params: Dict[str, Any],
        el: Element,
        severity: str,
        category: str,
        context: Optional[Context] = None,
        rule: Optional[Dict[str, Any]] = None,
    ) -> RuleResult:
        """Dispatch a single JSON check against one element via registry."""
        common = dict(
            rule_id=rule_id,
            element_id=el.guid,
            aabb=_aabb_dict(el),
            severity=severity,
            category=category,
        )

        # Run checkability gate first
        gate_result = self._checkability_gate(
            rule or {}, check_type, el, context, common,
        )
        if gate_result is not None:
            # Evidence-bundle invariant: gate results carry evidence too, so
            # an INCONCLUSIVE/HUMAN_REQUIRED is as auditable as a verdict.
            gate_result.evidence = self._build_evidence(
                rule or {}, el,
                geometry_tier="none",
                measurement_method="checkability_gate",
                measurement_source=str((gate_result.details or {}).get("gate", "gate")),
            )
            return gate_result

        spec = _CHECK_REGISTRY[check_type]  # safe - gate already checked
        try:
            if spec.needs_context:
                result = spec.handler(self, params, el, context, common)
            else:
                result = spec.handler(self, params, el, common)
        except Exception as exc:
            return RuleResult(
                **common,
                status=STATUS_INCONCLUSIVE,
                message=f"Geometry check failed: {exc} → Fix geometry in source model",
                details={"exception": str(exc)},
            )

        # Attach evidence bundle using provenance fields set by the handler
        if result.evidence is None and rule is not None:
            result.evidence = self._build_evidence(
                rule, el,
                geometry_tier=result.geometry_tier,
                measurement_method=result.measurement_method,
                measurement_source=result.measurement_source,
            )

        return result

    # --- Individual check implementations ---

    def _check_min_area(self, params, el, common) -> RuleResult:
        min_area = float(params["min_area"])
        # Property lookup only - no AABB synthesis here, to let polygon tier win
        area, source = self._prop_only(el, "area")
        if area is None:
            area, source = self._prop_only(el, "GrossFloorArea")
        # Tier 2: try Shapely polygon if available and element has footprint
        if area is None:
            poly_area = polygon_area(el)
            if poly_area is not None:
                area = poly_area
                source = "polygon_derived"
        # Tier 1 fallback: AABB
        if area is None:
            area = aabb_floor_area(el.aabb)
            source = "aabb_derived"
        passed = area >= min_area
        if source == "polygon_derived":
            method, tier = "polygon_area", "polygon"
        elif source == "aabb_derived":
            method, tier = "aabb_footprint", "aabb"
        else:
            method, tier = "property_lookup", None
        return RuleResult(
            **common,
            status=STATUS_PASS if passed else STATUS_FAIL,
            passed=passed,
            measured_value=area,
            expected_value=min_area,
            message=f"area={area:.2f} m² {'≥' if passed else '<'} min={min_area:.2f} m²",
            details={"area": area, "min_area": min_area},
            measurement_method=method,
            measurement_source=source,
            geometry_tier=tier,
        )

    def _check_min_width(self, params, el, common) -> RuleResult:
        min_w = float(params["min_width"])
        # Property lookup only - no AABB synthesis here, to let polygon tier win
        width, source = self._prop_only(el, "width")
        if width is None:
            width, source = self._prop_only(el, "Width")
        # Tier 2: try Shapely polygon minimum width
        if width is None:
            poly_w = polygon_min_width(el)
            if poly_w is not None:
                width = poly_w
                source = "polygon_derived"
        # Tier 1 fallback: AABB
        if width is None:
            if el.ifc_class in ("IfcDoor", "IfcWindow"):
                width = aabb_max_horizontal_dim(el.aabb)
            else:
                width = aabb_min_horizontal_dim(el.aabb)
            source = "aabb_derived"
        passed = width >= min_w
        if source == "polygon_derived":
            method, tier = "polygon_min_width", "polygon"
        elif source == "aabb_derived":
            method, tier = "aabb_horizontal_dim", "aabb"
        else:
            method, tier = "property_lookup", None
        return RuleResult(
            **common,
            status=STATUS_PASS if passed else STATUS_FAIL,
            passed=passed,
            measured_value=width,
            expected_value=min_w,
            message=f"width={width:.3f} m {'≥' if passed else '<'} min={min_w:.3f} m",
            details={"width": width, "min_width": min_w},
            measurement_method=method,
            measurement_source=source,
            geometry_tier=tier,
        )

    def _check_min_height(self, params, el, common) -> RuleResult:
        min_h = float(params["min_height"])
        height, source = self._float_prop_sourced(el, "height")
        if height is None:
            height, source = self._float_prop_sourced(el, "Height")
        if height is None:
            height = aabb_height(el.aabb)
            source = "aabb_derived"
        passed = height >= min_h
        return RuleResult(
            **common,
            status=STATUS_PASS if passed else STATUS_FAIL,
            passed=passed,
            measured_value=height,
            expected_value=min_h,
            message=f"height={height:.3f} m {'≥' if passed else '<'} min={min_h:.3f} m",
            details={"height": height, "min_height": min_h},
            measurement_method="aabb_height" if source == "aabb_derived" else "property_lookup",
            measurement_source=source,
            geometry_tier="aabb" if source == "aabb_derived" else None,
        )

    def _check_max_height(self, params, el, common) -> RuleResult:
        max_h = float(params["max_height"])
        height, source = self._float_prop_sourced(el, "height")
        if height is None:
            height = aabb_height(el.aabb)
            source = "aabb_derived"
        passed = height <= max_h
        return RuleResult(
            **common,
            status=STATUS_PASS if passed else STATUS_FAIL,
            passed=passed,
            measured_value=height,
            expected_value=max_h,
            message=f"height={height:.3f} m {'≤' if passed else '>'} max={max_h:.3f} m",
            details={"height": height, "max_height": max_h},
            measurement_method="aabb_height" if source == "aabb_derived" else "property_lookup",
            measurement_source=source,
            geometry_tier="aabb" if source == "aabb_derived" else None,
        )

    def _check_min_dims_2d(self, params, el, common) -> RuleResult:
        d1 = float(params.get("min_dim1", 0))
        d2 = float(params.get("min_dim2", 0))
        dx, dy, _ = aabb_dimensions(el.aabb)
        meas_small = min(dx, dy)
        meas_large = max(dx, dy)
        req_small = min(d1, d2)
        req_large = max(d1, d2)
        passed = meas_small >= req_small and meas_large >= req_large
        return RuleResult(
            **common,
            status=STATUS_PASS if passed else STATUS_FAIL,
            passed=passed,
            measured_value=meas_small,
            expected_value=req_small,
            message=(
                f"dims=({meas_small:.2f} × {meas_large:.2f}) m "
                f"required=({req_small:.2f} × {req_large:.2f}) m "
                f"{'OK' if passed else 'FAIL'}"
            ),
            details={"measured": [dx, dy], "required": [d1, d2]},
            measurement_method="aabb_dimensions_2d",
            measurement_source="aabb_derived",
            geometry_tier="aabb",
        )

    def _check_property_min(self, params, el, common) -> RuleResult:
        prop_name = params.get("property", "")
        min_val = float(params.get("min_value", 0))
        val, source = self._float_prop_sourced(el, prop_name)
        if val is None:
            return RuleResult(
                **common,
                status=STATUS_INCONCLUSIVE,
                message=(
                    f"Missing required property: {prop_name} → "
                    "Set it in Revit before IFC export"
                ),
                details={"gate": "data_missing", "missing_property": prop_name},
            )
        passed = val >= min_val
        return RuleResult(
            **common,
            status=STATUS_PASS if passed else STATUS_FAIL,
            passed=passed,
            measured_value=val,
            expected_value=min_val,
            message=f"{prop_name}={val} {'≥' if passed else '<'} min={min_val}",
            measurement_method="property_lookup" if source == "property" else "aabb_derived_property",
            measurement_source=source,
            geometry_tier="aabb" if source == "aabb_derived" else None,
        )

    def _check_property_max(self, params, el, common) -> RuleResult:
        prop_name = params.get("property", "")
        max_val = float(params.get("max_value", 0))
        val, source = self._float_prop_sourced(el, prop_name)
        if val is None:
            return RuleResult(
                **common,
                status=STATUS_INCONCLUSIVE,
                message=(
                    f"Missing required property: {prop_name} → "
                    "Set it in Revit before IFC export"
                ),
                details={"gate": "data_missing", "missing_property": prop_name},
            )
        passed = val <= max_val
        return RuleResult(
            **common,
            status=STATUS_PASS if passed else STATUS_FAIL,
            passed=passed,
            measured_value=val,
            expected_value=max_val,
            message=f"{prop_name}={val} {'≤' if passed else '>'} max={max_val}",
            measurement_method="property_lookup" if source == "property" else "aabb_derived_property",
            measurement_source=source,
            geometry_tier="aabb" if source == "aabb_derived" else None,
        )

    @staticmethod
    def _prop_only(el: Element, key: str) -> tuple:
        """Return (value, 'property') from IFC properties only — no AABB synthesis.

        Returns (None, None) if the property is absent or non-numeric.
        Used by polygon-eligible handlers to preserve the correct tier cascade:
        property → polygon → AABB.
        """
        v = el.properties.get(key)
        if v is None:
            v = el.properties.get(key.lower())
        if v is None:
            v = el.properties.get(key.capitalize())
        if v is not None:
            try:
                return (float(v), "property")
            except (TypeError, ValueError):
                pass
        return (None, None)

    @staticmethod
    def _float_prop_sourced(el: Element, key: str) -> tuple:
        """Return (value, source) where source is 'property' or 'aabb_derived'.

        Returns (None, None) if value cannot be resolved.

        NOTE: This method synthesizes values from AABB when no property exists.
        Handlers with polygon-tier alternatives (min_area, min_width) should use
        ``_prop_only`` instead, so the polygon path is not short-circuited.
        """
        v = el.properties.get(key)
        if v is None:
            v = el.properties.get(key.lower())
        if v is None:
            v = el.properties.get(key.capitalize())

        if v is not None:
            try:
                return (float(v), "property")
            except (TypeError, ValueError):
                pass

        if el.aabb:
            lk = key.lower()
            dx = el.aabb.max[0] - el.aabb.min[0]
            dy = el.aabb.max[1] - el.aabb.min[1]
            dz = el.aabb.max[2] - el.aabb.min[2]

            if lk == "area":
                if el.ifc_class in ("IfcSpace", "IfcSlab", "IfcRoof"):
                    return (float(dx * dy), "aabb_derived")
                elif el.ifc_class in ("IfcWindow", "IfcDoor", "IfcWall", "IfcWallStandardCase", "IfcCurtainWall"):
                    return (float(max(dx, dy) * dz), "aabb_derived")
                return (float(dx * dy), "aabb_derived")
            elif lk in ("height", "clearheight", "length"):
                return (float(dz if lk != "length" else max(dx, dy)), "aabb_derived")
            elif lk in ("width", "clearwidth"):
                return (float(min(dx, dy)), "aabb_derived")

        return (None, None)

    # --- Clearance Zone check ---

    def _check_clearance_zone(
        self, params, el: Element, context: "Context", common,
    ) -> RuleResult:
        """Expand the element AABB by `padding_m` and check for intersections.

        Params:
            padding_m:         expansion distance in metres (default 1.0)
            ignore_classes:    IFC classes to ignore (default envelope types)
        """
        from .geo_engine import aabb_intersect, AABB

        padding = float(params.get("padding_m", 1.0))
        ignore_classes = set(params.get("ignore_classes", [
            "IfcWall", "IfcWallStandardCase", "IfcCurtainWall",
            "IfcSlab", "IfcRoof", "IfcBuildingStorey", "IfcSpace",
        ]))

        clearance_aabb_dict = {
            "min": [
                el.aabb.min[0] - padding,
                el.aabb.min[1] - padding,
                el.aabb.min[2],
            ],
            "max": [
                el.aabb.max[0] + padding,
                el.aabb.max[1] + padding,
                el.aabb.max[2],
            ],
        }
        clearance_aabb = AABB(
            min=clearance_aabb_dict["min"],
            max=clearance_aabb_dict["max"],
        )

        clearance_bounds = AABB(min=clearance_aabb_dict["min"], max=clearance_aabb_dict["max"])
        blockers = []
        for other in context.spatial_index.query_aabb(clearance_bounds):
            if other.guid == el.guid or other.aabb is None:
                continue
            if other.ifc_class in ignore_classes:
                continue
            blockers.append(other.guid)

        passed = len(blockers) == 0
        # Build viz descriptors
        viz = [
            {"type": "clearance_zone", "aabb": clearance_aabb_dict,
             "label": f"{padding}m clearance zone"},
        ]
        if blockers:
            for bg in blockers[:3]:
                center = None
                be = context.element_by_id(bg)
                if be and be.aabb:
                    center = [
                        (be.aabb.min[i] + be.aabb.max[i]) / 2.0
                        for i in range(3)
                    ]
                    viz.append({"type": "bbox", "aabb": {
                        "min": list(be.aabb.min), "max": list(be.aabb.max),
                    }, "color": "blocker", "label": f"Obstruction {bg[:8]}"})

        return RuleResult(
            **common,
            status=STATUS_PASS if passed else STATUS_FAIL,
            passed=passed,
            message=(
                f"Clearance zone (±{padding}m): "
                f"{'clear' if passed else f'{len(blockers)} obstruction(s) found'}"
            ),
            details={
                "padding_m": padding,
                "blocking_elements": blockers,
                "clearance_aabb": clearance_aabb_dict,
                "viz": viz,
            },
            measurement_method="aabb_expansion_intersection",
            measurement_source="aabb_derived",
            geometry_tier="aabb",
        )

    # --- Turning Circle check ---

    def _check_turning_circle(self, params, el: Element, common) -> RuleResult:
        """Verify that a minimum inscribed circle fits in the element footprint.

        Params:
            diameter_m:  required turning diameter (default 1.5)
        """
        diameter = float(params.get("diameter_m", 1.5))
        radius = diameter / 2.0

        dx = el.aabb.max[0] - el.aabb.min[0]
        dy = el.aabb.max[1] - el.aabb.min[1]
        min_dim = min(dx, dy)
        passed = min_dim >= diameter

        center = [
            (el.aabb.min[0] + el.aabb.max[0]) / 2.0,
            (el.aabb.min[1] + el.aabb.max[1]) / 2.0,
            el.aabb.min[2],
        ]
        viz = [
            {"type": "turning_circle", "center": center, "radius": radius,
             "status": "pass" if passed else "fail",
             "label": f"{int(diameter * 1000)}mm turning circle"},
        ]
        if not passed:
            viz.append({
                "type": "annotation",
                "location": [center[0], center[1], center[2] + 0.3],
                "text": f"Need {diameter - min_dim:.3f}m more width",
            })

        return RuleResult(
            **common,
            status=STATUS_PASS if passed else STATUS_FAIL,
            passed=passed,
            measured_value=min_dim,
            expected_value=diameter,
            message=(
                f"Turning circle: min_dim={min_dim:.3f}m "
                f"{'≥' if passed else '<'} required={diameter}m"
            ),
            details={
                "min_horizontal_dim_m": min_dim,
                "required_diameter_m": diameter,
                "viz": viz,
                "suggestion": (
                    "" if passed
                    else f"Enlarge space by {diameter - min_dim:.3f}m to fit turning circle"
                ),
            },
            measurement_method="aabb_inscribed_circle",
            measurement_source="aabb_derived",
            geometry_tier="aabb",
        )

    # --- Ratio check ---

    def _check_ratio(self, params, el: Element, common) -> RuleResult:
        """Check that the ratio of two measures meets a threshold.

        Params:
            numerator:    property name or geometry keyword (area, width, height)
            denominator:  property name or geometry keyword
            min_ratio:    minimum acceptable ratio (default 0)
            max_ratio:    maximum acceptable ratio (default inf)
        """
        num_key = params.get("numerator", "")
        den_key = params.get("denominator", "")
        min_ratio = float(params.get("min_ratio", 0))
        max_ratio = float(params.get("max_ratio", float("inf")))

        num, num_src = self._float_prop_sourced(el, num_key)
        den, den_src = self._float_prop_sourced(el, den_key)

        if num is None:
            return RuleResult(
                **common, status=STATUS_INCONCLUSIVE,
                message=f"Missing numerator property: {num_key}",
                details={"gate": "data_missing", "missing_property": num_key},
            )
        if den is None or den == 0:
            return RuleResult(
                **common, status=STATUS_INCONCLUSIVE,
                message=f"Missing or zero denominator property: {den_key}",
                details={"gate": "data_missing", "missing_property": den_key},
            )

        ratio = num / den
        passed = min_ratio <= ratio <= max_ratio
        # Source is mixed if numerator and denominator come from different sources
        has_aabb = (num_src == "aabb_derived" or den_src == "aabb_derived")
        source = "aabb_derived" if has_aabb else "property"
        return RuleResult(
            **common,
            status=STATUS_PASS if passed else STATUS_FAIL,
            passed=passed,
            measured_value=ratio,
            expected_value=min_ratio,
            message=(
                f"{num_key}/{den_key} = {ratio:.4f} "
                f"(required: {min_ratio:.4f}–{max_ratio})"
            ),
            details={
                "numerator": num_key, "numerator_value": num,
                "denominator": den_key, "denominator_value": den,
                "ratio": ratio, "min_ratio": min_ratio, "max_ratio": max_ratio,
            },
            measurement_method="ratio_computation",
            measurement_source=source,
            geometry_tier="aabb" if has_aabb else None,
        )

    # --- Distance to nearest element of a target class ---

    def _check_distance_to_nearest(
        self, params, el: Element, context: "Context", common,
    ) -> RuleResult:
        """Check minimum distance to the nearest element of a target class.

        Params:
            target_class:  IFC class to search for (e.g. "IfcDoor")
            max_distance:  maximum allowed distance in metres
            min_distance:  minimum required distance in metres (default 0)
            search_radius: search radius in metres (default max_distance * 2)
        """
        from .geo_engine import distance_between

        target_class = params.get("target_class", "")
        max_dist = float(params.get("max_distance", float("inf")))
        min_dist = float(params.get("min_distance", 0))
        search_r = float(params.get("search_radius", max_dist * 2 if max_dist != float("inf") else 50))

        nearby = context.get_nearby_elements(el, search_r)
        candidates = [
            n for n in nearby
            if n.ifc_class.lower() == target_class.lower()
            and n.guid != el.guid
            and n.aabb is not None
        ]

        if not candidates:
            return RuleResult(
                **common,
                status=STATUS_FAIL if max_dist != float("inf") else STATUS_PASS,
                passed=max_dist == float("inf"),
                message=f"No {target_class} found within {search_r}m",
                details={
                    "target_class": target_class,
                    "search_radius": search_r,
                    "nearest_distance": None,
                },
            )

        best_dist = min(distance_between(el, c) for c in candidates)
        passed = min_dist <= best_dist <= max_dist
        return RuleResult(
            **common,
            status=STATUS_PASS if passed else STATUS_FAIL,
            passed=passed,
            measured_value=best_dist,
            expected_value=max_dist if max_dist != float("inf") else min_dist,
            message=(
                f"Nearest {target_class}: {best_dist:.2f}m "
                f"(required: {min_dist}–{max_dist}m)"
            ),
            details={
                "target_class": target_class,
                "nearest_distance": best_dist,
                "min_distance": min_dist,
                "max_distance": max_dist,
            },
            measurement_method="centroid_distance",
            measurement_source="aabb_derived",
            geometry_tier="aabb",
        )

    # --- Count nearby elements of a target class ---

    def _check_count_nearby(
        self, params, el: Element, context: "Context", common,
    ) -> RuleResult:
        """Check that enough elements of a target class exist within a radius.

        Params:
            target_class:  IFC class to count (e.g. "IfcRailing")
            radius:        search radius in metres (default 5.0)
            min_count:     minimum required count (default 1)
            max_count:     maximum allowed count (default inf)
        """
        target_class = params.get("target_class", "")
        radius = float(params.get("radius", 5.0))
        min_count = int(params.get("min_count", 1))
        max_count = float(params.get("max_count", float("inf")))

        nearby = context.get_nearby_elements(el, radius)
        matches = [
            n for n in nearby
            if n.ifc_class.lower() == target_class.lower()
            and n.guid != el.guid
        ]
        count = len(matches)
        passed = min_count <= count <= max_count
        return RuleResult(
            **common,
            status=STATUS_PASS if passed else STATUS_FAIL,
            passed=passed,
            measured_value=count,
            expected_value=min_count,
            message=(
                f"Found {count} {target_class} within {radius}m "
                f"(required: {min_count}–{max_count})"
            ),
            details={
                "target_class": target_class,
                "radius": radius,
                "count": count,
                "min_count": min_count,
                "max_count": max_count,
                "matched_guids": [m.guid for m in matches[:10]],
            },
            measurement_method="spatial_count",
            measurement_source="aabb_derived",
            geometry_tier="aabb",
        )

    # ------------------------------------------------------------------
    # Python rule sandbox
    # ------------------------------------------------------------------

    def _compile_python_rule(
        self, rule_id: str, code: str
    ) -> Callable[[Context, Element], Dict[str, Any]]:
        tree = ast.parse(code, mode="exec")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal)):
                raise ValueError(
                    "Imports and global/nonlocal are not allowed in Python rules"
                )
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in FORBIDDEN_BUILTINS:
                    raise ValueError(
                        f"Forbidden builtin '{func.id}' in rule code"
                    )

        safe_builtins: Dict[str, Any] = {
            "abs": abs, "min": min, "max": max, "sum": sum,
            "all": all, "any": any,
            "len": len, "round": round, "sorted": sorted,
            "enumerate": enumerate, "range": range,
            "isinstance": isinstance, "bool": bool,
            "int": int, "float": float, "str": str, "list": list,
            "dict": dict, "tuple": tuple, "set": set,
            "True": True, "False": False, "None": None,
        }

        # Import VizContext so Python rules can use it
        try:
            from .visualizer import VizContext
        except Exception:
            VizContext = None

        from .geo_engine import walkable_clear_width as _walkable_clear_width

        module_globals: Dict[str, Any] = {
            "__builtins__": safe_builtins,
            "math": math,
            "walkable_clear_width": _walkable_clear_width,
        }
        if VizContext is not None:
            module_globals["VizContext"] = VizContext

        # Execute in a single namespace so module-level helpers and constants
        # are visible inside ``run()`` (this matches normal Python module
        # semantics; a separate locals dict would leave names like helper
        # functions and module constants invisible to ``run``).
        compiled = compile(tree, f"<rule:{rule_id}>", "exec")
        exec(compiled, module_globals)
        run_fn = module_globals.get("run")
        if not callable(run_fn):
            raise ValueError("Python rule must define a callable 'run(context, element)'")

        def runner(context: Context, element: Element) -> Dict[str, Any]:
            try:
                out = run_fn(context, element)
                if isinstance(out, dict):
                    return out
                return {"passed": bool(out), "message": "", "details": {}}
            except Exception as exc:
                return {
                    "passed": None,
                    "message": f"Exception during rule execution: {exc}",
                    "details": {},
                }

        return runner

    # ------------------------------------------------------------------
    # Main execution
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Waiver application + staleness annotation
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_waivers(
        results: List[RuleResult],
        rules: List[Dict[str, Any]],
        context: "Context",
    ) -> None:
        """Attach matching WaiverRecords to FAIL results; annotate staleness.

        The rule re-runs every time — waivers are never consulted as a
        short-circuit. After execution, this method walks the result stream
        and, for each (rule, element) FAIL or PASS, looks up any waivers
        whose identity matches and annotates the result:

          - FAIL + identity match + still in force → ``waiver_state = "applied"``
          - FAIL + identity match + past expiry / version-changed / occasion exhausted
            → ``waiver_state = "invalid"`` with ``waiver_invalidation_reason`` set
          - PASS + identity match → ``waiver_state = "superseded"`` (informational)
          - Identity mismatch on a candidate waiver → not auto-applied
            (rendered later via the run log, not on the result itself)

        Status is never flipped.
        """
        if not context.waivers:
            return

        rule_meta: Dict[str, Dict[str, Any]] = {}
        for r in rules:
            rid = r.get("id")
            if rid:
                rule_meta[rid] = r

        # Group waivers by (rule_id, element_id) for O(1) lookup. We keep
        # every matching waiver since we render all of them in the report.
        by_anchor: Dict[tuple, List[WaiverRecord]] = {}
        for w in context.waivers.values():
            key = (w.rule_id, w.element_id)
            by_anchor.setdefault(key, []).append(w)

        now_iso = _now_iso()
        for res in results:
            if res.status not in (STATUS_FAIL, STATUS_PASS):
                continue
            if not res.element_id:
                continue
            candidates = by_anchor.get((res.rule_id, res.element_id), [])
            if not candidates:
                continue

            meta = rule_meta.get(res.rule_id, {}) or {}
            pack_id = str(meta.get("rule_pack_id", ""))
            version = _canonical_rule_version(meta)

            # Pick the most recent (granted_at) waiver whose identity matches.
            # Distinguish "rule version changed" from "everything else mismatch":
            # a waiver that matches every identity field except rule_version is
            # still about the same finding - it is rendered as stale-version,
            # not as a hard identity mismatch.
            applied: Optional[WaiverRecord] = None
            version_mismatched: Optional[WaiverRecord] = None
            other_mismatched: Optional[WaiverRecord] = None
            for w in sorted(candidates, key=lambda x: x.granted_at, reverse=True):
                if w.matches_identity(
                    rule_pack_id=pack_id,
                    rule_id=res.rule_id,
                    rule_version=version,
                    element_id=res.element_id,
                    project_id=context.project_id,
                ):
                    applied = w
                    break
                if (
                    w.rule_pack_id == pack_id
                    and w.rule_id == res.rule_id
                    and w.element_id == res.element_id
                    and w.project_id == context.project_id
                    and w.rule_version != version
                ):
                    version_mismatched = version_mismatched or w
                else:
                    other_mismatched = other_mismatched or w

            if applied is None:
                if version_mismatched is not None:
                    res.waiver = version_mismatched
                    res.waiver_state = WAIVER_STATE_INVALID
                    res.waiver_invalidation_reason = WAIVER_INVALIDATION_RULE_VERSION_CHANGED
                elif other_mismatched is not None:
                    res.waiver = other_mismatched
                    res.waiver_state = WAIVER_STATE_INVALID
                    res.waiver_invalidation_reason = WAIVER_INVALIDATION_IDENTITY_MISMATCH
                continue

            # Identity matched - assess freshness.
            # Engine-detectable invalidation: past expiry date, or the deployer
            # signalled prior consumption via context.metadata["consumed_waiver_ids"].
            # "one-time" / "until_next_submission" occasions are exhausted only
            # when the deployer says so - the engine has no persistent run history
            # of its own.
            invalidation_reason: Optional[str] = None
            consumed = set(
                (context.metadata or {}).get("consumed_waiver_ids", []) or []
            )
            if applied.expires_at and applied.expires_at < now_iso:
                invalidation_reason = WAIVER_INVALIDATION_PAST_EXPIRY_DATE
            elif applied.occasion in ("until_next_submission", "one-time") and applied.waiver_id in consumed:
                invalidation_reason = WAIVER_INVALIDATION_OCCASION_EXHAUSTED

            res.waiver = applied
            if res.status == STATUS_PASS:
                # New result no longer FAIL -> waiver is superseded.
                res.waiver_state = WAIVER_STATE_SUPERSEDED
            elif invalidation_reason is not None:
                res.waiver_state = WAIVER_STATE_INVALID
                res.waiver_invalidation_reason = invalidation_reason
            else:
                res.waiver_state = WAIVER_STATE_APPLIED

    # ------------------------------------------------------------------
    # Routing resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_routes(
        results: List[RuleResult], rules: List[Dict[str, Any]]
    ) -> None:
        """Set ``provider_route`` on every non-PASS / non-FAIL result.

        Resolution order:
          1. Rule-level ``routing`` block, if present.
          2. ``human_reason`` → ROUTING_REGISTRY entry (kind = human_reason).
          3. For confidence-failure INCONCLUSIVEs, the deficient ``dimension``
             from ``details`` → ROUTING_REGISTRY entry (kind = confidence_dim).
          4. ``details["gate"]`` → ROUTING_REGISTRY entry (kind = gate).

        Waived FAILs are also routed (waiver routes to a deviation reviewer).
        """
        rule_routing: Dict[str, Dict[str, Any]] = {}
        for r in rules:
            rid = r.get("id")
            routing = r.get("routing")
            if rid and isinstance(routing, dict):
                rule_routing[rid] = routing

        for res in results:
            if res.provider_route:
                continue
            # Definitive PASS without a waiver does not need routing
            if res.status == STATUS_PASS and not res.waiver:
                continue
            # Open FAILs (no waiver, no human_reason) do not need routing -
            # they are direct corrections; only waived FAILs route.
            if res.status == STATUS_FAIL and not res.waiver and not res.human_reason:
                continue

            override = rule_routing.get(res.rule_id)
            if override and override.get("primary"):
                res.provider_route = str(override["primary"])
                continue

            entry = None
            if res.human_reason:
                entry = resolve_route(res.human_reason)
            if entry is None:
                details = res.details or {}
                gate = details.get("gate")
                if gate == "confidence_insufficient":
                    dim = details.get("dimension")
                    if dim:
                        entry = resolve_route(f"{dim}_confidence_low")
                if entry is None and gate:
                    entry = resolve_route(gate)

            # Fallback: an INCONCLUSIVE produced inside a rule body without a
            # declared gate is still a data gap - route it as data_missing so
            # the "every non-binary result is routed" invariant holds even
            # for rules that forget to tag their details.
            if entry is None and res.status == STATUS_INCONCLUSIVE:
                entry = resolve_route("data_missing")

            if entry is not None:
                res.provider_route = entry.provider
            elif override and override.get("fallback"):
                res.provider_route = str(override["fallback"])

    @staticmethod
    def _build_coverage_gaps(results: List[RuleResult]) -> List[CoverageGap]:
        """Build structured coverage gaps from non-PASS/FAIL results.

        Two sources are covered:
          1. Engine checkability-gate failures, which set ``details["gate"]``
             explicitly (Gates 1–5 in ``_checkability_gate``).
          2. INCONCLUSIVE results emitted from inside a rule body (e.g. Python
             rules that detect a missing tag or unavailable input) that do not
             flow through the gate machinery. The gap_type is inferred from
             the ``details`` payload so these cases still appear in the gap
             report rather than only as bare INCONCLUSIVE rows.
        """
        from collections import defaultdict

        def _infer_gap_type(details: Dict[str, Any]) -> Optional[str]:
            if not details:
                return None
            if details.get("missing_external"):
                return "external_missing"
            if details.get("missing_property") or details.get("missing_properties"):
                return "data_missing"
            missing = details.get("missing")
            if missing:
                # 'aabb' / 'geometry' indicates geometry-side gaps; anything
                # else (e.g. a missing tagged feature like 'fire_exit_elements')
                # is treated as a data gap routed to the BIM modeler.
                if str(missing).lower() in ("aabb", "geometry"):
                    return "geometry_limited"
                return "data_missing"
            return None

        gap_groups: Dict[tuple, List[str]] = defaultdict(list)
        gap_missing: Dict[tuple, str] = {}
        for r in results:
            details = r.details or {}
            gate_type = details.get("gate")
            if gate_type is None and r.status == STATUS_INCONCLUSIVE:
                gate_type = _infer_gap_type(details)
            if gate_type is None:
                continue
            key = (r.rule_id, gate_type)
            if r.element_id:
                gap_groups[key].append(r.element_id)
            if key not in gap_missing:
                missing_parts = []
                for k in ("missing_properties", "missing_property", "missing_external", "missing"):
                    v = details.get(k)
                    if v:
                        missing_parts.append(
                            f"{k}: {v}" if isinstance(v, str) else f"{k}: {', '.join(v)}"
                        )
                # Fall back to the result message when no structured missing keys exist
                if not missing_parts and r.message:
                    missing_parts.append(r.message)
                gap_missing[key] = "; ".join(missing_parts)

        gaps = []
        for (rule_id, gate_type), elements in gap_groups.items():
            entry = resolve_route(gate_type)
            action = entry.action if entry else ""
            actor = entry.actor if entry else ""
            provider = entry.provider if entry else ""
            gaps.append(CoverageGap(
                rule_id=rule_id,
                gap_type=gate_type,
                affected_elements=elements,
                missing_data=gap_missing.get((rule_id, gate_type), ""),
                suggested_action=action,
                responsible_actor=actor,
                provider_route=provider,
            ))
        return gaps

    # ------------------------------------------------------------------
    # Authority-based precedence
    # ------------------------------------------------------------------

    @staticmethod
    def _authority_rank(rule: Dict[str, Any]) -> int:
        """Return authority rank (lower = higher authority). Default is lowest."""
        authority = rule.get("authority", "best_practice")
        try:
            return AUTHORITY_HIERARCHY.index(authority)
        except ValueError:
            return len(AUTHORITY_HIERARCHY)

    def _resolve_authority_overrides(self) -> List[Dict[str, Any]]:
        """When multiple rules share the same ID, keep the highest-authority one."""
        seen: Dict[str, Dict[str, Any]] = {}
        for rule in self.rules:
            rid = str(rule.get("id", "")).strip().lower()
            if not rid:
                continue
            if rid in seen:
                existing = seen[rid]
                if self._authority_rank(rule) < self._authority_rank(existing):
                    self.override_trail.append(OverrideRecord(
                        rule_id=rule.get("id", ""),
                        overridden_by=rule.get("id", ""),
                        reason="Higher authority rule takes precedence",
                        override_type="authority_precedence",
                        winning_authority=rule.get("authority", ""),
                        losing_authority=existing.get("authority", ""),
                    ))
                    seen[rid] = rule
                else:
                    self.override_trail.append(OverrideRecord(
                        rule_id=existing.get("id", ""),
                        overridden_by=existing.get("id", ""),
                        reason="Existing rule has equal or higher authority",
                        override_type="authority_precedence",
                        winning_authority=existing.get("authority", ""),
                        losing_authority=rule.get("authority", ""),
                    ))
            else:
                seen[rid] = rule
        return list(seen.values())

    # ------------------------------------------------------------------
    # Temporal filtering
    # ------------------------------------------------------------------

    def _filter_temporal(self, rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filter rules by regulation_date if set."""
        if not self._regulation_date:
            return rules
        target = self._regulation_date
        filtered = []
        for rule in rules:
            eff = rule.get("effective_date")
            sup = rule.get("superseded_date")
            # No dates = always active
            if not eff and not sup:
                filtered.append(rule)
                continue
            if eff and eff > target:
                continue  # not yet effective
            if sup and sup <= target:
                continue  # already superseded
            filtered.append(rule)
        return filtered

    # ------------------------------------------------------------------
    # Contradiction detection
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_contradictions(results: List[RuleResult]) -> List[Contradiction]:
        """Detect same-element contradictions from different rules.

        Aggregate-scope results (class/model) are excluded — a class-scope
        PASS and an element-scope FAIL are not a same-element contradiction
.
        """
        from collections import defaultdict
        # Group by element
        element_results: Dict[str, List[RuleResult]] = defaultdict(list)
        for r in results:
            if r.scope in ("class", "model"):
                continue
            if r.element_id:
                element_results[r.element_id].append(r)

        contradictions = []
        for eid, res_list in element_results.items():
            # Only check definitive results
            definitive = [r for r in res_list if r.status in (STATUS_PASS, STATUS_FAIL)]
            if len(definitive) < 2:
                continue
            # Look for same-category contradictions (PASS vs FAIL)
            cat_results: Dict[str, List[RuleResult]] = defaultdict(list)
            for r in definitive:
                cat_results[r.category].append(r)
            for cat, cat_list in cat_results.items():
                passes = [r for r in cat_list if r.status == STATUS_PASS]
                fails = [r for r in cat_list if r.status == STATUS_FAIL]
                if passes and fails:
                    # Report first contradiction pair per category
                    contradictions.append(Contradiction(
                        element_id=eid,
                        rule_id_a=passes[0].rule_id,
                        rule_id_b=fails[0].rule_id,
                        status_a=STATUS_PASS,
                        status_b=STATUS_FAIL,
                        description=f"Element {eid}: {passes[0].rule_id} passes but {fails[0].rule_id} fails in category '{cat}'",
                    ))
        return contradictions

    def execute(self, context: Context) -> List[RuleResult]:
        """Run all rules in dependency order. Never raises."""
        # Temporal filtering
        active_rules = self._filter_temporal(self.rules)

        # Stage-based rule filtering: skip rules whose min_stage exceeds current stage
        if self._execution_stage:
            from .data_models import STAGE_ORDER
            current_order = STAGE_ORDER.get(self._execution_stage, 0)
            active_rules = [
                r for r in active_rules
                if STAGE_ORDER.get(r.get("min_stage", "concept"), 0) <= current_order
            ]

        order = active_rules
        try:
            if any(r.get("depends_on") for r in active_rules):
                order = self._toposort()
        except Exception as exc:
            # Dependency error: mark all rules inconclusive
            return [
                RuleResult(
                    rule_id=r.get("id", "<unnamed>"),
                    status=STATUS_INCONCLUSIVE,
                    passed=False,
                    message=f"Dependency resolution failed: {exc}",
                )
                for r in self.rules
            ]

        all_results: List[RuleResult] = []
        for rule in order:
            rid = rule.get("id", "<unnamed>")
            severity = rule.get("severity", "major")
            category = rule.get("category", "general")
            try:
                if rule.get("language") == "python" or "code" in rule:
                    if rid not in self._compiled_python:
                        code = rule.get("code")
                        if not code and rule.get("path"):
                            with open(rule["path"], "r", encoding="utf-8") as fh:
                                code = fh.read()
                        if not code:
                            raise ValueError("Python rule missing 'code' or 'path'")
                        self._compiled_python[rid] = self._compile_python_rule(rid, code)
                    runner = self._compiled_python[rid]
                    py_scope = rule.get("scope", "element")
                    py_selection = self._select_elements(rule.get("selector", {}), context)

                    # Dormant-rule split: emit one aggregate
                    # NOT_APPLICABLE / INCONCLUSIVE result and stop.
                    if not py_selection and py_scope == "element":
                        all_results.append(
                            self._emit_dormant_result(rule, context, severity, category)
                        )
                        continue

                    # Class / model scope: call run(context, elements) once
                    # with the full list.
                    if py_scope in ("class", "model"):
                        target_class = self._selector_target_class(rule.get("selector", {}) or {})
                        anchor = (
                            f"class:{target_class}" if py_scope == "class" and target_class
                            else "model:"
                        )
                        try:
                            out = runner(context, py_selection)  # type: ignore[arg-type]
                        except Exception as exc:
                            out = {"passed": None, "message": f"Aggregate Python rule failed: {exc}", "details": {}}
                        passed = out.get("passed")
                        if passed is True:
                            status = STATUS_PASS
                        elif passed is False:
                            status = STATUS_FAIL
                        else:
                            status = STATUS_INCONCLUSIVE
                        rr = RuleResult(
                            rule_id=rid,
                            element_id=anchor,
                            status=status,
                            passed=passed,
                            message=out.get("message", ""),
                            details=out.get("details", {}),
                            severity=severity,
                            category=category,
                            scope=py_scope,
                            affected_element_ids=[el.guid for el in py_selection],
                            measurement_method=out.get("measurement_method", "python_rule"),
                            measurement_source=out.get("measurement_source", "python_rule"),
                        )
                        all_results.append(rr)
                        continue

                    for el in py_selection:
                        # Run Python rules through the same checkability gate as JSON rules
                        common_py = {
                            "rule_id": rid,
                            "element_id": el.guid,
                            "aabb": _aabb_dict(el),
                            "severity": severity,
                            "category": category,
                        }
                        gate_result = self._checkability_gate(rule, "python_rule", el, context, common_py)
                        if gate_result is not None:
                            gate_result.evidence = self._build_evidence(
                                rule, el,
                                geometry_tier="none",
                                measurement_method="checkability_gate",
                                measurement_source=str((gate_result.details or {}).get("gate", "gate")),
                            )
                            all_results.append(gate_result)
                            continue

                        out = runner(context, el)
                        # Python rules may return an explicit ``status`` to emit
                        # HUMAN_REQUIRED / NOT_APPLICABLE / UNSUPPORTED directly,
                        # otherwise fall back to the historical passed->status map.
                        explicit_status = out.get("status")
                        passed = out.get("passed")
                        if explicit_status in _VALID_STATUSES:
                            status = explicit_status
                        elif passed is True:
                            status = STATUS_PASS
                        elif passed is False:
                            status = STATUS_FAIL
                        else:
                            status = STATUS_INCONCLUSIVE
                        rr = RuleResult(
                            rule_id=rid,
                            element_id=el.guid,
                            status=status,
                            passed=passed,
                            message=out.get("message", ""),
                            details=out.get("details", {}),
                            aabb=_aabb_dict(el),
                            severity=severity,
                            category=category,
                            measurement_method=out.get("measurement_method", "python_rule"),
                            measurement_source=out.get("measurement_source", "python_rule"),
                            geometry_tier=out.get("geometry_tier"),
                            human_reason=out.get("human_reason"),
                            arrival_path=out.get("arrival_path"),
                            measured_value=out.get("measured_value"),
                            expected_value=out.get("expected_value"),
                        )
                        # Auto-attach evidence bundle (same pipeline as JSON rules)
                        rr.evidence = self._build_evidence(
                            rule, el,
                            geometry_tier=rr.geometry_tier,
                            measurement_method=rr.measurement_method,
                            measurement_source=rr.measurement_source,
                        )
                        all_results.append(rr)
                else:
                    all_results.extend(self._execute_json_rule(rule, context))
            except Exception as exc:
                all_results.append(RuleResult(
                    rule_id=rid,
                    status=STATUS_INCONCLUSIVE,
                    passed=False,
                    message=f"rule error: {exc}",
                    severity=severity,
                    category=category,
                ))

        # Apply waivers before routing so a waived FAIL routes
        # to the deviation reviewer rather than to a coverage-gap action.
        self._apply_waivers(all_results, active_rules, context)

        # Annotate waived FAILs with the deviation_request human_reason so
        # the routing pass picks up the right registry entry.
        for r in all_results:
            if r.waiver_state == WAIVER_STATE_APPLIED and r.human_reason is None:
                r.human_reason = "deviation_request"
                r.arrival_path = "from_fail:deviation_request"

        # Resolve provider_route on every result that needs one.
        # Runs before coverage-gap construction so the routes are visible to
        # any consumer that pulls them off the result stream.
        self._resolve_routes(all_results, active_rules)

        self.coverage_gaps = self._build_coverage_gaps(all_results)
        self.contradictions = self._detect_contradictions(all_results)

        # Evaluate stage exit criteria
        self.stage_exit_result = None
        if self._execution_stage:
            from .data_models import DEFAULT_STAGE_CRITERIA
            criteria = DEFAULT_STAGE_CRITERIA.get(self._execution_stage)
            if criteria:
                self.stage_exit_result = criteria.evaluate(all_results)

        return all_results


# ---------------------------------------------------------------------------
# Built-in check type registrations
# ---------------------------------------------------------------------------
# Each call wires a check_type string to a method on RuleEngine.
# External code can call register_check_type() to add new types without
# modifying this file.

register_check_type("min_area",          RuleEngine._check_min_area)
register_check_type("min_width",         RuleEngine._check_min_width)
register_check_type("min_height",        RuleEngine._check_min_height)
register_check_type("max_height",        RuleEngine._check_max_height)
register_check_type("min_dimensions_2d", RuleEngine._check_min_dims_2d)
register_check_type("property_min",      RuleEngine._check_property_min,      needs_geometry=False)
register_check_type("property_max",      RuleEngine._check_property_max,      needs_geometry=False)
register_check_type("clearance_zone",    RuleEngine._check_clearance_zone,    needs_context=True)
register_check_type("turning_circle",    RuleEngine._check_turning_circle)
register_check_type("ratio",             RuleEngine._check_ratio,             needs_geometry=False)
register_check_type("distance_to_nearest", RuleEngine._check_distance_to_nearest, needs_context=True)
register_check_type("count_nearby",      RuleEngine._check_count_nearby,      needs_context=True)

# Aggregate (class/model-scope) check types. These receive the
# full element list rather than a single element and emit one aggregate
# RuleResult with ``scope`` set and ``affected_element_ids`` populated.
register_check_type("count",        RuleEngine._check_count,        needs_geometry=False, scope="class")
register_check_type("sum_property", RuleEngine._check_sum_property, needs_geometry=False, scope="class")
register_check_type("any_pass",     RuleEngine._check_any_pass,     needs_geometry=False, scope="class")
register_check_type("all_pass",     RuleEngine._check_all_pass,     needs_geometry=False, scope="class")
