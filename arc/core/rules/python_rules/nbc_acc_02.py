# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""NBC_ACC_02: Wheelchair Turning Space Inside Spaces Near Doors.

A 1500 mm diameter turning circle must fit inside any accessible space
adjacent to a door. We approximate this by checking that the smaller
horizontal AABB dimension of the space is >= 1.5 m.

Source: NBC 2016, Part 3 §4.3.2
"""


def run(context, element):
    min_turning_m = 1.5

    try:
        dx = element.aabb.max[0] - element.aabb.min[0]
        dy = element.aabb.max[1] - element.aabb.min[1]
        min_dim = min(dx, dy)
    except Exception:
        return {
            "passed": None,
            "message": "Missing geometry: cannot check turning circle",
            "details": {},
        }

    passed = min_dim >= min_turning_m

    # -- Build visualization descriptors --
    center = [
        (element.aabb.min[0] + element.aabb.max[0]) / 2.0,
        (element.aabb.min[1] + element.aabb.max[1]) / 2.0,
        element.aabb.min[2],
    ]
    viz = [
        {"type": "turning_circle", "center": center, "radius": min_turning_m / 2.0,
         "status": "pass" if passed else "fail",
         "label": f"{int(min_turning_m * 1000)}mm turning circle"},
    ]
    if not passed:
        viz.append({
            "type": "annotation",
            "location": [center[0], center[1], center[2] + 0.3],
            "text": f"Need {min_turning_m - min_dim:.3f}m more width for turning",
        })

    return {
        "passed": passed,
        "message": (
            f"Wheelchair turning circle: min_dim={min_dim:.3f} m "
            f"{'≥' if passed else '<'} {min_turning_m} m"
        ),
        "details": {
            "min_horizontal_dim_m": min_dim,
            "required_turning_diameter_m": min_turning_m,
            "viz": viz,
            "suggestion": (
                "" if passed
                else f"Enlarge space by {min_turning_m - min_dim:.3f} m to fit turning circle"
            ),
        },
    }
