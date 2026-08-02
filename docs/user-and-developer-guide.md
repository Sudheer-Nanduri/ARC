# ARC User & Developer Guide

**For users running compliance checks and developers extending ARC.**

---

## Part 1: ARC-Core CLI - Installing and Using

### 1.1 Installation

**Prerequisites:** Python 3.10+, `uv` (recommended) or `pip`.

```bash
# Clone the repository
git clone <repo-url> && cd ARC

# Create virtual environment
uv venv
source .venv/bin/activate          # Linux/macOS
# .venv\Scripts\activate           # Windows

# Install ARC with dev dependencies
uv pip install -e ".[dev]"

# Install optional dependencies
uv pip install ifcopenshell         # IFC file loading
uv pip install reportlab            # PDF report generation
uv pip install shapely              # Polygon geometry tier (auto-installed with ifcopenshell)
```

**Verify installation:**

```bash
arc-check --demo                    # Should produce reports in results/
arc-check --list-packs              # Should show available rule packs
python -m pytest tests/ -q          # Should pass all tests
```

### 1.2 Running Checks

**Demo mode** (no IFC file needed - uses built-in test elements):

```bash
arc-check --demo --output results/
```

**Single IFC file:**

```bash
arc-check --ifc path/to/model.ifc --output results/
```

**Federated models** (multiple IFC files from different disciplines):

```bash
arc-check --ifc arch.ifc struct.ifc mech.ifc \
          --disciplines architectural,structural,mechanical \
          --output results/
```

**Filtered by category:**

```bash
arc-check --ifc model.ifc --categories accessibility,fire_egress
```

**Filtered by regulation date** (temporal filtering):

```bash
arc-check --ifc model.ifc --regulation-date 2024-06-01
```

Rules with `effective_date` after the target or `superseded_date` before the target are excluded.

**Delta comparison** (track changes between model versions):

```bash
# First run
arc-check --ifc model_v1.ifc --output results_v1/

# Second run with baseline
arc-check --ifc model_v2.ifc --output results_v2/ --baseline results_v1/compliance_results.json
```

Produces `delta_report.json` showing new failures, resolved failures, and status changes.

### 1.3 Understanding the Output

**Terminal summary:**

```
  RESULTS:
    PASS:               14  (70%)
    FAIL:                1  (5%)
    INCONCLUSIVE:        5  (25%)
    HUMAN_REQUIRED:      0  (0%)
    NOT_APPLICABLE:      0  (0%)
    UNSUPPORTED:         0  (0%)

  Checkability:      15/20 fully checkable (75%)
  Critical failures: 1
  Coverage gaps:     3
```

**Key metric - Checkability:** The ratio of (PASS + FAIL) to total results. This tells you what percentage of rule-element pairs could actually be evaluated. The remainder needs action (add data, run enrichment, install extensions, or involve a human reviewer).

**Output files:**

| File | Purpose |
|------|---------|
| `compliance_results.json` | Machine-readable results with evidence bundles, coverage gaps, clause coverage |
| `compliance_report.html` | Visual dashboard - open in any browser |
| `compliance_report.pdf` | Print-ready report (requires reportlab) |
| `issues.bcfzip` | BCF issues for BIM coordination tools |
| `summary.txt` | Terminal summary saved to file |
| `delta_report.json` | Delta comparison (when `--baseline` is used) |

### 1.4 Understanding Coverage Gaps

Coverage gaps explain *why* something couldn't be checked, *who* should act,
and *which P-card* they belong to. Categories come from `ROUTING_REGISTRY`
(plan Section 3.2 / `Data Model Schema.md` Section 11.2) - call `register_route(...)` at
deployment startup to add or override entries.

| Gap Type            | Responsible Actor    | Provider | Typical Action |
|---------------------|----------------------|----------|----------------|
| `data_missing`      | BIM Manager          | P4       | Add missing properties in authoring tool |
| `geometry_limited`  | BIM Manager          | P3       | Export model with valid geometry |
| `external_missing`  | Enrichment Provider  | P2       | Provide zoning, plot boundary, or regulatory data |
| `human_required`    | Reviewer             | P10      | Manual assessment of subjective criteria |
| `unsupported`       | Rule Author          | P1/P8    | Install or develop the required check type |
| `performance_gap`   | Simulation Provider  | P7       | Run the required simulation (fire/light/energy) |
| `readiness_issue`   | Model Author         | P6       | Resolve upstream IDS / clash check failure |
| `data_tagging_gap`  | BIM Manager          | P4       | Class is present in the model but no element is tagged the way the rule needs |

Each coverage-gap entry in `compliance_results.json` carries
`provider_route` so a downstream router (Jira, Slack, ticket system) can
fan the finding out to the right team automatically.

### 1.4.1 Waivers and the Dimensional Summary

`compliance_results.json` carries a richer `model_summary` payload:

```text
pair_counts         (one per rule × element)
rule_counts         (evaluated / skipped / any_fail / any_human_required)
element_counts      (touched / untouched / any_fail / any_human_required)
aggregate_counts    (class- and model-scope results, never folded into pair)
waiver_breakdown    (open_fails / waived_fails / invalid_waivers / superseded_waivers)
human_reason_breakdown
```

`compliance_score` is the historical pair-axis ratio
`PASS / (PASS + FAIL)`. `rule_axis_score` is `(evaluated − any_fail) / evaluated`.

Waivers (accepted deviations) attach as additional evidence on a FAIL -
status never flips. Every waiver in scope is rendered visibly; no silent
acceptance. See the Data Quality Playbook Section 7 for the wiring recipe.

### 1.5 Understanding the Clause Ledger

The clause coverage table maps regulation sections to implementing rules:

```json
{
  "clause_ref": "NBC 2016, Part 3 Section 4.6 - Tactile Warning Strips",
  "rule_ids": [],
  "automatable": false,
  "rationale": "Requires visual inspection of tactile surface placement and material"
}
```

Clauses with empty `rule_ids` and `automatable: false` are explicitly documented as needing human review - not silently skipped.

---

## Part 2: ARC-Spatial Blender Extension - Installing and Using

### 2.1 Building the Extension

```bash
python scripts/build_addon.py
```

This produces `arc-0.1.0.zip` containing:
- All `arc/` Python modules (flat layout)
- `blender_manifest.toml` (extension metadata)
- `wheelhouse/` with bundled wheels (reportlab, networkx)

### 2.2 Installing in Blender

1. Open Blender 4.2+
2. Go to **Edit > Preferences > Get Extensions**
3. Click **Install from Disk**
4. Select the `arc-0.1.0.zip` file
5. Enable the extension

The **Spatial ARC** panel appears in the **N-panel** (press N in 3D View, then find the "Spatial ARC" tab).

### 2.3 Using the Blender UI

**Setup Tab:**
- Set rules directory (default: bundled `arc/core/rules`)
- Import IFC model via Bonsai
- Configure check categories and mode

**Run Tab:**
- Click **Run Model Check** (or Ctrl+Shift+R)
- Results appear in the Results panel with color-coded statuses
- 6-status summary: PASS, FAIL, INCONCLUSIVE, HUMAN_REQUIRED, NOT_APPLICABLE, UNSUPPORTED
- Aggregate (class/model-scope) findings appear with sentinel anchors
  (`class:IfcStair`, `model:`) and list the elements they examined under
  `affected_element_ids`. Waived FAILs render under the same FAIL row with
  the waiver record visible.

**Review Tab:**
- Browse failures by category
- Click elements to select them in the 3D viewport
- Toggle heatmap visualization (Ctrl+Shift+H)

**Export Tab:**
- Export JSON/HTML/PDF reports
- Export BCF issues (Ctrl+Shift+E)
- Clear visualization (Ctrl+Shift+C)

### 2.4 Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Ctrl+Shift+R | Run compliance checks |
| Ctrl+Shift+C | Clear visualization |
| Ctrl+Shift+H | Toggle heatmap |
| Ctrl+Shift+E | Export BCF |

---

## Part 3: Developing ARC - For Contributors

### 3.1 Development Setup

```bash
git clone <repo-url> && cd ARC
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
uv pip install ifcopenshell

# Run tests to verify
python -m pytest tests/ -q
```

### 3.2 Project Structure for Developers

```
arc/
  core/                     # Domain-agnostic compliance engine (no Blender/IFC imports)
    rule_engine.py          # THE core file: check type registry, execution, gates
    data_models.py          # All dataclasses: Element, RuleResult, EvidenceBundle, etc.
    rule_loader.py          # JSON/Python rule discovery + pack manifest loading
    context.py              # Wires elements + spatial index + topology for rule execution
    report_generator.py     # JSON/HTML/PDF generation
    rules/                  # Built-in rule packs (JSON + Python rules, clause ledgers)
      json_rules/
      python_rules/
      pack_manifest.json
      clause_ledger.json
  spatial/                  # Blender/IFC-specific layer (geometry, topology, UI)
    geo_engine.py           # Geometry: AABB, KD-tree, Shapely polygon
    topology_engine.py      # NetworkX graph: connectivity, pathfinding
    ui_panel.py             # Blender N-panel UI
    operators.py            # Blender operators
  cli.py                    # CLI entry point
  ...
tests/
  test_rule_engine.py       # Core engine tests
  test_data_models.py       # Data model tests
  test_regression.py        # Golden snapshot regression tests
  test_phase3_4.py          # Architecture hardening + engine capability tests
  test_phase5.py            # Ecosystem feature tests
  ...
```

### 3.3 Adding a New Check Type

**Step 1:** Write the handler method on `RuleEngine`:

```python
# In arc/core/rule_engine.py, add a method to the RuleEngine class:

def _check_max_area(self, params, element, common):
    """Maximum floor area check."""
    max_area = params.get("max_area", float("inf"))
    area = aabb_floor_area(element.aabb)
    passed = area <= max_area
    return RuleResult(
        rule_id=common["rule_id"],
        element_id=element.guid,
        status=STATUS_PASS if passed else STATUS_FAIL,
        passed=passed,
        message=f"Area {area:.2f} m² {'<=' if passed else '>'} {max_area} m²",
        measured_value=round(area, 3),
        expected_value=max_area,
        severity=common["severity"],
        category=common["category"],
        measurement_method="aabb_floor_area",
        measurement_source="aabb",
        geometry_tier="aabb",
    )
```

**Step 2:** Register it at the bottom of `arc/core/rule_engine.py`:

```python
register_check_type("max_area", RuleEngine._check_max_area)
```

**Step 3:** Write a JSON rule that uses it:

```json
{
  "id": "MAX_AREA_01",
  "selector": {"ifc_class": "IfcSpace"},
  "check_type": "max_area",
  "params": {"max_area": 50.0},
  "severity": "major",
  "category": "spatial"
}
```

**Step 4:** Add tests and run:

```bash
python -m pytest tests/ -q
```

### 3.4 Adding a Python Rule

Create a file in `arc/core/rules/python_rules/`, e.g., `my_rule.py`:

```python
"""Custom rule: check that corridors connect to at least one exit."""

RULE_METADATA = {
    "selector": {"ifc_class": "IfcSpace", "properties": {"SpaceType": "Corridor"}},
    "severity": "critical",
    "category": "fire_egress",
}

def check(context, element):
    # Use context.topology for pathfinding
    exits = [e for e in context.elements if e.ifc_class == "IfcDoor"
             and e.properties.get("IsExit")]
    if not exits:
        return {"passed": None, "message": "No exit doors found in model"}

    # Check connectivity through topology graph
    for exit_el in exits:
        if context.topology.has_path(element.guid, exit_el.guid):
            return {"passed": True,
                    "message": f"Corridor connected to exit {exit_el.guid}"}

    return {"passed": False,
            "message": "Corridor not connected to any exit door"}
```

The engine auto-discovers it by filename (rule ID = `my_rule`).

### 3.5 Publishing an Extension Package

Third-party check type packages use Python entry points:

**In your package's `pyproject.toml`:**

```toml
[project.entry-points."arc.check_types"]
fire_spread = "arc_fire_checks:register"
```

**In your package's `__init__.py`:**

```python
def register():
    from arc.core.rule_engine import register_check_type

    def _check_fire_spread(engine, params, element, context, common):
        # ... your implementation
        pass

    register_check_type("fire_spread", _check_fire_spread, needs_context=True)
```

**Users install and it auto-registers:**

```bash
uv pip install arc-fire-checks
arc-check --ifc model.ifc   # fire_spread check type is now available
```

### 3.6 Dual Packaging: pip + Blender

The same `arc/` source tree serves both distributions:

| Channel | How | What Changes |
|---------|-----|-------------|
| **pip** | `uv pip install -e .` | Uses `pyproject.toml`, installs as `spatial-arc` |
| **Blender** | `python scripts/build_addon.py` | Flattens `arc/` into zip, uses `blender_manifest.toml` |

**Why this works:** The `arc/core/` and `arc/spatial/` packages are cleanly separated. `core/` contains the compliance engine, rule loader, data models, and report generator - it has zero Blender or IFC imports and can run standalone via pip. `spatial/` contains all Blender-specific code (UI panels, operators) and IFC geometry/topology engines. All Blender-specific imports (`import bpy`) are guarded with `try/except` and live exclusively in `spatial/`. When installed via pip, those modules exist but are inert - they simply skip Blender registration.

**Rules for contributors:**
1. Never add unconditional `import bpy` to any module
2. Never add Blender or IFC imports to anything under `arc/core/`
3. Test both paths: `python -m pytest tests/` AND `python scripts/build_addon.py`

### 3.7 Running Tests

```bash
# Full suite (79 tests)
python -m pytest tests/ -q

# Specific test file
python -m pytest tests/test_rule_engine.py -q

# Regression tests (compare against golden snapshot)
python -m pytest tests/test_regression.py -q

# With verbose output
python -m pytest tests/ -v
```

**Regression testing:** The first run of `test_regression.py` creates a golden snapshot in `tests/fixtures/`. Subsequent runs compare against it. If you intentionally change engine behavior, delete the snapshot and re-run to create a new one.

---

## Part 4: Rule Pack Authoring

### 4.1 Creating a Rule Pack

A rule pack is a directory containing:

```
my_jurisdiction/
  pack_manifest.json        # Pack metadata
  json_rules/
    fire_safety.json        # JSON rules
    accessibility.json
  python_rules/
    complex_egress.py       # Python rules
  clause_ledger.json        # Regulation-to-rule mapping
```

### 4.2 Pack Manifest

```json
{
  "pack_id": "singapore_bca_v1",
  "version": "1.0.0",
  "jurisdiction": "Singapore",
  "author": "BCA Working Group",
  "description": "Building and Construction Authority Code 2019",
  "required_check_types": ["min_area", "min_width", "min_height", "ratio"],
  "governance_status": "draft",
  "effective_date": "2019-01-01",
  "superseded_date": null,
  "rule_ids": ["BCA_ACC_01", "BCA_FIRE_01"]
}
```

`governance_status` values: `draft` | `review` | `published` | `deprecated`

### 4.3 Clause Ledger

Map every relevant regulation clause - even non-automatable ones:

```json
[
  {
    "clause_ref": "BCA Code 2019, Part 3 Section 4.1 - Accessible Routes",
    "rule_ids": ["BCA_ACC_01"],
    "automatable": true,
    "rationale": null
  },
  {
    "clause_ref": "BCA Code 2019, Part 3 Section 4.8 - Signage Legibility",
    "rule_ids": [],
    "automatable": false,
    "rationale": "Requires subjective assessment of signage adequacy"
  }
]
```

This is what makes ARC's coverage reporting work - explicitly documenting what can and cannot be checked.

### 4.4 Rule Schema Fields

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique rule identifier |
| `selector` | Yes | Element filter: `{"ifc_class": "IfcDoor"}` |
| `check_type` | Yes (JSON) | Registered check type name |
| `params` | Yes (JSON) | Parameters for the check type |
| `severity` | No | `critical` / `major` / `minor` (default: `major`) |
| `category` | No | `accessibility` / `fire_egress` / `spatial` / etc. |
| `source` | No | Regulation reference string |
| `jurisdiction` | No | Country/region label |
| `interpretation_notes` | No | How ambiguous text was interpreted |
| `authority` | No | `statute` / `regulation` / `circular` / `local_rule` / `project_condition` / `guideline` / `best_practice` |
| `effective_date` | No | ISO date: rule becomes active |
| `superseded_date` | No | ISO date: rule becomes inactive |

---

## Appendix: CLI Reference

```
arc-check [options]

Input (one required):
  --ifc PATH [PATH ...]     IFC file(s) to check (multiple for federated)
  --demo                    Use built-in demo elements

Configuration:
  --rules DIR               Rules directory (default: arc/core/rules)
  --output DIR              Output directory (default: results/)
  --mode MODE               pre_compliance | validation
  --categories LIST         Comma-separated category filter
  --regulation-date DATE    ISO date for temporal rule filtering
  --disciplines LIST        Comma-separated discipline labels for federated IFC

Analysis:
  --baseline PATH           Baseline results JSON for delta comparison

Information:
  --list-packs              List available rule packs and exit
```
