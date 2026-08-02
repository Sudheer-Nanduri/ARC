# Data Quality Playbook: Revit-to-IFC Export for Rule-Ready Models

## Purpose

This playbook helps IFC models exported from Revit contain the information ARC
needs to produce meaningful results. A rule-ready model minimizes
`INCONCLUSIVE` findings and improves measurement reliability.

---

## 1. Revit-to-IFC Export Settings

### Recommended Configuration

| Setting | Value | Why |
|---|---|---|
| **IFC Version** | IFC 4x3 (preferred) or IFC 2x3 | IFC 4x3 has better property support |
| **Space Boundaries** | 2nd Level | Generates IfcSpace from Room elements |
| **Export Rooms** | Yes, as IfcSpace | **Critical** - spatial rules depend on this |
| **Base Quantities** | Yes | Provides area, volume, length natively |
| **Property Sets** | Include user-defined | Custom properties carry through |
| **Export Parts** | As Building Elements | Ensures elements are individually addressable |
| **Split by Level** | Yes | Helps level-based spatial analysis |
| **Coordinate System** | Shared Coordinates | **Critical** for multi-model federation |

### MVD Selection

- **Design Transfer View** - recommended for most cases
- **Coordination View 2.0** - acceptable alternative
- Avoid "Reference View" for compliance checking (limited geometry)

---

## 2. Required Properties per Rule Category

Each rule category depends on specific IFC properties being present. Missing
required properties cause `INCONCLUSIVE` results with a data-gap reason.

### Accessibility Rules

| Property | IFC Location | Required For |
|---|---|---|
| `IfcDoor.OperationType` | Direct attribute | Door swing direction (push/pull clearance) |
| `IfcSpace` elements | Rooms -> IfcSpace export | Room area/width checks |
| `IfcRamp` classification | Element category | Ramp gradient checks |
| Door dimensions | Base quantities or geometry | Clear width checks |

### Fire & Egress Rules

| Property | IFC Location | Required For |
|---|---|---|
| `IfcStair` classification | Element category | Stair width/headroom |
| `IfcSpace` with function | Name or property set | Identifying fire exits |
| `FireExit` property | Custom property set | Egress path distance calculation |
| Floor heights | Level data | Travel distance measurement |

### Spatial Dimension Rules

| Property | IFC Location | Required For |
|---|---|---|
| `IfcSpace` with name | Name attribute | Room type identification (Kitchen, Bathroom) |
| Room area | Base quantities | Minimum area checks |
| Room dimensions | Geometry bounding box | Minimum width checks |
| Corridor classification | IfcSpace name or property | Corridor width checks |

### Parking Rules

| Property | IFC Location | Required For |
|---|---|---|
| Parking space IfcSpace | Room -> IfcSpace | Bay dimension checks |
| Parking type | Name or property | Car vs. two-wheeler differentiation |
| Ramp slope | IfcRamp geometry | Gradient checks |

### Ventilation Rules

| Property | IFC Location | Required For |
|---|---|---|
| `IfcWindow` elements | Element category | Window area ratio |
| Room-to-window association | IfcRelContainedInSpatialStructure | Mapping windows to spaces |
| Ceiling height | Slab-to-slab distance or property | Minimum height checks |

---

## 3. Top 10 Common Export Failures

These are the most frequent reasons rules return `INCONCLUSIVE`:

| # | Failure | Impact | Fix |
|---|---|---|---|
| 1 | **No Room elements in Revit** | All spatial dimension rules fail (no IfcSpace) | Create Room elements for every enclosed space |
| 2 | **Rooms not exported as IfcSpace** | Same as above | Set Space Boundaries to "2nd Level" in export |
| 3 | **Generic names** ("Room 1", "Space") | Engine can't classify room function | Name rooms descriptively (Kitchen, Bedroom, Corridor) |
| 4 | **Door OperationType missing** | Wheelchair clearance direction unknown | Set door family Operation parameter |
| 5 | **Different coordinate origins** | Models misalign in federated check | Use Shared Coordinates for all models |
| 6 | **Stairs as generic elements** | Stair-specific rules skip them | Use Revit Stair components (not generic walls/slabs) |
| 7 | **Walls not fully enclosing spaces** | IfcSpace boundaries are incomplete | Close all room-bounding walls; no gaps |
| 8 | **Mixed units** | Dimension calculations off | Ensure all models use the same units (preferably meters) |
| 9 | **Missing levels** | Egress path analysis fails | Define all floor levels in the Revit model |
| 10 | **Non-manifold geometry** | Headroom/occlusion checks unreliable | Fix geometry warnings in Revit before export |

---

## 4. IfcSpace Requirements

IfcSpace is the **single most important IFC entity** for spatial compliance rules. Without it, room dimension checks, corridor width checks, and spatial layout rules cannot function.

### How to Ensure IfcSpace

1. **Create Room elements** in Revit for every enclosed space (including corridors, lobbies, parking bays)
2. **Name rooms descriptively** - the name is used for functional classification
3. **Set Space Boundaries** to "2nd Level" in the IFC export settings
4. **Verify** - after export, open the IFC in a viewer and confirm IfcSpace elements exist

### What Happens Without IfcSpace

- Engine returns `INCONCLUSIVE` for spatial dimension rules that lack usable geometry
- A warning is displayed: **"Model lacks IfcSpace boundaries. Run Bonsai space generation first."**
- Students can use Bonsai's built-in space generation tool as a fallback, but Revit export is preferred

---

## 5. Multi-Discipline Coordination

When loading multiple IFC files (Arch + Struct + several MEP), coordinate systems must align:

### Checklist

- [ ] All Revit models use **Shared Coordinates** (not Internal Origin)
- [ ] All models reference the **same survey point**
- [ ] All models use the **same unit system** (meters preferred)
- [ ] All models use the **same IFC version** (IFC 4x3 preferred)
- [ ] Models don't overlap in element classification (no duplicate walls between Arch and Struct)

### Verification

After loading all models in Blender:
1. Check visual alignment in the 3D viewport - all disciplines should overlap correctly
2. Run **"Validate Model"** - the engine will warn about unit mismatches or duplicate GUIDs
3. Check element count - should roughly equal the sum of individual file element counts

---

## 6. Pre-Flight Checklist

Before running the Spatial ARC engine, verify:

- [ ] All discipline IFC files are loaded in Bonsai
- [ ] Models are visually aligned (same coordinate system)
- [ ] "Validate Model" returns no critical errors
- [ ] IfcSpace elements exist (check element count in Model Status panel)
- [ ] Units are correctly detected (check Model Status panel)
- [ ] Bonsai sync status is ✅ (no uncommitted changes)
- [ ] Selected rule categories match your analysis goals

---

## 7. Wiring Waivers and Routing Overrides

Reference: `docs/specifications/data-model-schema.md` Section 11 (routing and deviation contract).

### 7.1 Waivers - accepted deviations

A waiver attaches to a FAIL when an authorised actor accepts the deviation
(e.g. a heritage clause overriding a width minimum). Status remains FAIL -
the waiver is additional evidence, never a status flip. Every waiver in
scope (applied, stale, moot) is rendered in every output format; there is
no silent acceptance.

Waivers ride in via `Context.waivers` at runtime:

```python
from arc.core.context import Context
from arc.core.data_models import WaiverRecord

waivers = {
    "w_2026_042": WaiverRecord(
        waiver_id="w_2026_042",
        rule_id="NBC_ACC_04",
        rule_pack_id="nbc_dcr_india_v1",
        rule_version="1.0.0",
        element_id="0aBcD...GUID",
        project_id="proj_heritage_42",
        granted_by="conservation_officer_v.shah",
        authority_basis="Heritage Listing Order Section 3.2",
        rationale="Heritage door retained per conservation order.",
        conditions=["Add fire-detector add-on"],
        occasion="until_next_submission",
        expires_at="2026-12-31T23:59:59+00:00",
    ),
}

ctx = Context(elements=..., project_id="proj_heritage_42",
              model_source="heritage_house.ifc", waivers=waivers)
```

**Identity binding (non-negotiable).** A waiver only auto-applies when
`(rule_pack_id, rule_id, rule_version, element_id, project_id)` match
exactly. Any mismatch leaves the FAIL open and renders the waiver as
informational (stale-identity).

**Staleness.** The engine never pre-emptively HUMAN_REQUIREDs on staleness.
The rule re-runs; the waiver is annotated as `applied / stale / moot`:

| State    | Trigger                                          | Effect on the new run             |
|----------|--------------------------------------------------|-----------------------------------|
| applied  | Identity matches; not expired; occasion in force | FAIL routed to deviation reviewer |
| stale    | Expired / version changed / occasion exhausted   | FAIL stays open; route to BIM/compliance |
| moot     | New result is no longer FAIL                     | Informational only                |

### 7.2 Routing overrides

To change where a category routes (e.g. send `data_missing` to your own
compliance officer instead of the BIM modeller), call `register_route`
once at deployment startup:

```python
from arc.core.data_models import register_route, ROUTE_KIND_GATE

register_route(
    "data_missing",
    "Ticket the BIM team via Jira project ARC-MODEL",
    "bim_team_jira",
    "P4",
    ROUTE_KIND_GATE,
)
```

For one-off per-rule overrides, add a `routing` block to the rule:

```json
{"routing": {"primary": "P7", "fallback": "P10"}}
```

### 7.3 Project identity

`Context.project_id` is what waivers bind to. The default is
`model_source` (file name / Speckle stream / Revit project name); set it
explicitly if your authoring source does not carry a project header. The
engine logs a warning when waivers are present and `project_id == "unknown"`.
