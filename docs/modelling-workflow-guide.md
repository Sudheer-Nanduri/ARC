# Modelling Workflow Guide: Preparing IFC Models for ARC

## Overview

This guide walks through the complete workflow from Revit modeling to spatial compliance checking in Blender via Bonsai. The process mirrors real industry BIM coordination workflows.

```
Revit (Model) -> Navisworks (Clash) -> IFC (Export) -> Bonsai (Import) -> Spatial ARC (Check) -> Fix -> Repeat
```

---

## Step 1: Revit Modeling

Create separate models per discipline:

| Discipline | Content | Revit File |
|---|---|---|
| **Architectural** | Walls, doors, windows, rooms, stairs, ramps, balconies, corridors | `Arch_Model.rvt` |
| **Structural** | Columns, beams, slabs, foundations | `Struct_Model.rvt` |
| **MEP - HVAC** | Ducts, air terminals, mechanical equipment | `MEP_HVAC.rvt` |
| **MEP - Plumbing** | Pipes, fixtures, drainage | `MEP_Plumbing.rvt` |
| **MEP - Electrical** | Cable trays, lighting, switchboards | `MEP_Electrical.rvt` |

> **Key:** MEP may produce **several** separate files. This is expected.

### Modeling Requirements for Rule-Ready IFC

- Assign **Room/Space elements** in Revit (Room -> IfcSpace is crucial for spatial rules)
- Use correct **Revit categories** (they map to IFC classes - Door -> IfcDoor, Stair -> IfcStair)
- Name elements descriptively - names are used as fallback classification
- Set **door swing direction** (OperationType) - needed for wheelchair clearance checks
- Ensure walls fully enclose spaces - gaps break spatial boundary detection

> See the **Data Quality Playbook** for a full checklist of what the engine expects.

---

## Step 2: Navisworks Coordination

Before exporting to IFC, resolve all hard clashes:

1. **Append** all discipline models into a single Navisworks project
2. Run **Clash Detective** - focus on MEP vs. Structural and MEP vs. Architectural intersections
3. **Resolve** all critical and major clashes by coordinating with discipline teams
4. **Re-export** updated models once clashes are resolved

> **Why this matters:** The Spatial ARC engine does NOT perform clash detection - it assumes clash-free models. Clashes are handled here, upstream.

---

## Step 3: IFC Export from Revit

Export each discipline model as a separate IFC file:

### Recommended Revit Export Settings

| Setting | Value |
|---|---|
| IFC Version | **IFC 4x3** (preferred) or IFC 2x3 |
| File Type | `.ifc` |
| Space Boundaries | **2nd Level** (critical for IfcSpace) |
| Export Rooms as IfcSpace | **Yes** (mandatory) |
| Export Base Quantities | **Yes** |
| Export Parts as Building Elements | **Yes** |
| Split Walls and Columns by Level | **Yes** |
| Use Family and Type Name | **Yes** |
| Export User Defined Property Sets | **Yes** |

### Expected Output

```
project_folder/
|-- Arch_Model.ifc
|-- Struct_Model.ifc
|-- MEP_HVAC.ifc
|-- MEP_Plumbing.ifc
`-- MEP_Electrical.ifc
```

---

## Step 4: Bonsai Import in Blender

Import all discipline IFC files into a single Blender project:

1. Open **Blender** (5.1+ recommended)
2. Ensure the **Bonsai** (BlenderBIM) add-on is enabled
3. Go to **File -> Open IFC Project** -> select `Arch_Model.ifc`
4. For each additional file: **File -> Append IFC Project** -> select `Struct_Model.ifc`, then each MEP file
5. Verify all models are loaded - check the Outliner for separate collections per discipline
6. Visual check: ensure all models are aligned (same coordinate system)

> **Coordinate System:** Ensure all Revit models use the **same shared coordinates** before export. If models are misaligned in Blender, the spatial checks will produce invalid results.

---

## Step 5: Running Compliance Checks

### 5.1 Open the Spatial ARC Panel

1. Press `N` to open the sidebar in the 3D Viewport
2. Click the **"Spatial ARC"** tab

### 5.2 Validate the Model

1. Check the **Model Status** panel - verify element count, units, and Bonsai sync status
2. Click **"Validate Model"** - this runs pre-checks:
   - IFC integrity (valid GUIDs, required properties)
   - Geometry validity (non-manifold meshes, degenerate faces)
   - Unit assignment verification
3. Fix any critical warnings before proceeding

### 5.3 Configure Rules

1. Select **Preset**: "Mumbai DCR/NBC" (default)
2. Enable/disable **Categories** as needed (e.g., uncheck Parking if not relevant)
3. **Mode**: Use **Pre-Compliance** (recommended for students - it's tolerant and visual)
4. **Scope**: "Entire Model" for comprehensive checks, or "Selected Elements" for focused analysis
5. Review the **complexity estimate** - if the estimate is very high, consider scoping down

### 5.4 Execute

1. Click **"▶ Run Model Check"**
2. Watch the progress bar - the engine processes elements in time-budgeted chunks
3. You can click **"Cancel"** at any time to stop and get partial results

---

## Step 6: Interpreting Results

### Result States

| State | Icon | Meaning |
|---|---|---|
| **PASS** | ✅ | Element meets the rule requirement |
| **FAIL** | ❌ | Element violates the rule requirement |
| **INCONCLUSIVE** | ⚠ | Required data, geometry, confidence, or external input is missing |
| **HUMAN_REQUIRED** | 🔶 | Expert judgment is required |
| **NOT_APPLICABLE** | - | The rule has no subject in the active scope or stage |
| **UNSUPPORTED** | 🛠 | The selected subject exists but the check capability is unavailable |

### Understanding Compliance Volumes

When an element **fails**, the engine generates translucent 3D volumes in the viewport:

- **Blue translucent volume** = the required clearance/dimension space
- **Red translucent volume** = the violated space (where obstruction exists)
- Click a volume -> see the rule that generated it, with measured vs. required values

### Guided Feedback

For each failure, the engine provides a **suggestion**:
- What to fix (e.g., "Move door 260mm outward")
- How much to fix by (exact delta value)
- The source building code clause

---

## Step 7: Headless Batch Mode (Large Models)

For models too large to check interactively, use headless mode:

```bash
# Single model
arc-check --ifc model.ifc --stage submission --output results/

# Federated models
arc-check --ifc architectural.ifc structural.ifc \
  --disciplines architectural,structural --stage submission --output results/

# The same CLI can run through Blender's Python
blender --background --python run_checks.py -- --ifc model.ifc --stage submission --output results/
```

### Output Files

```
results/
|-- compliance_report.pdf      <- Open this for detailed review
|-- compliance_results.json    <- Machine-readable for further analysis
|-- issues.bcfzip             <- Import into Revit/Navisworks
`-- summary.txt               <- Quick terminal-friendly summary
```

---

## Step 8: Authoring Custom Rules

Use the [ARC Rule Authoring Guide](rule-authoring-guide.md) and start from a
bundled rule with the same check type. Place JSON rules under a pack's
`json_rules/` directory and Python rules under `python_rules/`. Record the source
clause and interpretation notes, test binary and missing-input cases, and keep
the pack governance status `draft` until domain review is complete.

AI tools can assist with a first draft, but their output must be checked against
the implemented schema, the source regulation, and representative IFC exports.

---

## Step 9: Exporting Reports

| Format | Purpose | Use Case |
|---|---|---|
| **BCF** (`.bcfzip`) | Issue tracking | Import into Revit/Navisworks to locate violations in original models |
| **PDF** | Formal audit report | Submit for review, print, or share with stakeholders |
| **JSON** | Machine-readable | Further analysis, dashboards, or CI/CD integration |

### PDF Report Sections

The structured PDF report includes:
1. Cover page with project details
2. Executive summary with pass/fail statistics
3. Per-category compliance breakdown
4. Detailed failure list grouped by severity, with guided improvement suggestions
5. Invalid/inconclusive items with remediation guidance
6. Semantic enrichment summary
7. Multi-model source summary (if federated)

---

## Step 10: Iteration Loop

```
Fix in Revit -> Re-export IFC -> Re-import in Bonsai -> Re-run checks -> Compare results
```

1. Fix the violations identified in the compliance report
2. Re-export the updated discipline IFC files from Revit
3. Re-import into Blender via Bonsai (or reload the project)
4. Run checks again
5. Compare results headlessly with `arc-check --ifc updated.ifc --baseline previous/compliance_results.json`
6. Repeat until compliance is achieved

> **Tip:** The baseline comparison records new, resolved, and persistent
> findings in the generated result payload.
