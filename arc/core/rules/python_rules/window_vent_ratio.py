# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""window_vent_ratio: Natural Ventilation Area Ratio — Windows.

Window openable area as a fraction of the associated wall area must meet the
minimum ratio (default 0.1). Falls back to AABB-derived areas when the
authored properties are absent.

Source: NBC 2016, Part 8 §3.4.1
"""


def run(context, element):
    """Check window-to-wall ratio (ventilation) against minimum ratio.

    Uses `element.properties['window_area']` and `element.properties['wall_area']`.
    """
    try:
        min_ratio = float(element.properties.get("min_ratio", 0.1))
    except Exception:
        min_ratio = 0.1

    window_area = element.properties.get("window_area")
    if window_area is None and element.aabb:
        # Fallback to geometric surface area (width * height)
        dx = element.aabb.max[0] - element.aabb.min[0]
        dy = element.aabb.max[1] - element.aabb.min[1]
        dz = element.aabb.max[2] - element.aabb.min[2]
        window_area = max(dx, dy) * dz

    wall_area = element.properties.get("wall_area")
    if wall_area is None and element.aabb:
        # Fallback to finding the overlapping wall using spatial index
        for n in context.neighbours(element):
            if n.ifc_class in ("IfcWall", "IfcWallStandardCase") and n.aabb:
                w_dx = n.aabb.max[0] - n.aabb.min[0]
                w_dy = n.aabb.max[1] - n.aabb.min[1]
                w_dz = n.aabb.max[2] - n.aabb.min[2]
                wall_area = max(w_dx, w_dy) * w_dz
                break

    try:
        window_area = float(window_area) if window_area is not None else 0.0
        wall_area = float(wall_area) if wall_area is not None else 0.0
    except Exception:
        return {"passed": None, "message": "invalid area values", "details": {}}

    if wall_area <= 0:
        return {"passed": None, "message": "wall area missing or zero. Could not evaluate vent ratio.", "details": {}}

    ratio = window_area / wall_area
    passed = ratio >= min_ratio
    return {
        "passed": passed,
        "message": f"ratio={ratio:.3f} min={min_ratio}",
        "details": {
            "window_area_m2": window_area,
            "wall_area_m2": wall_area,
            "ratio": ratio,
            "min_ratio": min_ratio,
            "suggestion": "Increase the window size to meet ventilation minimums." if not passed else ""
        }
    }
