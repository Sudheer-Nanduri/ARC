# ARC Rule Authoring Guide

ARC supports declarative JSON rules and executable Python rules. Start with JSON
when a built-in check type expresses the requirement; use Python only when the
logic cannot be represented by a selector, check type, and parameters.

Rules are interpretations of regulatory text. Record the exact source,
jurisdiction, assumptions, and interpretation notes, and obtain domain review
before treating a pack as anything beyond a draft research artifact.

## Pack layout

```text
my_pack/
  pack_manifest.json
  clause_ledger.json
  json_rules/
    rules.json
  python_rules/
    custom_rule.py
```

`load_rules("my_pack")` discovers both rule directories and attaches the pack
identifier and version from the manifest to every rule. The bundled pack under
`arc/core/rules/` is a working example.

## JSON rules

A minimal declarative rule looks like this:

```json
{
  "id": "NBC_ACC_04",
  "title": "Door Clear Width",
  "description": "Minimum clear doorway width.",
  "selector": {"ifc_class": "IfcDoor"},
  "check_type": "min_width",
  "params": {"min_width": 0.9},
  "severity": "critical",
  "category": "accessibility",
  "source": "NBC 2016, Part 3 section 4.3.1",
  "jurisdiction": "India",
  "interpretation_notes": "AABB width is a proxy for unobstructed clear width."
}
```

Required operational fields are `id`, `selector`, `check_type`, and the
parameters required by that check type. Public packs should also supply `title`,
`description`, `severity`, `category`, `source`, `jurisdiction`, and
`interpretation_notes` so evidence remains reviewable.

Selectors use `ifc_class` and may include property conditions:

```json
{
  "selector": {
    "ifc_class": "IfcSpace",
    "properties": {"LongName": "LOBBY"}
  }
}
```

Optional execution fields include `min_stage`, `requires_geometry`,
`required_properties`, `required_external`, `confidence_requirement`, and
`human_judgment`. Missing declared requirements are handled by the engine's
checkability gates rather than hidden inside the rule body.

## Built-in check types

| Check type | Principal parameters |
|---|---|
| `min_area` | `min_area` |
| `min_width` | `min_width` |
| `min_height` | `min_height` |
| `max_height` | `max_height` |
| `min_dimensions_2d` | minimum horizontal dimensions |
| `property_min` | `property`, `min_value` |
| `property_max` | `property`, `max_value` |
| `ratio` | numerator/denominator fields and bounds |
| `clearance_zone` | clearance padding and blocking classes |
| `turning_circle` | required diameter/radius |
| `distance_to_nearest` | `target_class` and distance bounds |
| `count_nearby` | `target_class`, `radius`, and count bounds |

Aggregate types `count`, `sum_property`, `any_pass`, and `all_pass` emit one
class- or model-scope result with `affected_element_ids`. Consult the handlers in
`arc/core/rule_engine.py` and the bundled JSON rules for the exact parameter
names supported by 0.1.0.

## Python rules

A Python rule file defines optional literal `RULE_METADATA` and a
`run(context, element)` function:

```python
RULE_METADATA = {
    "title": "Accessible turning space",
    "selector": {"ifc_class": "IfcSpace"},
    "severity": "major",
    "category": "accessibility",
    "source": "NBC 2016, Part 3 section 4.3.2",
    "jurisdiction": "India",
    "interpretation_notes": "Uses the smaller horizontal AABB dimension.",
}


def run(context, element):
    width = min(
        element.aabb.max[0] - element.aabb.min[0],
        element.aabb.max[1] - element.aabb.min[1],
    )
    required = 1.5
    passed = width >= required
    return {
        "passed": passed,
        "message": f"Turning width {width:.3f} m; required {required:.3f} m",
        "details": {
            "measured_value": width,
            "required_value": required,
            "measurement_method": "aabb_min_horizontal_dimension",
            "geometry_tier": "aabb",
            "confidence_label": "approximate",
        },
    }
```

Return `passed: True` for PASS, `False` for FAIL, or `None` when the rule body
discovers a condition it cannot decide. Prefer declaring known data and geometry
requirements in metadata so the engine can classify and route gaps consistently.
The engine adds the canonical evidence bundle and routing after execution.

Python rule code is parsed before execution. Imports, global/nonlocal statements,
and dangerous built-ins are rejected, and only a restricted set of built-ins is
available. This is a safety aid, not a secure sandbox: install Python rule packs
only from sources you trust.

## Pack governance

`pack_manifest.json` identifies the pack and its lifecycle:

```json
{
  "pack_id": "example_pack_v1",
  "version": "1.0.0",
  "jurisdiction": "Example",
  "author": "Example organization",
  "description": "Example compliance rules",
  "required_check_types": ["min_width"],
  "governance_status": "draft",
  "effective_date": "2026-01-01",
  "superseded_date": null,
  "rule_ids": ["EXAMPLE_01"]
}
```

Use `draft` until the interpretations and tests have been reviewed. Other
supported lifecycle values are `review`, `published`, and `deprecated`.
`clause_ledger.json` should list implemented clauses and deliberate exclusions,
including why a clause is not automated.

## Validation checklist

Before distributing a rule pack:

1. Confirm selectors match real exported IFC classes and property names.
2. Test PASS, FAIL, missing-data, and non-applicable cases.
3. Confirm measurement units are metres and ratios use the documented scale.
4. Inspect evidence fields and routing, not only the status.
5. Record approximations and interpretation choices explicitly.
6. Run `python -m pytest tests -q` when changing the bundled pack.
7. Reproduce the reference model counts and explain any intentional change.

AI tools may help draft rule data, but generated rules require the same source
checking, tests, and human governance as hand-written rules. Do not paste
copyrighted regulation text into the repository; cite the source clause.
