# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""DCR_FIRE_01: Stair Width — Minimum.

Residential buildings > 24 m: >= 1.5 m.
Commercial / institutional: >= 2.0 m.

Strategy: use AABB horizontal minimum as stair width.
Building type inferred from 'BuildingType' property (defaults to 'residential').

Source: Mumbai DCR 2034 §38; NBC 2016 Part 4 §5.1
"""


def run(context, element):
    building_type = str(element.properties.get("BuildingType", "residential")).lower()
    min_width = 2.0 if "commercial" in building_type or "institutional" in building_type else 1.5

    # Prefer explicit Width property
    width = element.properties.get("Width") or element.properties.get("width")
    if width is None:
        try:
            dx = element.aabb.max[0] - element.aabb.min[0]
            dy = element.aabb.max[1] - element.aabb.min[1]
            width = min(dx, dy)
        except Exception:
            return {
                "passed": None,
                "message": "Missing geometry: cannot measure stair width",
                "details": {},
            }

    try:
        width = float(width)
    except (TypeError, ValueError):
        return {
            "passed": None,
            "message": f"Invalid Width value '{width}'",
            "details": {},
        }

    passed = width >= min_width

    # -- Build visualization descriptors --
    viz = []
    if element.aabb:
        stair_center = [
            (element.aabb.min[0] + element.aabb.max[0]) / 2.0,
            (element.aabb.min[1] + element.aabb.max[1]) / 2.0,
            element.aabb.min[2],
        ]
        # Determine which axis is the width (shorter)
        dx = element.aabb.max[0] - element.aabb.min[0]
        dy = element.aabb.max[1] - element.aabb.min[1]
        if dx <= dy:
            dim_start = [element.aabb.min[0], stair_center[1], stair_center[2] + 0.1]
            dim_end = [element.aabb.max[0], stair_center[1], stair_center[2] + 0.1]
        else:
            dim_start = [stair_center[0], element.aabb.min[1], stair_center[2] + 0.1]
            dim_end = [stair_center[0], element.aabb.max[1], stair_center[2] + 0.1]

        viz.append({
            "type": "dimension_line", "start": dim_start, "end": dim_end,
            "measured": width, "required": min_width,
            "label": f"Stair width ({building_type})",
        })
        if not passed:
            viz.append({
                "type": "annotation",
                "location": [stair_center[0], stair_center[1], element.aabb.max[2] + 0.3],
                "text": f"Widen by {min_width - width:.3f}m (DCR §38)",
            })

    return {
        "passed": passed,
        "message": (
            f"Stair width={width:.3f} m {'≥' if passed else '<'} "
            f"min={min_width} m ({building_type})"
        ),
        "details": {
            "measured_width_m": width,
            "required_width_m": min_width,
            "building_type": building_type,
            "viz": viz,
            "suggestion": (
                "" if passed
                else f"Widen stair by {min_width - width:.3f} m to comply with DCR §38"
            ),
        },
    }
