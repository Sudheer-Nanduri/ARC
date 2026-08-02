# ARC Platform Architecture

This document describes the public architecture of ARC 0.1.0. It is intended for
contributors and integrators; the normative user-facing behavior is defined by
the tests and the specifications in [`docs/specifications/`](specifications/).

## Design boundary

ARC produces compliance evidence, not permit decisions. Its output records what
was checked, what could not be checked, the measurement basis, and the actor who
should handle unresolved work. Approval, rejection, and acceptance of deviations
remain institutional decisions outside the engine.

The implementation is split into two layers:

- **ARC-Core** (`arc/core/`) is a Python rule engine with no Blender or IFC
  imports. It owns the data model, execution pipeline, checkability gates,
  geometry and topology services, routing, waiver matching, and reports.
- **ARC-Spatial** (`arc/spatial/`) is the Blender/Bonsai integration and review
  interface. It translates scene or IFC information into the core data model and
  visualizes the resulting evidence.

The headless IFC adapter is in `arc/core/ifc_integration.py`. IfcOpenShell is an
optional adapter dependency rather than a dependency of the core rule engine.

## Processing pipeline

```text
models + rule packs + context + waivers
                  |
                  v
          canonical Context
                  |
                  v
       selectors and stage filter
                  |
                  v
        checkability gates
                  |
                  v
          rule execution
                  |
                  v
   evidence + routing + waiver annotation
                  |
                  v
       JSON / HTML / PDF / BCF
```

For every selected rule-element pair, the engine applies deterministic gates
before executing the check body. The first gate that cannot be satisfied yields
an explicit non-binary result. The six statuses are:

| Status | Meaning |
|---|---|
| `PASS` | The measured requirement is satisfied. |
| `FAIL` | The measured requirement is violated. |
| `INCONCLUSIVE` | Required data, geometry, confidence, or external input is missing. |
| `HUMAN_REQUIRED` | The requirement needs expert judgment or reliable classification is unavailable. |
| `NOT_APPLICABLE` | The rule has no subject at the current model, building, or stage. |
| `UNSUPPORTED` | A subject exists, but the required check type is not registered. |

Element-scope non-binary results carry a route to a responsible actor. All
element results carry an `EvidenceBundle`, including results produced by a gate.

## Core components

| Component | Responsibility |
|---|---|
| `data_models.py` | Elements, results, evidence, coverage, waivers, summaries, and capability records |
| `context.py` | Execution context and access to spatial and topology indexes |
| `rule_loader.py` | JSON and Python rule loading, manifests, clause ledgers, and static screening |
| `rule_engine.py` | Selection, gates, check handlers, execution, routing, and waiver annotation |
| `geo_engine.py` | AABB, polygon, proximity, and spatial-index operations |
| `topology_engine.py` | Connectivity graphs and route calculations |
| `report_generator.py` | JSON payloads plus HTML, PDF, and terminal reports |
| `bcf_exporter.py` | Scope-aware BCF-style issue archives |
| `delta.py` | Comparison with a prior result payload |
| `registry.py` / `extensions.py` | Rule-pack registry and entry-point discovery |
| `validators.py` | External JSON, GeoJSON, and rule-data validation |

`arc/core/cli.py` composes these components for the `arc-check` command.
`run_checks.py` is a thin source-tree wrapper around the same entry point.

## Evidence model

A `RuleResult` identifies the rule and affected element or aggregate scope,
records the six-status outcome, and links to a structured `EvidenceBundle`.
Evidence fields describe the measurement method and source, geometry tier,
confidence label, assumptions, and applicable rule version. Coverage gaps retain
the reason a check did not reach PASS or FAIL and its suggested next action.

Waivers are separate authority records. Exact identity matching uses project,
rule pack, rule, rule version, and element, together with validity conditions.
A matched waiver annotates a finding but never changes its technical status or
the pair-axis counts.

## Rule packs and extension points

The bundled pack contains declarative JSON rules and restricted Python rule
modules. Its manifest records pack identity, version, jurisdiction, required
check types, and governance status. The accompanying clause ledger distinguishes
implemented clauses from deliberate exclusions.

ARC can be extended at three boundaries:

1. Add rule packs without modifying the engine.
2. Register new check types through the public extension protocol.
3. Build adapters that translate other model formats into `Element` and
   `Context` objects.

Python rule packs are governed executable artifacts. Static screening and a
restricted namespace reduce accidental misuse, but they are not a security
boundary for arbitrary untrusted code.

## Geometry and confidence

The engine uses the strongest available representation and records the choice:
explicit properties, polygon geometry where available, and AABB proxies where a
rule permits them. Missing or unreliable capability is surfaced as
`INCONCLUSIVE` or `HUMAN_REQUIRED`; it is not silently converted into a measured
PASS or FAIL. Topological and cross-element checks use indexes built lazily from
the current context.

## Packaging and dependencies

The Python package requires Python 3.10 or newer and NetworkX. Optional extras
provide IFC loading (`ifcopenshell`), polygon geometry (`shapely`), PDF output
(`reportlab` and `pillow`), and tests (`pytest`). The Python wheel excludes the
Blender-only bundled wheelhouse.

The Blender build script creates a 4.2+ extension archive and a legacy archive.
Those archives include pure-Python NetworkX and ReportLab wheels. Pillow is not
bundled because its wheels are platform- and Python-version-specific, so PDF
output inside Blender requires a matching Pillow installation.

## Validation and known limits

The public suite contains 157 passing tests and one expected skip outside
Blender. It covers the core pipeline, status and routing invariants, waivers,
report payloads, configuration, geometry, topology, CLI behavior, and headless
visualization helpers. The bundled IFC models provide end-to-end reference runs
for the paper's reported counts.

The 0.1.0 rule pack is a purposive research set, not authoritative regulatory
coverage. The current evaluation validates engine behavior rather than legal or
professional correctness. Mesh-level geometry, additional jurisdictions,
expert-labelled ground truth, operational permitting studies, and real public
waiver datasets remain outside the validated scope.

## Further documentation

- [Installation and verification](../INSTALL.md)
- [User and developer guide](user-and-developer-guide.md)
- [Integrator guide](integrator-guide.md)
- [Rule authoring guide](rule-authoring-guide.md)
- [Data model schema](specifications/data-model-schema.md)
- [API contract](specifications/api-contract.md)
