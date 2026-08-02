# ARC Public API Contract

This document describes the supported integration surface for ARC 0.1.0. Public
behavior is exercised by the test suite; modules and names beginning with an
underscore are implementation details.

## Command-line interface

Install the package to expose `arc-check`, or run `python -m arc.core.cli`.

```bash
arc-check --demo
arc-check --ifc model.ifc --stage submission --output results/
arc-check --ifc model.ifc --baseline previous/compliance_results.json
arc-check --list-packs
```

Use `arc-check --help` for the complete option list. A successful run writes
JSON, HTML, BCF, and a terminal summary. PDF is written when ReportLab and Pillow
are available. Input or configuration failures return a non-zero exit code.

## Programmatic execution

The stable composition pattern is:

```python
from arc.core.context import Context
from arc.core.data_models import Element
from arc.core.rule_engine import RuleEngine
from arc.core.rule_loader import load_rules

elements: list[Element] = []
rules = load_rules("arc/core/rules")
results = RuleEngine(rules, execution_stage="submission").execute(
    Context(elements, project_id="example-project")
)
```

Adapters are responsible for translating source models into `Element` objects.
The bundled headless IFC adapter exposes `load_ifc()` and
`load_federated_ifc()` from `arc.core.ifc_integration`; both require the optional
IfcOpenShell dependency.

## Rules

`load_rules(path)` loads JSON rule files and screened Python rule modules.
`RuleEngine` accepts the resulting rule dictionaries. A rule identifies its
selector, check type, parameters, source clause, version, and governance fields.
See the [rule authoring guide](../rule-authoring-guide.md) for the supported JSON
and Python formats.

Third-party packages may register check types through the extension interface in
`arc.core.extensions`. Rule modules are executable governed artifacts and must
be treated as trusted dependencies even though ARC applies static screening and
a restricted namespace.

## Result contract

`RuleEngine.execute(context)` returns `list[RuleResult]`. Each element-scope
result contains:

- rule and element identity;
- one of the six canonical statuses;
- measured and required values when available;
- severity, message, and structured details;
- an `EvidenceBundle` describing provenance and confidence;
- routing for unresolved non-binary outcomes;
- waiver state when an authority record was evaluated.

Class- and model-scope aggregate results use `affected_element_ids` and are kept
separate from the pair axis in summaries. See the [data model
schema](data-model-schema.md) for invariants and serialization details.

## Reporting

Functions in `arc.core.report_generator` produce the canonical JSON payload and
human-readable reports. `arc.core.bcf_exporter` writes scope-aware BCF-style
archives. Consumers should rely on named JSON fields, not field order or HTML
markup. Unknown additive fields should be ignored for forward compatibility.

## Compatibility

ARC-Core supports Python 3.10 and newer. It does not import Blender or
IfcOpenShell. Blender-facing registration and operators live under
`arc.spatial`; they are inert when `bpy` is unavailable.
