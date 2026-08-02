# Rule Type Taxonomy: What ARC Can Check

This document maps the complete taxonomy of Automated Code Compliance Checking (ACCC) rule types against ARC's engine capabilities. It is the definitive reference for what the engine handles today, how each check category is implemented, and what remains outside its scope.

---

## 1. Academic Classification (Solihin & Eastman, 2015)

The most widely cited ACCC framework classifies rules by the data complexity they require from the BIM model:

| Type | Description | ARC Coverage | Implementation |
|:-----|:------------|:-------------|:---------------|
| **Type 1** - Explicit BIM Data | Rules checked directly against existing IFC properties (room names, material ratings, element classification) | **Full** | JSON `property_min`, `property_max`, selector property filters |
| **Type 2** - Derived Attributes | Rules requiring computation from explicit data (area from dimensions, slope from geometry, ratios) | **Full** | JSON `min_area`, `min_width`, `min_height`, `max_height`, `min_dimensions_2d`, `ratio`; AABB-derived geometry via the geometry-tier cascade (property -> polygon -> AABB) |
| **Type 3** - Extended Structures | Rules requiring spatial relationships, topological networks, or cross-element analysis not natively in IFC | **Full** | JSON `clearance_zone`, `turning_circle`, `distance_to_nearest`, `count_nearby`; Python rules for topology (`context.topology`), pathfinding, multi-element logic |
| **Type 4** - Proof of Solution | Rules requiring human qualitative judgment, external simulation, or aesthetic evaluation | **Excluded** | Outside scope by design. ARC automates objective, measurable checks only. |

---

## 2. Practical Check Categories

### 2.1 Dimension Checks (Scalar)

**What:** Verify a single measurement against a threshold.

| Check | JSON check_type | Params | Example Rule |
|:------|:----------------|:-------|:-------------|
| Minimum floor area | `min_area` | `min_area` (m^2) | Habitable room >= 9.5 m^2 |
| Minimum width | `min_width` | `min_width` (m) | Door opening >= 0.9 m |
| Minimum height | `min_height` | `min_height` (m) | Ceiling height >= 2.75 m |
| Maximum height | `max_height` | `max_height` (m) | Railing <= 1.2 m |

**Measurement strategy:** Prefer explicit IFC properties (`Width`, `Height`, `area`, `GrossFloorArea`). Fall back to AABB dimensions when properties are missing. For doors/windows, `min_width` uses the major horizontal dimension (opening width); for spaces, it uses the minor dimension (clear width).

### 2.2 Dimension Checks (Compound)

**What:** Verify two dimensions simultaneously (length x width).

| Check | JSON check_type | Params | Example Rule |
|:------|:----------------|:-------|:-------------|
| Minimum 2D footprint | `min_dimensions_2d` | `min_dim1`, `min_dim2` (m) | Parking bay >= 2.5 x 5.5 m |

**Measurement strategy:** AABB horizontal dimensions (dx, dy) sorted and compared against sorted requirements.

### 2.3 Property Threshold Checks

**What:** Compare an explicit IFC property value against a threshold.

| Check | JSON check_type | Params | Example Rule |
|:------|:----------------|:-------|:-------------|
| Property >= value | `property_min` | `property`, `min_value` | Fire resistance rating >= 60 min |
| Property <= value | `property_max` | `property`, `max_value` | Ramp slope <= 0.0833 (1:12) |

**Measurement strategy:** Direct property lookup only. Returns INCONCLUSIVE if the property is missing. Does not require geometry.

### 2.4 Ratio Checks

**What:** Verify the ratio between two measured or derived values.

| Check | JSON check_type | Params | Example Rule |
|:------|:----------------|:-------|:-------------|
| Ratio within range | `ratio` | `numerator`, `denominator`, `min_ratio`, `max_ratio` | Window area / floor area >= 0.1 |

**Measurement strategy:** Both numerator and denominator are resolved through the same property+geometry fallback chain as scalar checks. Returns INCONCLUSIVE if either value is missing. Does not require geometry if both values are explicit properties.

**JSON example:**
```json
{
  "id": "DCR_VENT_01",
  "check_type": "ratio",
  "selector": { "ifc_class": "IfcSpace" },
  "params": {
    "numerator": "WindowArea",
    "denominator": "area",
    "min_ratio": 0.1
  }
}
```

### 2.5 Spatial Clearance Checks

**What:** Verify that sufficient clear space exists around an element.

| Check | JSON check_type | Params | Example Rule |
|:------|:----------------|:-------|:-------------|
| Obstruction-free buffer | `clearance_zone` | `padding_m`, `ignore_classes` | 1.5 m wheelchair approach |
| Inscribed turning space | `turning_circle` | `diameter_m` | 1.5 m wheelchair turning circle |

**Measurement strategy:** `clearance_zone` expands the element AABB horizontally by `padding_m`, then queries the spatial index for obstructions (ignoring envelope elements by default). `turning_circle` checks that the minimum horizontal AABB dimension fits the required diameter.

### 2.6 Proximity Checks

**What:** Verify distance or count relationships between elements of different classes.

| Check | JSON check_type | Params | Example Rule |
|:------|:----------------|:-------|:-------------|
| Distance to nearest | `distance_to_nearest` | `target_class`, `max_distance`, `min_distance` | Exit sign within 30 m of any space |
| Element count nearby | `count_nearby` | `target_class`, `radius`, `min_count`, `max_count` | At least 1 railing within 5 m of balcony slab |

**Measurement strategy:** `distance_to_nearest` uses KD-tree radius query followed by center-to-center Euclidean distance. `count_nearby` counts elements of the target class within the search radius.

**JSON example (railing on balcony):**
```json
{
  "id": "NBC_SAFE_01_v2",
  "check_type": "count_nearby",
  "selector": { "ifc_class": "IfcSlab", "properties": { "PredefinedType": "BALCONY" } },
  "params": {
    "target_class": "IfcRailing",
    "radius": 3.0,
    "min_count": 1
  }
}
```

### 2.7 Topology and Pathfinding Checks

**What:** Verify connectivity, travel distances, and route existence through the building graph.

| Check | Mechanism | Example Rule |
|:------|:----------|:-------------|
| Egress travel distance | Python rule + `context.topology.shortest_path_length()` | Max 30 m to nearest fire exit |
| Connected accessible route | Python rule + `context.topology.shortest_path()` | Continuous wheelchair path from entrance to all floors |
| Connectivity verification | Python rule + `context.topology.connected_components()` | All habitable spaces connected to at least one exit |

**Why Python-only:** Topology checks inherently require multi-step logic (find exits, iterate paths, evaluate distances conditionally). JSON cannot express this. The topology engine provides the primitives; Python rules compose them.

**Graph construction:** Hybrid semantic + AABB. Prefers `IfcRelSpaceBoundary` / `IfcRelConnectsElements` edges (high confidence 0.9). Falls back to AABB-proximity between spatial elements (IfcSpace, IfcDoor, IfcStair) when IFC relationships are missing (low confidence 0.3). Walls and slabs never create AABB edges.

### 2.8 Conditional / Context-Dependent Checks

**What:** Rules where the threshold depends on building context (building type, height, occupancy).

| Check | Mechanism | Example Rule |
|:------|:----------|:-------------|
| Type-dependent dimension | Python rule with property branching | Stair width >= 1.5 m (residential) or >= 2.0 m (commercial) |
| Height-dependent requirement | Python rule with aggregate query | Buildings > 24 m require min 2 staircases |
| Occupancy-conditional | Python rule | Assembly occupancy > 300 requires 2 exits |

**Why Python-only:** JSON rules are stateless single-element checks. Conditional logic, cross-element aggregation, and context-dependent thresholds require procedural code. This is the correct boundary - forcing these into JSON would produce an unmaintainable pseudo-language.

### 2.9 Cross-Element / Aggregate Checks

**What:** Rules that evaluate collections of elements (counts per floor, sum of areas, comparative analysis).

| Check | Mechanism | Example Rule |
|:------|:----------|:-------------|
| Count elements per storey | Python rule + `elements_by_class()` + grouping | Min 2 staircases per floor above 24 m |
| Aggregate area | Python rule + `sum(area)` | Total parking area >= X% of built-up area |
| Comparative | Python rule | All exits must be >= 50% of widest exit |

**Available primitives:** `context.elements_by_class()`, `context.filter_by_property()`, `context.get_nearby_elements()`. Python rules have access to the full element list and can group, filter, and aggregate freely.

---

## 3. What ARC Cannot Check (and Why)

### 3.1 True Mesh Geometry (Non-AABB)

| Limitation | Impact | Mitigation |
|:-----------|:-------|:-----------|
| Slope/gradient from actual mesh surfaces | Ramp slope requires mesh normals, not AABB | In Blender: Python rules can access `bpy.data.meshes` for real geometry. Headless: require slope as IFC property. |
| Curved element measurement | Circular corridors, spiral stairs | AABB approximation. Precise measurement requires bmesh slicing (Blender-only, post-MVP). |
| Orientation / facing direction | North-facing window checks | Requires mesh face normals or IFC property `Orientation`. Python rule can access Blender mesh data. |

**Architectural decision:** ARC uses AABB as the universal geometry primitive because it works identically in Blender and headless modes. Mesh-level operations are available only inside Blender and are accessed through Python rules, not the JSON engine.

### 3.2 Qualitative / Simulation-Based (Type 4)

| Excluded | Reason |
|:---------|:-------|
| Aesthetic conformance | Subjective, cannot be automated |
| Energy performance simulation | Requires external simulation engine (EnergyPlus, etc.) |
| Daylight factor calculation | Requires raytracing simulation |
| Structural adequacy | Requires FEA, not geometric checking |

**Architectural decision:** ARC is a geometric and topological compliance engine. Simulation-based checks are delegated to specialized tools. ARC can consume their outputs as IFC properties and check thresholds (e.g., `property_min` on a "DaylightFactor" property set by an external tool).

### 3.3 IFC Data Quality

| Excluded | Handled By |
|:---------|:-----------|
| Property schema conformance (IDS) | Bonsai IfcTester (native panel) |
| Missing required properties | ARC returns INCONCLUSIVE; an upstream IDS check can catch this earlier |
| Incorrect IFC classification | ARC's semantic layer flags low confidence; IDS validates authoritatively |

**Architectural decision:** Data quality is Pillar 1 (IDS). ARC is Pillar 3 (Spatial Compliance). ARC does not duplicate IDS - it depends on it as a prerequisite.

---

## 4. Extensibility: Adding New Check Types

The rule engine uses a **plug-in registry**. New JSON check types do not require modifying the engine's dispatch logic:

```python
from arc.rule_engine import register_check_type

def _check_my_custom(engine, params, element, context, common):
    # ... measurement logic ...
    return RuleResult(**common, status=..., passed=..., message=...)

register_check_type(
    "my_custom_check",
    _check_my_custom,
    needs_context=True,    # requires spatial index / topology
    needs_geometry=True,   # requires valid AABB
    scope=None,            # "class" or "model" for aggregate handlers
)
```

After registration, JSON rules can use `"check_type": "my_custom_check"` immediately.

For aggregate (class/model-scope) check types, register with
`scope="class"` or `scope="model"`. The handler then receives the full
element list instead of a single element:

```python
def _check_my_aggregate(engine, params, elements, common):
    # elements: list[Element]
    # common already contains scope + affected_element_ids
    return RuleResult(**common, status=..., passed=..., ...)

register_check_type("my_aggregate", _check_my_aggregate,
                    needs_geometry=False, scope="class")
```

Built-in aggregate types ship: `count`, `sum_property`, `any_pass`,
`all_pass`.

### Extension paths by audience

| Audience | Mechanism | Complexity |
|:---------|:----------|:-----------|
| Architects / code consultants | JSON rules with existing check types | No code |
| Students / basic Python | Python `run(context, element)` rules | Low code |
| Developers extending the engine | `register_check_type()` for new JSON types | Moderate |
| Gov-Node rule authors | JSON rules using the universal schema | No code |

---

## 5. Rule Type Summary Matrix

| # | Category | JSON | Python | Context | Geometry | Example |
|:--|:---------|:----:|:------:|:-------:|:--------:|:--------|
| 1 | Scalar dimension | `min_area` / `min_width` / `min_height` / `max_height` | -- | No | Yes | Room area >= 9.5 m^2 |
| 2 | Compound dimension | `min_dimensions_2d` | -- | No | Yes | Parking 2.5 x 5.5 m |
| 3 | Property threshold | `property_min` / `property_max` | -- | No | No | Slope <= 1:12 |
| 4 | Ratio | `ratio` | -- | No | Optional | Window/floor >= 10% |
| 5 | Spatial clearance | `clearance_zone` / `turning_circle` | -- | Yes | Yes | 1.5 m approach zone |
| 6 | Proximity / existence | `distance_to_nearest` / `count_nearby` | -- | Yes | Yes | Railing near balcony |
| 7 | Topology / pathfinding | -- | `context.topology.*` | Yes | Yes | Egress <= 30 m |
| 8 | Conditional logic | -- | `if/else` in `run()` | Yes | Varies | Building-type-dependent |
| 9 | Cross-element aggregate | -- | `elements_by_class()` + logic | Yes | Varies | 2 stairs per floor |
| 10 | Mesh geometry | -- | `bpy.data.meshes` (Blender only) | No | Mesh | True ramp slope |
