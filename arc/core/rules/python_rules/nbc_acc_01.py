# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""NBC_ACC_01: Wheelchair Approach Clearance — Doors.

Checks that an IfcDoor has sufficient approach clearance on the pull side
(1.5 m x 1.5 m) and push side (1.2 m x 1.5 m) per NBC 2016, Part 3 §4.3.1.

Design note (hybrid-fallback principle, consistent with corridor_width):
the manoeuvring requirement applies to the approach ZONE in front of and
behind the door — not to the door element itself. The door's own AABB can
never measure the zone, so it is not used as a pass/fail threshold.

Method:
  1. Door orientation is inferred from the AABB (thin axis = wall normal).
     If the footprint is too square to infer orientation reliably, the case
     routes to a reviewer (HUMAN_REQUIRED / irregular_geometry).
  2. Rectangular approach zones are constructed on both sides of the door.
     Since pull/push sides are unknown from geometry alone, both depth
     assignments (1.5/1.2 and 1.2/1.5) are evaluated.
  3. Obstruction = a modeled element (excluding the host wall system and
     spatial containers) intersecting a required zone. FAIL is only emitted
     on positive obstruction evidence under BOTH assignments; obstruction
     under only one assignment routes to a reviewer. No obstruction on
     either side yields PASS with an explicit recorded assumption (zone
     containment within the storey is not verified from AABB data).

Source: NBC 2016, Part 3 §4.3.1
"""

RULE_METADATA = {
    "selector": {"ifc_class": "IfcDoor"},
    "severity": "critical",
    "category": "accessibility",
    "source": "NBC 2016, Part 3 §4.3.1",
    "params": {
        "pull_depth_m": 1.5,
        "push_depth_m": 1.2,
        "zone_width_m": 1.5,
        "max_squareness": 0.6,
    },
    "interpretation_notes": (
        "Approach clearance is assessed on constructed zones beyond each door "
        "face, not on the door AABB. FAIL requires positive obstruction "
        "evidence on both pull/push assignments; ambiguous orientation or "
        "single-assignment obstruction routes to a reviewer."
    ),
}

_PULL_DEPTH = 1.5
_PUSH_DEPTH = 1.2
_ZONE_WIDTH = 1.5
_MAX_SQUARENESS = 0.6  # thickness/width above this = orientation ambiguous

# Spatial containers and the door itself are never obstructions. Host walls
# are excluded dynamically (walls intersecting the door AABB).
_IGNORE_CLASSES = {
    "IfcDoor", "IfcSpace", "IfcBuildingStorey", "IfcSlab", "IfcRoof",
    "IfcCovering", "IfcWindow",
}
_WALL_CLASSES = {"IfcWall", "IfcWallStandardCase", "IfcCurtainWall"}


def _intersects_2d(a_min, a_max, b_min, b_max):
    return not (
        a_max[0] <= b_min[0] or a_min[0] >= b_max[0]
        or a_max[1] <= b_min[1] or a_min[1] >= b_max[1]
    )


def _zone(door_aabb, axis, direction, depth, width):
    """Rectangular zone beyond one door face along `axis` (0=x, 1=y)."""
    other = 1 - axis
    center_other = (door_aabb.min[other] + door_aabb.max[other]) / 2.0
    zmin = list(door_aabb.min)
    zmax = list(door_aabb.max)
    if direction > 0:
        zmin[axis] = door_aabb.max[axis]
        zmax[axis] = door_aabb.max[axis] + depth
    else:
        zmax[axis] = door_aabb.min[axis]
        zmin[axis] = door_aabb.min[axis] - depth
    zmin[other] = center_other - width / 2.0
    zmax[other] = center_other + width / 2.0
    zmin[2] = door_aabb.min[2]
    zmax[2] = door_aabb.min[2] + 2.0  # manoeuvring volume height
    return {"min": zmin, "max": zmax}


def _blockers_in(context, zone, host_wall_guids, door_guid):
    found = []
    for other in context.query_bbox(zone["min"], zone["max"]):
        if other.guid == door_guid or other.guid in host_wall_guids:
            continue
        if other.ifc_class in _IGNORE_CLASSES:
            continue
        found.append(other.guid)
    return found


def run(context, element):
    if element.ifc_class not in ("IfcDoor",):
        return {"passed": None, "message": "Not an IfcDoor — skipping", "details": {}}

    if element.aabb is None:
        return {
            "passed": None,
            "message": "Missing geometry: cannot construct wheelchair approach zones",
            "details": {"missing": "aabb", "gate": "geometry_limited"},
        }

    dx = element.aabb.max[0] - element.aabb.min[0]
    dy = element.aabb.max[1] - element.aabb.min[1]
    if dx <= 0 or dy <= 0:
        return {
            "passed": None,
            "message": "Degenerate door footprint: cannot construct approach zones",
            "details": {"missing": "aabb", "gate": "geometry_limited"},
        }

    thickness, width = (dx, dy) if dx < dy else (dy, dx)
    axis = 0 if dx < dy else 1  # thin axis = approach direction

    # Orientation ambiguity -> reviewer
    if width > 0 and (thickness / width) > _MAX_SQUARENESS:
        return {
            "passed": None,
            "status": "HUMAN_REQUIRED",
            "human_reason": "irregular_geometry",
            "arrival_path": "rule:orientation_ambiguous",
            "message": (
                f"Door footprint too square ({thickness:.2f} x {width:.2f} m) to "
                "infer swing orientation; reviewer to verify approach clearance"
            ),
            "details": {
                "measurement_method": "approach_zone_aabb",
                "footprint": [round(dx, 3), round(dy, 3)],
            },
        }

    # Host wall system = walls whose footprint intersects the door AABB
    host_wall_guids = set()
    for other in context.query_bbox(element.aabb.min, element.aabb.max):
        if other.ifc_class in _WALL_CLASSES and _intersects_2d(
            other.aabb.min, other.aabb.max, element.aabb.min, element.aabb.max
        ):
            host_wall_guids.add(other.guid)

    zone_width = max(_ZONE_WIDTH, width)
    sides = {}
    for direction, tag in ((+1, "side_a"), (-1, "side_b")):
        z_pull = _zone(element.aabb, axis, direction, _PULL_DEPTH, zone_width)
        z_push = _zone(element.aabb, axis, direction, _PUSH_DEPTH, zone_width)
        sides[tag] = {
            "pull_zone": z_pull,
            "push_zone": z_push,
            "pull_blockers": _blockers_in(context, z_pull, host_wall_guids, element.guid),
            "push_blockers": _blockers_in(context, z_push, host_wall_guids, element.guid),
        }

    # Assignment 1: side_a=pull(1.5), side_b=push(1.2)
    # Assignment 2: side_a=push(1.2), side_b=pull(1.5)
    blocked_1 = bool(sides["side_a"]["pull_blockers"] or sides["side_b"]["push_blockers"])
    blocked_2 = bool(sides["side_a"]["push_blockers"] or sides["side_b"]["pull_blockers"])

    all_blockers = sorted(set(
        sides["side_a"]["pull_blockers"] + sides["side_b"]["pull_blockers"]
        + sides["side_a"]["push_blockers"] + sides["side_b"]["push_blockers"]
    ))

    viz = [
        {"type": "clearance_zone", "aabb": sides["side_a"]["pull_zone"],
         "label": f"Approach zone A ({_PULL_DEPTH}m)"},
        {"type": "clearance_zone", "aabb": sides["side_b"]["pull_zone"],
         "label": f"Approach zone B ({_PULL_DEPTH}m)"},
    ]
    for guid in all_blockers:
        b_el = context.element_by_id(guid)
        if b_el and b_el.aabb:
            viz.append({
                "type": "bbox",
                "aabb": {"min": list(b_el.aabb.min), "max": list(b_el.aabb.max)},
                "color": "blocker", "label": "Obstruction",
            })

    common_details = {
        "measurement_method": "approach_zone_aabb",
        "zone_width_m": zone_width,
        "pull_depth_m": _PULL_DEPTH,
        "push_depth_m": _PUSH_DEPTH,
        "blocking_elements": all_blockers,
        "viz": viz,
        "assumptions": [
            "Approach zones constructed from door AABB; swing direction unknown",
            "Zone containment within the storey boundary not verified from AABB data",
        ],
    }

    if blocked_1 and blocked_2:
        return {
            "passed": False,
            "message": (
                f"Wheelchair approach blocked: {len(all_blockers)} modeled "
                "obstruction(s) intrude into the required manoeuvring zones on "
                "both pull/push assignments"
            ),
            "details": {
                **common_details,
                "suggestion": "Clear the approach zones on both sides of the door "
                              "or relocate the obstructing elements.",
            },
        }

    if blocked_1 or blocked_2:
        return {
            "passed": None,
            "status": "HUMAN_REQUIRED",
            "human_reason": "irregular_geometry",
            "arrival_path": "rule:swing_assignment_ambiguous",
            "message": (
                "Obstruction found under one pull/push assignment only; swing "
                "direction determines compliance — reviewer to verify"
            ),
            "details": common_details,
        }

    return {
        "passed": True,
        "message": (
            "No modeled obstructions in wheelchair approach zones "
            f"({_PULL_DEPTH} m pull / {_PUSH_DEPTH} m push, width {zone_width:.2f} m) "
            "on either side"
        ),
        "details": common_details,
    }
