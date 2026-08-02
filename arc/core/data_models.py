# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""Canonical data models for ARC-Core.

- Element carries guid/ifc_class/discipline/confidence/property_sets/metadata
- RuleResult carries 6-status taxonomy (PASS/FAIL/INCONCLUSIVE/HUMAN_REQUIRED/NOT_APPLICABLE/UNSUPPORTED)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional


def _default_confidence() -> Dict[str, float]:
    return {"semantic": 0.0, "geometry": 0.0, "topology": 0.0}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp(v: float) -> float:
    """Clamp to 0-1 and reject NaN/Inf per spec numeric safety rules."""
    if v is None or math.isnan(v) or math.isinf(v):
        return 0.0
    return max(0.0, min(1.0, v))


@dataclass
class AABB:
    min: List[float]
    max: List[float]


@dataclass
class Element:
    """Spatial element extracted from an IFC model or created programmatically."""
    guid: str
    ifc_class: str
    discipline: str = "unknown"

    # Geometry
    aabb: Optional[AABB] = None

    # Properties
    properties: Dict[str, Any] = field(default_factory=dict)
    property_sets: Dict[str, Any] = field(default_factory=dict)

    # Confidence scores (0-1 each, never missing per spec)
    confidence: Dict[str, float] = field(default_factory=_default_confidence)

    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # Clamp confidence values on construction
        self.confidence = {k: _clamp(v) for k, v in self.confidence.items()}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "guid": self.guid,
            "ifc_class": self.ifc_class,
            "discipline": self.discipline,
            "aabb": asdict(self.aabb) if self.aabb else None,
            "properties": self.properties,
            "property_sets": self.property_sets,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# 6-Status Taxonomy
# ---------------------------------------------------------------------------

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_INCONCLUSIVE = "INCONCLUSIVE"
STATUS_HUMAN_REQUIRED = "HUMAN_REQUIRED"
STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"
STATUS_UNSUPPORTED = "UNSUPPORTED"

_VALID_STATUSES = {
    STATUS_PASS, STATUS_FAIL, STATUS_INCONCLUSIVE,
    STATUS_HUMAN_REQUIRED, STATUS_NOT_APPLICABLE, STATUS_UNSUPPORTED,
}

# ---------------------------------------------------------------------------
# Execution Stages
# ---------------------------------------------------------------------------

STAGE_CONCEPT = "concept"
STAGE_SCHEMATIC = "schematic"
STAGE_SUBMISSION = "submission"

VALID_STAGES = {STAGE_CONCEPT, STAGE_SCHEMATIC, STAGE_SUBMISSION}

# Ordering for min_stage comparison: concept < schematic < submission
STAGE_ORDER = {STAGE_CONCEPT: 0, STAGE_SCHEMATIC: 1, STAGE_SUBMISSION: 2}


def _derive_status(passed: Optional[bool], status: Optional[str]) -> str:
    """Derive canonical status string from passed flag and optional explicit status."""
    if status in _VALID_STATUSES:
        return status
    if passed is True:
        return STATUS_PASS
    if passed is False:
        return STATUS_FAIL
    return STATUS_INCONCLUSIVE


@dataclass
class EvidenceBundle:
    """Full provenance record per compliance finding."""
    rule_version: str = "1.0"
    rule_source: str = ""
    element_guid: str = ""
    geometry_tier: str = ""              # "aabb" | "polygon" | "mesh"
    measurement_method: str = ""
    measurement_source: str = ""         # "aabb_derived" | "property" | "polygon" | "mesh"
    confidence_label: str = ""           # "approximate" (AABB proxy) | "reported" (authored property, unverified) | "measured" (engine-computed from polygon/mesh) | "counted" (aggregate selection)
    external_data_used: List[str] = field(default_factory=list)
    enrichment_sources: Dict[str, str] = field(default_factory=dict)
    assumptions: List[str] = field(default_factory=list)
    compound_approximation: bool = False
    timestamp: str = field(default_factory=_now_iso)
    arc_version: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RuleResult:
    rule_id: str
    element_id: Optional[str] = None
    passed: Optional[bool] = None
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    aabb: Optional[Dict[str, Any]] = None

    # Extended spec fields
    status: Optional[str] = None
    measured_value: Optional[float] = None
    expected_value: Optional[float] = None
    tolerance: float = 0.0
    confidence: Dict[str, float] = field(default_factory=_default_confidence)
    severity: str = "major"
    category: str = "general"
    timestamp: str = field(default_factory=_now_iso)

    # Measurement provenance
    measurement_method: Optional[str] = None
    measurement_source: Optional[str] = None
    geometry_tier: Optional[str] = None

    # Evidence bundle - full provenance record
    evidence: Optional[EvidenceBundle] = None

    # Routing / HUMAN_REQUIRED sub-reason model
    # human_reason: registry-keyed sub-reason (e.g. "subjective_rule", "deviation_request")
    # arrival_path: how the result reached its status ("gate:human_judgment", "from_fail:deviation_request", ...)
    # scope: result anchor - "element" | "class" | "model"
    # affected_element_ids: for class/model scope, the elements the rule actually evaluated
    # waiver: attached WaiverRecord for FAILs accepted as allowed deviations
    # provider_route: resolved P-card (e.g. "P3", "P10") set by _resolve_routes after execution
    human_reason: Optional[str] = None
    arrival_path: Optional[str] = None
    scope: str = "element"
    affected_element_ids: List[str] = field(default_factory=list)
    waiver: Optional["WaiverRecord"] = None
    waiver_state: Optional[str] = None  # "applied" | "invalid" | "superseded"; only set when a waiver record was considered
    waiver_invalidation_reason: Optional[str] = None
    provider_route: Optional[str] = None

    def __post_init__(self):
        self.status = _derive_status(self.passed, self.status)
        if self.passed is None and self.status == STATUS_PASS:
            self.passed = True
        elif self.passed is None and self.status == STATUS_FAIL:
            self.passed = False
        self.confidence = {k: _clamp(v) for k, v in self.confidence.items()}

    @property
    def delta(self) -> Optional[float]:
        if self.measured_value is not None and self.expected_value is not None:
            return self.measured_value - self.expected_value
        return None

    @property
    def is_aggregate(self) -> bool:
        """True for class/model-scope results that do not anchor on a single element."""
        return self.scope in ("class", "model")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "element_id": self.element_id,
            "status": self.status,
            "passed": self.passed,
            "message": self.message,
            "details": self.details,
            "aabb": self.aabb,
            "measured_value": self.measured_value,
            "expected_value": self.expected_value,
            "delta": self.delta,
            "tolerance": self.tolerance,
            "confidence": self.confidence,
            "severity": self.severity,
            "category": self.category,
            "timestamp": self.timestamp,
            "measurement_method": self.measurement_method,
            "measurement_source": self.measurement_source,
            "geometry_tier": self.geometry_tier,
            "evidence": self.evidence.to_dict() if self.evidence else None,
            "human_reason": self.human_reason,
            "arrival_path": self.arrival_path,
            "scope": self.scope,
            "affected_element_ids": list(self.affected_element_ids),
            "waiver": self.waiver.to_dict() if self.waiver else None,
            "waiver_state": self.waiver_state,
            "waiver_invalidation_reason": self.waiver_invalidation_reason,
            "provider_route": self.provider_route,
        }


@dataclass
class ModelSummary:
    """Aggregate result counts across multiple named axes.

    Counts live on four independent axes — pair, rule, element, aggregate —
    so that no single number mixes them. ``compliance_score`` is defined on
    the pair axis (the historical denominator); a second optional rule-axis
    score is computed on demand. Waiver and human-reason breakdowns are
    informational sub-set views that are never folded into the axis totals.
    """
    total_elements: int = 0
    total_rules: int = 0
    # Pair axis - one entry per (rule, element) result (the historical totals)
    pass_count: int = 0
    fail_count: int = 0
    inconclusive_count: int = 0
    human_required_count: int = 0
    not_applicable_count: int = 0
    unsupported_count: int = 0
    # Rule axis - one entry per rule (evaluated / skipped / any_fail / any_human_required)
    rule_counts: Dict[str, int] = field(default_factory=lambda: {
        "evaluated": 0, "skipped": 0, "any_fail": 0, "any_human_required": 0,
    })
    # Element axis - one entry per element (touched / any_fail / any_human_required / untouched)
    element_counts: Dict[str, int] = field(default_factory=lambda: {
        "touched": 0, "any_fail": 0, "any_human_required": 0, "untouched": 0,
    })
    # Aggregate axis - class/model-scope results, never folded into the pair/element axes
    aggregate_counts: Dict[str, int] = field(default_factory=lambda: {
        "pass": 0, "fail": 0, "inconclusive": 0,
        "human_required": 0, "not_applicable": 0, "unsupported": 0,
    })
    # Sub-set views - informational; do not sum to anything else
    human_reason_breakdown: Dict[str, int] = field(default_factory=dict)
    waiver_breakdown: Dict[str, int] = field(default_factory=lambda: {
        "open_fails": 0, "waived_fails": 0, "invalid_waivers": 0, "superseded_waivers": 0,
    })

    @property
    def compliance_score(self) -> float:
        """Pair-axis score: PASS / (PASS + FAIL) — historical denominator."""
        checked = self.pass_count + self.fail_count
        if checked == 0:
            return 0.0
        return self.pass_count / checked

    @property
    def rule_axis_score(self) -> float:
        """Rule-axis score: rules with no open FAIL / evaluated rules."""
        evaluated = self.rule_counts.get("evaluated", 0)
        if evaluated == 0:
            return 0.0
        any_fail = self.rule_counts.get("any_fail", 0)
        return (evaluated - any_fail) / evaluated

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_elements": self.total_elements,
            "total_rules": self.total_rules,
            "pass_count": self.pass_count,
            "fail_count": self.fail_count,
            "inconclusive_count": self.inconclusive_count,
            "human_required_count": self.human_required_count,
            "not_applicable_count": self.not_applicable_count,
            "unsupported_count": self.unsupported_count,
            "compliance_score": round(self.compliance_score, 4),
            "rule_axis_score": round(self.rule_axis_score, 4),
            "pair_counts": {
                "pass": self.pass_count, "fail": self.fail_count,
                "inconclusive": self.inconclusive_count,
                "human_required": self.human_required_count,
                "not_applicable": self.not_applicable_count,
                "unsupported": self.unsupported_count,
            },
            "rule_counts": dict(self.rule_counts),
            "element_counts": dict(self.element_counts),
            "aggregate_counts": dict(self.aggregate_counts),
            "human_reason_breakdown": dict(self.human_reason_breakdown),
            "waiver_breakdown": dict(self.waiver_breakdown),
        }


def build_model_summary(results: List[RuleResult], elements: List[Element], rules: List[Any]) -> ModelSummary:
    """Compute a ModelSummary from a list of RuleResults.

    Aggregate-scope (class/model) results contribute only to ``aggregate_counts``
    and not to the pair/element axes.
    """
    s = ModelSummary(
        total_elements=len(elements),
        total_rules=len(rules),
    )
    # Pair axis (and waiver/human-reason sub-set views) - skip aggregate-scope rows
    rule_status_seen: Dict[str, set] = {}
    element_status_seen: Dict[str, set] = {}
    for r in results:
        if r.scope in ("class", "model"):
            key_map = {
                STATUS_PASS: "pass", STATUS_FAIL: "fail",
                STATUS_INCONCLUSIVE: "inconclusive",
                STATUS_HUMAN_REQUIRED: "human_required",
                STATUS_NOT_APPLICABLE: "not_applicable",
                STATUS_UNSUPPORTED: "unsupported",
            }
            bucket = key_map.get(r.status, "inconclusive")
            s.aggregate_counts[bucket] = s.aggregate_counts.get(bucket, 0) + 1
            continue

        if r.status == STATUS_PASS:
            s.pass_count += 1
        elif r.status == STATUS_FAIL:
            s.fail_count += 1
            if r.waiver is None or r.waiver_state in (None, "invalid", "superseded"):
                s.waiver_breakdown["open_fails"] += 1
            else:
                s.waiver_breakdown["waived_fails"] += 1
        elif r.status == STATUS_HUMAN_REQUIRED:
            s.human_required_count += 1
        elif r.status == STATUS_NOT_APPLICABLE:
            s.not_applicable_count += 1
        elif r.status == STATUS_UNSUPPORTED:
            s.unsupported_count += 1
        else:
            s.inconclusive_count += 1

        if r.waiver_state == "invalid":
            s.waiver_breakdown["invalid_waivers"] += 1
        elif r.waiver_state == "superseded":
            s.waiver_breakdown["superseded_waivers"] += 1

        if r.human_reason:
            s.human_reason_breakdown[r.human_reason] = (
                s.human_reason_breakdown.get(r.human_reason, 0) + 1
            )

        rule_status_seen.setdefault(r.rule_id, set()).add(r.status)
        if r.element_id:
            element_status_seen.setdefault(r.element_id, set()).add(r.status)

    # Rule axis
    evaluated_rules = set(rule_status_seen.keys())
    s.rule_counts["evaluated"] = len(evaluated_rules)
    if rules:
        all_rule_ids = {r.get("id") for r in rules if isinstance(r, dict) and r.get("id")}
        s.rule_counts["skipped"] = len(all_rule_ids - evaluated_rules)
    for statuses in rule_status_seen.values():
        if STATUS_FAIL in statuses:
            s.rule_counts["any_fail"] += 1
        if STATUS_HUMAN_REQUIRED in statuses:
            s.rule_counts["any_human_required"] += 1

    # Element axis
    s.element_counts["touched"] = len(element_status_seen)
    s.element_counts["untouched"] = max(len(elements) - len(element_status_seen), 0)
    for statuses in element_status_seen.values():
        if STATUS_FAIL in statuses:
            s.element_counts["any_fail"] += 1
        if STATUS_HUMAN_REQUIRED in statuses:
            s.element_counts["any_human_required"] += 1

    return s


@dataclass
class CoverageGap:
    """Structured gap object describing why a rule-element pair could not be checked.

    ``provider_route`` is the P-card resolved from ``ROUTING_REGISTRY`` at
    coverage-gap construction time.
    """
    rule_id: str
    gap_type: str = ""          # "data_missing" | "geometry_limited" | "external_missing" | "human_required" | "unsupported" | "data_tagging_gap" | "performance_gap" | "readiness_issue"
    affected_elements: List[str] = field(default_factory=list)
    missing_data: str = ""
    suggested_action: str = ""
    responsible_actor: str = ""  # actor string from the routing registry
    provider_route: str = ""    # P-card from the routing registry

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ClauseCoverage:
    """Maps a regulation clause to its implementing rules."""
    clause_ref: str
    rule_ids: List[str] = field(default_factory=list)
    automatable: bool = True
    rationale: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Architecture Hardening Models
# ---------------------------------------------------------------------------

AUTHORITY_HIERARCHY = [
    "statute",
    "regulation",
    "circular",
    "local_rule",
    "project_condition",
    "guideline",
    "best_practice",
]


@dataclass
class RulePackManifest:
    """Rule pack metadata and manifest."""
    pack_id: str
    version: str = "1.0.0"
    jurisdiction: str = ""
    author: str = ""
    description: str = ""
    required_check_types: List[str] = field(default_factory=list)
    governance_status: str = "draft"  # draft | review | published | deprecated
    effective_date: Optional[str] = None
    superseded_date: Optional[str] = None
    rule_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OverrideRecord:
    """Audit trail entry for rule override events."""
    rule_id: str
    overridden_by: str
    reason: str = ""
    override_type: str = "implicit"
    winning_authority: str = ""
    losing_authority: str = ""
    timestamp: str = field(default_factory=_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Contradiction:
    """Cross-rule contradiction record."""
    element_id: str
    rule_id_a: str
    rule_id_b: str
    status_a: str
    status_b: str
    description: str = ""
    clause_ref_a: str = ""
    clause_ref_b: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Engine Capability Models
# ---------------------------------------------------------------------------

@dataclass
class EnrichmentValue:
    """Per-source provenance for enriched properties."""
    value: Any = None
    source: str = ""
    confidence: float = 0.0
    timestamp: str = field(default_factory=_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StageExitCriteria:
    """Configurable minimum thresholds per design stage."""
    stage_name: str
    min_checkability_pct: float = 0.0
    max_unresolved_critical: int = 0
    require_coverage_gaps_identified: bool = True

    def evaluate(self, results: List["RuleResult"]) -> Dict[str, Any]:
        """Evaluate results against criteria."""
        total = len(results)
        if total == 0:
            return {"met": False, "reason": "No results to evaluate"}

        checkable = sum(1 for r in results if r.status in (STATUS_PASS, STATUS_FAIL))
        checkability_pct = (checkable / total) * 100
        critical_unresolved = sum(
            1 for r in results
            if r.status == STATUS_FAIL and r.severity == "critical"
        )

        checks = {
            "checkability_met": checkability_pct >= self.min_checkability_pct,
            "checkability_pct": round(checkability_pct, 1),
            "critical_met": critical_unresolved <= self.max_unresolved_critical,
            "critical_unresolved": critical_unresolved,
        }
        checks["met"] = checks["checkability_met"] and checks["critical_met"]
        return checks

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Default exit criteria per stage
DEFAULT_STAGE_CRITERIA = {
    STAGE_CONCEPT: StageExitCriteria(
        stage_name="concept",
        min_checkability_pct=30.0,
        max_unresolved_critical=999,
    ),
    STAGE_SCHEMATIC: StageExitCriteria(
        stage_name="schematic",
        min_checkability_pct=70.0,
        max_unresolved_critical=5,
    ),
    STAGE_SUBMISSION: StageExitCriteria(
        stage_name="submission",
        min_checkability_pct=95.0,
        max_unresolved_critical=0,
    ),
}


def register_stage(name: str, order: int, criteria: Optional[StageExitCriteria] = None) -> None:
    """Register a lifecycle stage so deployments can declare their own vocabulary.

    The bundled three stages (concept/schematic/submission) remain the default
    seed; this entry point lets a deployment using RIBA Plan of Work, ISO 19650,
    or a custom framework add additional stage names without forking core
. ``order`` controls comparison against ``min_stage`` on a
    rule; lower order = earlier in the lifecycle.
    """
    if not name:
        raise ValueError("Stage name must be a non-empty string")
    VALID_STAGES.add(name)
    STAGE_ORDER[name] = int(order)
    if criteria is not None:
        DEFAULT_STAGE_CRITERIA[name] = criteria


# ---------------------------------------------------------------------------
# Waiver record
# ---------------------------------------------------------------------------

WAIVER_STATE_APPLIED = "applied"
WAIVER_STATE_INVALID = "invalid"        # waiver no longer in force (any reason below)
WAIVER_STATE_SUPERSEDED = "superseded"  # underlying check now passes - waiver no longer applies

# Reasons a waiver is marked "invalid". Set on RuleResult.waiver_invalidation_reason.
WAIVER_INVALIDATION_RULE_VERSION_CHANGED = "rule_version_changed"
WAIVER_INVALIDATION_PAST_EXPIRY_DATE = "past_expiry_date"
WAIVER_INVALIDATION_OCCASION_EXHAUSTED = "occasion_exhausted"
WAIVER_INVALIDATION_IDENTITY_MISMATCH = "identity_mismatch"


@dataclass
class WaiverRecord:
    """Attached to a FAIL when an authorised actor accepts the deviation.

    Status remains FAIL; the waiver is additional evidence rendered separately
    in every report. Identity is the 5-tuple
    (rule_pack_id, rule_id, rule_version, element_id, project_id) — any
    mismatch leaves the new FAIL open. ``occasion`` and ``expires_at`` drive
    staleness; ``extra`` is the per-deployment / per-research extension slot.
    """
    waiver_id: str
    finding_key: str = ""
    rule_id: str = ""
    rule_pack_id: str = ""
    rule_version: str = ""
    element_id: str = ""
    project_id: str = ""
    granted_by: str = ""
    authority_basis: str = ""
    rationale: str = ""
    conditions: List[str] = field(default_factory=list)
    occasion: str = "one-time"
    consideration: Optional[str] = None
    expires_at: Optional[str] = None
    granted_at: str = field(default_factory=_now_iso)
    supersedes: Optional[str] = None
    notes: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def matches_identity(
        self,
        rule_pack_id: str,
        rule_id: str,
        rule_version: str,
        element_id: str,
        project_id: str,
    ) -> bool:
        return (
            self.rule_pack_id == rule_pack_id
            and self.rule_id == rule_id
            and self.rule_version == rule_version
            and self.element_id == element_id
            and self.project_id == project_id
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Routing registry
# ---------------------------------------------------------------------------
# One registry covers gate types, human reasons, and confidence dimensions.
# ``kind`` is a section tag for report grouping, not a separate namespace -
# every category lives in one keyspace so register_route() is the single
# entry point.

ROUTE_KIND_GATE = "gate"
ROUTE_KIND_HUMAN_REASON = "human_reason"
ROUTE_KIND_CONFIDENCE_DIM = "confidence_dim"


@dataclass
class RouteEntry:
    category: str
    action: str
    actor: str
    provider: str
    kind: str  # one of ROUTE_KIND_*

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


ROUTING_REGISTRY: Dict[str, RouteEntry] = {}


def register_route(
    category: str,
    action: str,
    actor: str,
    provider: str,
    kind: str = ROUTE_KIND_GATE,
) -> None:
    """Register or overwrite a routing entry.

    Categories are unique across gate / human_reason / confidence_dim kinds
    so a deployment can re-target any built-in entry by re-registering with
    the same name.
    """
    if not category:
        raise ValueError("Route category must be a non-empty string")
    if kind not in (ROUTE_KIND_GATE, ROUTE_KIND_HUMAN_REASON, ROUTE_KIND_CONFIDENCE_DIM):
        raise ValueError(f"Unknown route kind: {kind!r}")
    ROUTING_REGISTRY[category] = RouteEntry(category, action, actor, provider, kind)


def resolve_route(category: Optional[str]) -> Optional[RouteEntry]:
    """Look up a routing entry; ``None`` if the category is unknown."""
    if not category:
        return None
    return ROUTING_REGISTRY.get(category)


# Seed: gate categories (these supersede the historical _GAP_ACTIONS rows).
register_route(
    "data_missing",
    "Add missing properties in authoring tool or run enrichment",
    "BIM_manager", "P4", ROUTE_KIND_GATE,
)
register_route(
    "geometry_limited",
    "Export model with valid geometry for affected elements",
    "BIM_manager", "P3", ROUTE_KIND_GATE,
)
register_route(
    "external_missing",
    "Provide required external data (zoning, plot boundary, etc.)",
    "enrichment_provider", "P2", ROUTE_KIND_GATE,
)
register_route(
    "unsupported",
    "Register or install the required check type package",
    "rule_author", "P1/P8", ROUTE_KIND_GATE,
)
register_route(
    "performance_gap",
    "Run the required simulation and supply the result",
    "simulation_provider", "P7", ROUTE_KIND_GATE,
)
register_route(
    "readiness_issue",
    "Resolve upstream model readiness / IDS / clash check before re-run",
    "model_author", "P6", ROUTE_KIND_GATE,
)
register_route(
    "data_tagging_gap",
    "Tag the present elements with the property values the rule requires",
    "BIM_manager", "P4", ROUTE_KIND_GATE,
)
register_route(
    "human_required",
    "Manual review required — rule involves subjective judgment",
    "reviewer", "P10", ROUTE_KIND_GATE,
)

# Seed: human reasons.
register_route(
    "subjective_rule",
    "Manual review of intrinsically subjective rule (aesthetics, intent)",
    "reviewer", "P10", ROUTE_KIND_HUMAN_REASON,
)
register_route(
    "qualitative_threshold",
    "Run performance simulation to supply qualitative evidence",
    "simulator", "P7", ROUTE_KIND_HUMAN_REASON,
)
register_route(
    "deviation_request",
    "Compliance officer to consider deviation / sign off the FAIL",
    "compliance_officer", "P10", ROUTE_KIND_HUMAN_REASON,
)
register_route(
    "contradiction_adjudication",
    "Two rules disagree on the same element — rule author to adjudicate",
    "rule_author", "P1", ROUTE_KIND_HUMAN_REASON,
)
register_route(
    "authority_dispute",
    "Equal-rank rules conflict — rule author / authority to resolve",
    "rule_author", "P1", ROUTE_KIND_HUMAN_REASON,
)
register_route(
    "irregular_geometry",
    "Reviewer to verify clear corridor width through irregular space (lift core, alcoves, branches)",
    "reviewer", "P10", ROUTE_KIND_HUMAN_REASON,
)

# Seed: confidence dimensions - the closed-loop enricher feedback channel.
register_route(
    "semantic_confidence_low",
    "Improve semantic enrichment (run domain enricher / verify class tags)",
    "enricher", "P4", ROUTE_KIND_CONFIDENCE_DIM,
)
register_route(
    "geometry_confidence_low",
    "Re-export with valid geometry or run geometry refinement",
    "BIM_manager", "P3", ROUTE_KIND_CONFIDENCE_DIM,
)
register_route(
    "topology_confidence_low",
    "Repair connectivity / re-run topology enricher",
    "BIM_manager", "P3", ROUTE_KIND_CONFIDENCE_DIM,
)
