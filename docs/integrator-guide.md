# ARC External Integrator Guide

**For organizations and developers building connections to ARC - rule providers, enrichment services, simulation engines, BIM platforms, AI systems, and municipal review workflows.**

---

## How ARC Fits the Ecosystem

ARC is the **evidence engine** at the center of a compliance checking workflow. It does not try to do everything - it accepts inputs from external systems, runs transparent checks, and produces structured output for downstream consumers.

```
                          ┌---------------------┐
  Enrichment Providers -->|                     |--> Reports (JSON/HTML/PDF/BCF)
  Simulation Engines ---->|                     |--> Permit Portals
  External Data Sources ->|     ARC Engine      |--> BIM Coordination Tools
  Rule Pack Authors ----->|  (evidence engine)  |--> CI/CD Pipelines
  Quality Gate Tools ---->|                     |--> Human Review Workflows
                          `---------------------┘
                                  ^
                    IFC Models via:
                    - CLI (headless)
                    - Blender/Bonsai (interactive)
                    - Speckle (webhook-triggered)
                    - Revit (via IFC export or Speckle)
```

### Nine External Actor Categories

| # | Category | What They Provide to ARC | What They Consume from ARC |
|---|----------|--------------------------|---------------------------|
| 1 | **Enrichment Providers** | Property classification, inferred attributes | Coverage gaps (what properties are missing) |
| 2 | **Simulation Providers** | Fire spread results, structural analysis, daylight factors | Element data to simulate, rule parameters |
| 3 | **Rule Pack Authors** | JSON/Python rules, clause ledgers, pack manifests | Check type registry, rule schema docs |
| 4 | **Integration Platforms** | IFC models, triggering mechanisms | Results JSON, BCF issues |
| 5 | **Quality Gate Tools** | Validated geometry, IDS conformance | Pre-check data quality status |
| 6 | **Check Type Extensions** | New check type implementations | Registration API, element/context data |
| 7 | **External Data Sources** | Zoning maps, plot boundaries, regulatory tables | Data requirements from rule schemas |
| 8 | **Human Actors** | Expert judgment, override decisions | Evidence bundles, coverage reports |
| 9 | **AI Agents** | Model corrections, enrichment, generated rules | Statuses, measurements, reasons, routing - as a machine-readable feedback contract |

---

## 1. Enrichment Providers

**What:** Services that add or infer properties on building elements before ARC checks them.

**Examples:** ML space classifiers, GNN-based property enrichers, ILS Space, knowledge graph systems, manual classification tools.

**Integration pattern:**

```
IFC Model -> Enrichment Service -> Enriched IFC/JSON -> ARC Engine
```

**What ARC provides to you:**
- **Coverage gap reports** tell you exactly which properties are missing and for which elements
- Gap type `data_missing` with `responsible_actor: "enrichment_provider"` identifies your work
- Element list with existing properties (from JSON results) so you know what's already there

**What you provide to ARC:**
- Enriched element properties (added to the IFC model or provided as a property overlay)
- Provenance metadata: which enrichment service produced which values, with confidence scores

**Data format for enrichment overlay:**

```json
{
  "element_id": "2O2Fr$t4X7Z8xBndo$JYNI",
  "enrichments": {
    "SpaceType": {
      "value": "Corridor",
      "source": "ML_classifier_v3",
      "confidence": 0.92,
      "timestamp": "2026-04-04T12:00:00Z"
    }
  }
}
```

**ARC's `EnrichmentValue` dataclass** supports per-source provenance - when multiple enrichment sources disagree, the highest-confidence value is used, and all sources are recorded in the evidence bundle.

**Key principle:** ARC faithfully checks what it receives. If your enrichment is wrong, ARC will produce an incorrect finding with full provenance showing exactly which enrichment led to that result. This is by design - auditability over hidden correction.

### Semantic Checkers as Enrichment Sources

Semantic validation systems can feed enriched data into ARC elements **before** rule execution. These include:

- **NLP-based clause parsers** - extract structured parameters from regulation text and attach them as element or rule metadata
- **Ontology validators** - verify that IFC classifications align with domain ontologies (e.g., buildingSMART Data Dictionary, UniClass, OmniClass) and flag misclassified elements
- **Classification verifiers** - confirm that space types, material designations, and occupancy labels are consistent and complete

**Integration pattern:** Semantic checkers run upstream of ARC. They write corrected or enriched properties (classifications, inferred space types, validated material labels) onto elements. ARC then evaluates rules against these enriched properties, with full provenance tracing back to the semantic source.

---

## 2. Simulation Providers

**What:** External simulation engines whose results feed into ARC compliance checks.

**Examples:** FDS (fire dynamics), ETABS/SAP2000 (structural), EnergyPlus (energy), Radiance (daylight), CFD tools (ventilation).

**Integration pattern:**

```
IFC Model -> Simulation Engine -> Simulation Results -> ARC (as external data or element properties)
```

**How to connect:**

1. **Property injection:** Add simulation results as element properties before running ARC
   - Example: FDS fire spread time -> element property `fire_spread_time_s`
   - ARC rules can then use `property_min`/`property_max` to check thresholds

2. **External data files:** Provide results as JSON/GeoJSON that rules reference
   - Example: Daylight factor grid as GeoJSON -> rule checks `daylight_factor >= 2.0`
   - ARC validates external data structure before use (see `arc/core/validators.py`)

3. **Custom check types:** For complex simulation-dependent checks, publish an extension package
   - Your check type handler can invoke your simulation engine or read cached results

**What ARC provides to you:**
- Element geometry (AABB or polygon) for simulation model setup
- Room connectivity graph (topology) for network-based simulations
- Standard result format to write your findings back into

---

## 3. Rule Pack Authors

**Who:** Researchers, consultants, government bodies, standards organizations, AI-assisted rule generators.

**What you produce:** Rule packs - sets of rules for a specific jurisdiction, building type, or compliance domain.

### Publishing a Rule Pack

**Directory structure:**

```
my_pack/
  pack_manifest.json         # Required: pack metadata
  json_rules/
    fire_safety.json         # JSON rules (any number of files)
    accessibility.json
  python_rules/
    complex_egress.py        # Python rules (optional)
  clause_ledger.json         # Required: regulation-to-rule mapping
```

**Pack manifest example:**

```json
{
  "pack_id": "eu_epbd_2024",
  "version": "1.0.0",
  "jurisdiction": "European Union",
  "author": "Energy Performance Research Group",
  "description": "Energy Performance of Buildings Directive 2024 spatial requirements",
  "required_check_types": ["min_area", "ratio", "property_min"],
  "governance_status": "draft",
  "effective_date": "2024-01-01",
  "rule_ids": ["EPBD_VENT_01", "EPBD_VENT_02", "EPBD_LIGHT_01"]
}
```

**Governance status lifecycle:** `draft` -> `review` -> `published` -> `deprecated`

**Clause ledger is mandatory.** Every relevant regulation clause must be listed - even those you *cannot* automate:

```json
{
  "clause_ref": "EPBD 2024, Art. 7 Section 3 - Indoor Air Quality Assessment",
  "rule_ids": [],
  "automatable": false,
  "rationale": "Requires on-site measurement or simulation not available from IFC geometry alone"
}
```

This is the foundation of ARC's coverage transparency.

### AI-Assisted Rule Generation

If you're using LLMs or NLP to convert regulation text to ARC rules:

1. **Output format:** Generate the JSON rule schema documented above
2. **Include `interpretation_notes`:** Document how ambiguous text was interpreted
3. **Include `source`:** Exact clause reference for traceability
4. **Mark confidence:** Use `governance_status: "draft"` until human-reviewed
5. **Generate the clause ledger:** Map all clauses, marking non-automatable ones explicitly

**ARC validates rule schemas** via `arc/core/validators.py` - call `validate_rule_schema(rule_dict)` to check your generated rules before publishing.

### Authority and Precedence

Rules can declare an `authority` level:

```
statute > regulation > circular > local_rule > project_condition > guideline > best_practice
```

When two rules share the same ID, the higher-authority rule wins. This enables jurisdiction-specific overrides of generic rule packs.

### Temporal Versioning

Rules can declare `effective_date` and `superseded_date`. Users filter with `--regulation-date` to get rules applicable at a specific point in time. Use this for regulation amendments and sunset clauses.

---

## 4. Integration Platforms

**What:** BIM software, model hosting platforms, and CI/CD systems that trigger ARC and consume results.

### Blender / Bonsai

ARC ships as a native Blender extension. No additional integration needed.

- IFC loading via Bonsai's in-memory model
- Interactive N-panel UI
- 3D visualization of results
- BCF export for coordination

### Speckle

**Pattern:** Webhook-triggered headless pipeline.

```
Speckle Model Update -> Webhook -> CI Runner -> arc-check --ifc -> Results -> Speckle Comments/BCF
```

**Implementation:**

1. Export IFC from Speckle stream (using Speckle's IFC connector)
2. Run `arc-check --ifc exported.ifc --output results/`
3. Parse `compliance_results.json` for programmatic consumption
4. Post BCF issues back to Speckle or create comments via Speckle API

**Delta comparison** is valuable here: run with `--baseline` pointing to the previous run's results to show what changed.

### GitHub Actions / CI/CD

```yaml
# .github/workflows/compliance.yml
name: ARC Compliance Check
on:
  push:
    paths: ['models/**']
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - run: |
          uv venv && source .venv/bin/activate
          uv pip install spatial-arc ifcopenshell
          arc-check --ifc models/building.ifc --output results/
      - uses: actions/upload-artifact@v4
        with:
          name: compliance-report
          path: results/
```

### Revit

Two integration paths:

**Path A: Speckle CI/CD (zero Revit plugin)**
- Designer pushes model to Speckle from Revit
- Speckle webhook triggers ARC headless
- Results posted back as BCF or Speckle comments
- No C# code needed

**Path B: IFC Export Pipeline**
- Export IFC from Revit (native or via IFC exporter)
- Run `arc-check --ifc exported.ifc`
- Import BCF results back into Revit coordination tools

**Path C: Direct Plugin (future)**
- C# Revit add-in that calls ARC via subprocess or REST API
- Would require ARC running as a local service (FastAPI wrapper)

### ThatOpen (IFC.js / web-based)

- Use ARC's JSON output (`compliance_results.json`) in web viewers
- Element GUIDs in results map directly to IFC GlobalId
- Status colors defined in `_status_color()` for consistent rendering

### Geometry Engine Providers

Blender/Bonsai is the current interactive geometry engine, but **ARC-Core has zero geometry engine dependency**. It works with any system that provides `Element` objects populated with AABB or polygon data.

**Supported and potential geometry providers:**

| Provider | Integration Path |
|----------|-----------------|
| **Blender / Bonsai** | Native extension (current default) |
| **Revit** | IFC export or Speckle pipeline; future direct API via C# subprocess |
| **ThatOpen / IFC.js** | Browser-based IFC parsing, feed elements to ARC via REST API |
| **FreeCAD** | Python-native IFC import, can call ARC-Core directly |
| **Trimble Connect** | IFC export or API-based element extraction |
| **IfcOpenShell (headless)** | CLI/Python - no GUI needed; used by `arc-check` today |
| **Any IFC-capable system** | Anything that produces Element objects with AABB/polygon geometry |

**The contract is simple:** provide ARC with `Element` objects that have bounding box coordinates (`aabb_min`, `aabb_max`) and/or polygon vertices, plus IFC properties. ARC handles all rule evaluation, evidence generation, and reporting from there.

### Flask / FastAPI Wrapper

For web-hosted scenarios, wrap ARC as a REST API:

```python
from fastapi import FastAPI, UploadFile
from arc.core.cli import main as arc_main

app = FastAPI()

@app.post("/check")
async def check(ifc_file: UploadFile):
    # Save uploaded file, run ARC, return JSON results
    ...
```

---

## 5. Quality Gate Tools

**What:** Tools that validate data quality *before* ARC runs compliance checks.

**An Extensible Quality Pipeline:**

The quality checking pipeline is open-ended. Each project can compose the gates it needs, in the order that makes sense. Common steps include:

```
Step: DATA QUALITY (IDS/IfcTester)           -> Information presence, schema conformance
Step: CLASH DETECTION (Navisworks etc.)      -> Physical intersections
Step: SPATIAL COMPLIANCE (ARC)               -> Regulatory spatial requirements
Step: SEMANTIC CHECKING                      -> Classification & ontology validation
Step: SIMULATION VALIDATION                  -> Energy, fire, daylight, structural
Step: CONSTRUCTABILITY ANALYSIS              -> Buildability & sequencing feasibility
Step: SUSTAINABILITY ASSESSMENT              -> Embodied carbon, material passports
Step: DOMAIN-SPECIFIC VALIDATION             -> Any custom quality gate plugin
```

IDS conformance, clash detection, and spatial compliance (ARC) are the most common steps, but the architecture supports **arbitrary quality gate plugins**. Organizations can add, remove, or reorder steps to match their review process.

**Integration pattern:**

1. **IDS validators** (IfcTester): Run first to ensure required properties exist
2. **Geometry validators** (val3dity): Verify mesh quality before ARC uses AABB/polygon
3. **Clash detection**: Resolve physical conflicts before checking spatial compliance
4. **Semantic checkers**: Validate classifications, ontology alignment, naming conventions
5. **ARC**: Runs after upstream quality gates pass
6. **Additional gates**: Simulation validation, constructability, sustainability, or any domain-specific plugin

**ARC's checkability gate** reports when upstream quality issues affect checking - `INCONCLUSIVE` with gap type `data_missing` or `geometry_limited` indicates issues in earlier pipeline steps.

---

## 6. Check Type Extension Packages

**What:** pip-installable packages that add new check types to ARC.

**The pattern:** `pip install arc-fire-checks` and new check types are immediately available.

### Creating an Extension Package

**Package structure:**

```
arc-fire-checks/
  pyproject.toml
  arc_fire_checks/
    __init__.py
    checks.py
```

**`pyproject.toml`:**

```toml
[project]
name = "arc-fire-checks"
version = "0.1.0"
dependencies = ["spatial-arc>=0.1.0"]

[project.entry-points."arc.check_types"]
fire_checks = "arc_fire_checks:register"
```

**`__init__.py`:**

```python
def register():
    from arc.core.rule_engine import register_check_type, RuleEngine
    from arc.core.data_models import RuleResult, STATUS_PASS, STATUS_FAIL

    def _check_fire_separation(engine, params, element, context, common):
        min_dist = params.get("min_distance", 3.0)
        target_class = params.get("fire_wall_class", "IfcWall")
        # ... implementation using context.spatial_index
        pass

    register_check_type("fire_separation", _check_fire_separation, needs_context=True)
```

**ARC auto-discovers** extensions via `importlib.metadata.entry_points` at startup.

### Available APIs for Extension Authors

| API | Import | Purpose |
|-----|--------|---------|
| `register_check_type(name, handler, ...)` | `from arc.core.rule_engine import register_check_type` | Register new check types |
| `Element`, `RuleResult`, statuses | `from arc.core.data_models import ...` | Data structures |
| `aabb_*` functions | `from arc.core.geo_engine import ...` | Geometry calculations |
| `SpatialIndex` | `from arc.core.geo_engine import SpatialIndex` | Proximity queries |
| `Context` | `from arc.core.context import Context` | Execution context |

---

## 7. External Data Sources

**What:** Data not contained in the IFC model that rules need for checking.

**Examples:**
- GeoJSON site boundaries and zoning maps
- Regulatory parameter tables (FSI limits by zone, fire rating by occupancy)
- Project-specific conditions (variances, special permits)
- Environmental data (wind zones, seismic zones, rainfall data)
- Municipal plot data (setbacks, road widths)

### Providing External Data

External data can be:

1. **Element properties:** Pre-process and add to elements before ARC runs
2. **Rule parameters:** Include directly in rule JSON (`params` field)
3. **External files:** Referenced by Python rules that load them at execution time

**ARC validates GeoJSON and JSON external data** via `arc/core/validators.py`:

```python
from arc.core.validators import validate_geojson, validate_external_json

warnings = validate_geojson(my_geojson_data)
warnings = validate_external_json(my_data, required_keys=["zone", "fsi_limit"])
```

### Coverage Gap Feedback Loop

When a rule needs external data that isn't available, ARC reports:
- Gap type: `external_missing`
- Responsible actor: `enrichment_provider`
- Suggested action: "Provide required external data (zoning, plot boundary, etc.)"

This creates a **feedback loop**: run ARC -> see what's missing -> provide data -> re-run -> more rules become checkable.

---

## 8. Human-in-the-Loop Setups

**What:** Workflows where human experts review ARC findings and make compliance decisions.

### ARC's Decision Boundary

ARC generates **evidence**, not **verdicts**. The 6-status taxonomy enforces this:

- `PASS`/`FAIL` = machine finding with full evidence
- `HUMAN_REQUIRED` = explicitly flagged for human judgment
- `INCONCLUSIVE` = data gap, not a judgment

**A PASS from ARC is not an approval.** It means: "given this rule, this element, and this measurement method, the measured value meets the threshold. Here is the evidence."

### Municipal Advisory Mode

For pilot deployments with municipalities:

1. Run ARC in `pre_compliance` mode
2. Review evidence bundles - each finding traces to rule, element, geometry tier, measurement
3. Human reviewer makes institutional decision (approve, conditional approve, reject)
4. Override decisions are logged separately (not in ARC - in the permit management system)

**ARC's coverage report** is the key document: "Of 200 rule-element pairs, 148 were fully checkable (74%). 32 need BIM data improvements, 12 need external data, 8 require human judgment."

### Expert Review Workflow

```
ARC Run -> Evidence Report -> Expert Reviews Findings -> Decision Record
                                    ↓
                          Questions about methodology?
                          -> Check evidence bundle:
                            - geometry_tier: "aabb" (approximate)
                            - measurement_method: "aabb_min_horizontal_dim"
                            - confidence: "approximate"
                            - assumptions: ["Clear width from AABB, frame offset not modeled"]
```

---

## Data Formats Reference

### ARC Results JSON (v6 Payload)

```json
{
  "version": 6,
  "model_summary": {
    "total_elements": 42,
    "total_rules": 12,
    "pass_count": 16,
    "fail_count": 6,
    "inconclusive_count": 4,
    "human_required_count": 0,
    "not_applicable_count": 0,
    "unsupported_count": 0,
    "compliance_score": 0.7273,
    "pair_counts":      {"pass": 16, "fail": 6, "inconclusive": 4, "...": 0},
    "rule_counts":      {"evaluated": 8, "skipped": 4, "any_fail": 3, "any_human_required": 0},
    "element_counts":   {"touched": 39, "untouched": 3, "any_fail": 5, "any_human_required": 0},
    "aggregate_counts": {"pass": 0, "fail": 0, "inconclusive": 1, "not_applicable": 3, "...": 0},
    "waiver_breakdown": {"open_fails": 5, "waived_fails": 1, "invalid_waivers": 0, "superseded_waivers": 0},
    "human_reason_breakdown": {"subjective_rule": 0}
  },
  "evidence_summary": {
    "results_with_evidence": 22,
    "geometry_tier_distribution": {"aabb": 18, "property": 4},
    "confidence_distribution": {"approximate": 18, "exact": 4}
  },
  "coverage_gaps": [...],
  "clause_coverage": [...],
  "element_results": [
    {
      "rule_id": "NBC_ACC_04",
      "element_id": "2O2Fr$t4X7Z8xBndo$JYNI",
      "status": "FAIL",
      "scope": "element",
      "affected_element_ids": [],
      "human_reason": "deviation_request",
      "arrival_path": "from_fail:deviation_request",
      "provider_route": "P10",
      "waiver_state": "applied",
      "waiver": {
        "waiver_id": "w_2026_042",
        "rule_pack_id": "nbc_dcr_india_v1",
        "rule_version": "1.0.0",
        "granted_by": "compliance_officer_42",
        "rationale": "Heritage building; 1.0m door retained per conservation order.",
        "occasion": "until_next_submission"
      },
      "message": "Width 0.800 m < 0.900 m minimum",
      "evidence": {"geometry_tier": "aabb", "...": "..."}
    },
    {
      "rule_id": "stair_count",
      "element_id": "class:IfcStair",
      "status": "PASS",
      "scope": "class",
      "affected_element_ids": ["g1...", "g2..."],
      "provider_route": null
    }
  ]
}
```

**Dimensional axes (plan Section 3.5).** `pair_counts` are element-scope results;
`aggregate_counts` are class/model-scope results; `rule_counts` and
`element_counts` count distinct rules / elements that fired or have any
failing finding. No total mixes axes. `compliance_score` is the historical
pair-axis ratio.

### BCF Issues (scope-aware)

ARC exports BCF-style issues. Each FAIL result becomes a topic with:
- `element_guids` - list (always populated; length 1 for element scope, N for class/model scope)
- `scope` - `"element" | "class" | "model"`
- `anchor` - the GUID or sentinel (`class:IfcStair`, `model:`)
- `human_reason`, `arrival_path`, `provider_route`, `waiver` - when applicable

A class-scope finding referencing 4 stairs becomes **one** BCF issue with
four components, not four issues.

---

## Routing, Waivers, and Deviation Tracking

Reference: `docs/specifications/data-model-schema.md` Section 11 (routing and deviation contract).

### Provider Routes

Every routable result carries a `provider_route` (P-card) resolved against
`ROUTING_REGISTRY`. The registry maps gate categories, human reasons, and
confidence dimensions to (action, actor, provider) tuples - call
`register_route(...)` once at deployment startup to add or override entries.

| If you see in `provider_route` | Your role |
|--------------------------------|-----------|
| `P1`, `P8`                     | Rule author / check-type extension provider |
| `P2`                           | External data source (zoning, plot, regulatory tables) |
| `P3`                           | BIM modeller / geometry engine |
| `P4`                           | Enrichment provider |
| `P5`                           | Semantic checker / classifier |
| `P6`                           | Quality gate (IDS, clash detection, readiness) |
| `P7`                           | Simulation provider |
| `P9`                           | Domain expert (per-deployment) |
| `P10`                          | Reviewer / compliance officer |

### Waivers (Accepted Deviations)

ARC distinguishes "FAIL (open)", "FAIL (waived)", and "FAIL (waiver stale)".
A waiver is additional evidence - status stays FAIL. Identity is the
5-tuple `(rule_pack_id, rule_id, rule_version, element_id, project_id)`.
Any mismatch leaves the FAIL open. Waivers ride in via `Context.waivers`
at runtime; they are never committed alongside rules. See the Data Quality
Playbook Section 7 for the wiring recipe.

### Scope-aware Findings

Rules may declare `scope: "class"` or `"model"` to emit one aggregate
finding instead of N synthetic per-element rows. Aggregate findings use
sentinel ids - `class:<key>` or `model:` - and list the elements they
examined under `affected_element_ids`. The `class:` / `model:` prefix is
the canonical marker downstream consumers use to detect non-element
findings.

---

## 9. AI Agents and Automated Remediation

**What:** LLM-driven or autonomous systems that read ARC output, act on it, and
re-run the checks. Recent work in this direction includes multi-agent BIM
authoring that iterates against rule-based feedback, schema-guided IFC querying
and field-level model modification, and LLM workflows that interpret regulations,
select extraction tools, and generate compliance reports.

ARC does not replace agent reasoning and does not certify a design. It provides
an **inspectable feedback contract**: a loop an agent can close, with an
explicit boundary marking where it must stop.

### The remediation loop

```
   ┌-------------------------------------------------┐
   |                                                 |
   ▼                                                 |
submit model --▶ arc-check --▶ results JSON --▶ agent reads
                                                 status + reason
                                                 + routing
                                                     |
                        ┌----------------------------┤
                        ▼                            ▼
                 machine-actionable            STOP - escalate
                 (FAIL, INCONCLUSIVE            (HUMAN_REQUIRED,
                  with a data gap)               subjective rules)
                        |
                        ▼
                 correct or enrich --▶ re-run --▶ compare with --baseline
```

### What makes each status actionable

| Status | Agent action | Signal to use |
|---|---|---|
| `FAIL` | Adjust geometry or property, re-run | `measured_value`, `expected_value`, `delta` |
| `INCONCLUSIVE` | Supply the missing input, re-run | `coverage_gaps`, `provider_route`, evidence `data_source` |
| `HUMAN_REQUIRED` | **Do not auto-resolve** - escalate | `human_reason`, routed actor |
| `UNSUPPORTED` | Register a check type, or report the capability gap | check type name |
| `NOT_APPLICABLE` | No action | reason field |
| `PASS` | No action - and not an approval | evidence bundle |

### The automation boundary

This is the part worth implementing carefully. `INCONCLUSIVE` and
`HUMAN_REQUIRED` are not interchangeable:

- **`INCONCLUSIVE` is a data gap.** An agent supplying the missing property or
  geometry and re-running is exactly the intended use.
- **`HUMAN_REQUIRED` is reserved for a person.** It means the determination
  depends on judgment ARC will not simulate. An agent that "resolves" these has
  broken the contract the evidence record exists to protect.

Treat the distinction as a hard gate in your agent loop, not a heuristic.

### Practical notes

- **Drive it headless.** `arc-check --ifc model.ifc --output results/` needs no
  Blender. Exit codes and the JSON payload are the interface.
- **Diff runs, do not re-read everything.** `--baseline results/compliance_results.json`
  reports new failures, resolved failures, and status changes - a compact reward
  signal for an iterating agent.
- **Rules stay governed.** Agent-generated rules should carry
  `governance_status: "draft"` and `interpretation_notes` until a human reviews
  them. See Section 3.
- **Keep evidence, not just verdicts.** The evidence bundle records measurement
  method, geometry tier, confidence, and assumptions. Discarding it loses the
  auditability that makes an agentic loop reviewable after the fact.
- **Never let an agent write waivers.** A waiver is an authority record. See the
  waiver section below.

---

## Getting Started Checklist

| I want to... | Start here |
|--------------|-----------|
| Check my IFC model | Install ARC, run `arc-check --ifc model.ifc` |
| Write rules for my jurisdiction | Read Part 3 above, create a rule pack |
| Connect Revit to ARC | See Section 4 (Integration Platforms) - Speckle CI/CD path |
| Build an enrichment service | See Section 1 - read coverage gaps to find what's needed |
| Add a simulation-based check | See Section 2 - property injection or extension package |
| Set up CI/CD compliance | See Section 4 - GitHub Actions example |
| Build a web-based checker | See Section 4 - FastAPI wrapper |
| Pilot with a municipality | See Section 8 - advisory mode workflow |
| Train an AI to write rules | See Section 3 - AI-assisted rule generation |
| Build an agent that fixes models | See Section 9 - remediation loop and the automation boundary |
| Publish a check type package | See Section 6 - entry-point extension protocol |
