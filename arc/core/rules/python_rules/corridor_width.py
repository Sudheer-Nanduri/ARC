# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""Clear corridor / lobby width check.

Apartment lobbies are typically L-shaped, U-shaped, or ring-shaped around a
lift core. The IfcSpace AABB is the rectangular envelope of that irregular
footprint, so ``min(dx, dy)`` of the AABB is **not** the clear corridor
width — it is just the bounding-box edge, which silently includes the
lift-core footprint as if it were walkable. Detecting the lift-core columns
and walls as "intrusions" and FAIL-ing the space is also wrong: those are
not obstructions, they are the boundary of the walkable corridor that wraps
around them.

This rule therefore:

1. Builds a 2D walkable polygon = lobby AABB footprint minus the footprints
   of structural elements that intersect it (columns, walls, curtain walls,
   railings, ramps). Done by the engine-provided helper
   ``walkable_clear_width`` (which uses shapely when available).

2. Measures clear corridor width as twice the largest inscribed-circle
   radius on the walkable polygon.

3. Routes results into four buckets:
     - PASS  : walkable polygon is connected, regular aspect, clear width ≥ min
     - FAIL  : walkable polygon is connected, regular aspect, clear width < min
     - HUMAN_REQUIRED (irregular_geometry) : walkable polygon is split into
       multiple disjoint pieces, has a high aspect ratio, OR the lobby is
       only available as an AABB and obstructions are present — in any of
       these cases the engine cannot reliably quote a single number; a
       reviewer with the model open should make the call.
     - INCONCLUSIVE : missing AABB (handled by the engine's geometry gate
       before the rule body runs).

The HUMAN_REQUIRED route is registered as ``irregular_geometry`` in the
routing registry and resolves to a reviewer (P10).

Per the v5 paper design decision (Hybrid fallback): FAIL is only emitted
when the measurement comes from a real polygon-derived clearance. AABB-only
evaluations may PASS (no obstructions, width ≥ threshold) but never FAIL —
ambiguous AABB-only cases route to a reviewer instead.
"""

RULE_METADATA = {
    "selector": {"ifc_class": "IfcSpace", "properties": {"LongName": "LOBBY"}},
    "severity": "major",
    "category": "spatial",
    "source": "NBC 2016, Part 3 §4.3.2",
    "params": {
        "min_width": 1.2,
        "max_aspect_ratio": 6.0,
        "min_walkable_area_ratio": 0.30,
    },
    "interpretation_notes": (
        "Clear corridor width is measured on the walkable polygon "
        "(lobby footprint minus structural obstructions) as 2× the largest "
        "inscribed-circle radius. Irregular lobbies (multi-branch, lift "
        "core, alcoves) route to a reviewer rather than auto-fail."
    ),
}

_DEFAULT_MIN_WIDTH = 1.2
_DEFAULT_MAX_ASPECT = 6.0
_DEFAULT_MIN_WALKABLE_RATIO = 0.30

_OBSTRUCTION_CLASSES = (
    "IfcColumn", "IfcWall", "IfcWallStandardCase", "IfcCurtainWall",
    "IfcRailing", "IfcRamp", "IfcRampFlight", "IfcBeam",
)


def _f(value, default):
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def run(context, element):
    min_width = _f(element.properties.get("min_width"), _DEFAULT_MIN_WIDTH)
    max_aspect = _f(element.properties.get("max_aspect_ratio"), _DEFAULT_MAX_ASPECT)
    min_walkable_ratio = _f(
        element.properties.get("min_walkable_area_ratio"),
        _DEFAULT_MIN_WALKABLE_RATIO,
    )

    if element.aabb is None:
        return {
            "passed": None,
            "status": "INCONCLUSIVE",
            "message": "Lobby has no AABB — cannot evaluate corridor width",
            "details": {"gate": "geometry_limited", "missing": "aabb"},
        }

    lobby_aabb = element.aabb
    headroom_z = lobby_aabb.min[2] + 2.1

    obstructions = []
    for other in context.neighbours(element):
        if other.guid == element.guid or other.aabb is None:
            continue
        if other.ifc_class not in _OBSTRUCTION_CLASSES:
            continue
        if other.aabb.min[2] >= headroom_z:
            continue
        obstructions.append(other)

    obstruction_aabbs = [o.aabb for o in obstructions]
    measurement = walkable_clear_width(lobby_aabb, obstruction_aabbs)  # noqa: F821

    # ---------------------------------------------------------------
    # Hybrid AABB-only path (shapely unavailable, or shapely errored)
    # ---------------------------------------------------------------
    if not measurement["ok"]:
        dx = lobby_aabb.max[0] - lobby_aabb.min[0]
        dy = lobby_aabb.max[1] - lobby_aabb.min[1]
        aabb_width = min(dx, dy)
        if not obstructions and aabb_width >= min_width:
            return {
                "passed": True,
                "message": (
                    f"AABB clear width {aabb_width:.2f} m ≥ {min_width:.2f} m "
                    "(no obstructions; polygon path unavailable)"
                ),
                "details": {
                    "aabb_width": aabb_width,
                    "min_width": min_width,
                    "obstruction_count": 0,
                    "measurement_basis": "aabb",
                    "fallback_reason": measurement.get("reason", ""),
                },
                "measurement_method": "aabb_min_horizontal_dim",
                "measurement_source": "aabb_derived",
                "geometry_tier": "aabb",
                "measured_value": aabb_width,
                "expected_value": min_width,
            }
        return {
            "passed": None,
            "status": "HUMAN_REQUIRED",
            "human_reason": "irregular_geometry",
            "arrival_path": "gate:human_judgment",
            "message": (
                f"AABB-only lobby ({dx:.2f}×{dy:.2f} m) with "
                f"{len(obstructions)} structural element(s) inside its envelope — "
                "reviewer to verify clear corridor width."
            ),
            "details": {
                "aabb_width": aabb_width,
                "min_width": min_width,
                "obstruction_count": len(obstructions),
                "obstruction_guids": [o.guid for o in obstructions[:20]],
                "measurement_basis": "aabb_with_obstructions",
                "fallback_reason": measurement.get("reason", ""),
                "human_reason": "irregular_geometry",
            },
            "measurement_method": "aabb_min_horizontal_dim",
            "measurement_source": "aabb_derived",
            "geometry_tier": "aabb",
        }

    # ---------------------------------------------------------------
    # Polygon path
    # ---------------------------------------------------------------
    walkable_area = measurement["walkable_area_m2"]
    lobby_area = measurement["lobby_area_m2"]
    walkable_ratio = measurement["walkable_area_ratio"]
    n_parts = measurement["walkable_parts"]
    aspect = measurement["aabb_aspect_ratio"]
    clear_width = measurement["clear_width_m"]

    # Disconnected walkable region -> reviewer.
    if n_parts == 0:
        return {
            "passed": None,
            "status": "HUMAN_REQUIRED",
            "human_reason": "irregular_geometry",
            "arrival_path": "gate:human_judgment",
            "message": (
                f"Walkable area collapsed to zero after subtracting "
                f"{len(obstructions)} obstruction(s) — reviewer to verify."
            ),
            "details": {
                "walkable_area_m2": walkable_area,
                "lobby_area_m2": lobby_area,
                "walkable_area_ratio": walkable_ratio,
                "obstruction_count": len(obstructions),
                "obstruction_guids": [o.guid for o in obstructions[:20]],
                "measurement_basis": "polygon_empty",
                "human_reason": "irregular_geometry",
            },
            "measurement_method": "polygon_subtraction",
            "measurement_source": "polygon_derived",
            "geometry_tier": "polygon",
        }
    if n_parts > 1:
        return {
            "passed": None,
            "status": "HUMAN_REQUIRED",
            "human_reason": "irregular_geometry",
            "arrival_path": "gate:human_judgment",
            "message": (
                f"Walkable area split into {n_parts} disjoint piece(s) after "
                f"subtracting {len(obstructions)} obstruction(s) — reviewer to "
                "decide which piece is the primary corridor."
            ),
            "details": {
                "walkable_area_m2": walkable_area,
                "lobby_area_m2": lobby_area,
                "walkable_area_ratio": walkable_ratio,
                "walkable_parts": n_parts,
                "obstruction_count": len(obstructions),
                "obstruction_guids": [o.guid for o in obstructions[:20]],
                "measurement_basis": "polygon_multipart",
                "human_reason": "irregular_geometry",
            },
            "measurement_method": "polygon_subtraction",
            "measurement_source": "polygon_derived",
            "geometry_tier": "polygon",
        }

    # High aspect ratio (long thin envelope) or low walkable ratio (lift core
    # eats most of the envelope) -> reviewer.
    if aspect > max_aspect or walkable_ratio < min_walkable_ratio:
        return {
            "passed": None,
            "status": "HUMAN_REQUIRED",
            "human_reason": "irregular_geometry",
            "arrival_path": "gate:human_judgment",
            "message": (
                f"Irregular lobby: aspect={aspect:.1f} (max {max_aspect}), "
                f"walkable ratio={walkable_ratio:.0%} (min {int(min_walkable_ratio*100)}%) — "
                "single clear-width number not meaningful; reviewer to inspect."
            ),
            "details": {
                "walkable_area_m2": walkable_area,
                "lobby_area_m2": lobby_area,
                "walkable_area_ratio": walkable_ratio,
                "aabb_aspect_ratio": aspect,
                "max_aspect_ratio": max_aspect,
                "min_walkable_area_ratio": min_walkable_ratio,
                "obstruction_count": len(obstructions),
                "obstruction_guids": [o.guid for o in obstructions[:20]],
                "measurement_basis": "polygon_irregular",
                "human_reason": "irregular_geometry",
            },
            "measurement_method": "polygon_subtraction",
            "measurement_source": "polygon_derived",
            "geometry_tier": "polygon",
        }

    passed = clear_width >= min_width
    return {
        "passed": passed,
        "message": (
            f"Clear corridor width {clear_width:.2f} m "
            f"{'≥' if passed else '<'} required {min_width:.2f} m "
            f"(walkable {walkable_area:.1f} of {lobby_area:.1f} m², "
            f"{len(obstructions)} obstruction(s))"
        ),
        "details": {
            "clear_width_m": clear_width,
            "min_width": min_width,
            "walkable_area_m2": walkable_area,
            "lobby_area_m2": lobby_area,
            "walkable_area_ratio": walkable_ratio,
            "obstruction_count": len(obstructions),
            "obstruction_guids": [o.guid for o in obstructions[:20]],
            "measurement_basis": "polygon_inscribed_circle",
            "suggestion": (
                "" if passed
                else "Widen corridor or relocate obstructions to achieve "
                     f"{min_width:.2f} m clear width."
            ),
        },
        "measurement_method": "polygon_inscribed_circle",
        "measurement_source": "polygon_derived",
        "geometry_tier": "polygon",
        "measured_value": clear_width,
        "expected_value": min_width,
    }
