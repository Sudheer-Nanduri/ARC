# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""NBC_VENT_01: Room Height — Minimum Ceiling.

Habitable rooms >= 2.75 m; non-habitable (bathroom, storage) >= 2.4 m.
Ceiling height taken as AABB z-dimension.

Source: NBC 2016, Part 8 §3.1
"""


def run(context, element):
    space_type = str(element.properties.get("SpaceType", "habitable")).lower()
    non_habitable_types = ("bathroom", "toilet", "wc", "storage", "closet", "utility")
    is_non_habitable = any(t in space_type for t in non_habitable_types)
    min_height = 2.4 if is_non_habitable else 2.75

    height = element.properties.get("Height") or element.properties.get("ClearHeight")
    if height is None:
        try:
            height = element.aabb.max[2] - element.aabb.min[2]
        except Exception:
            return {
                "passed": None,
                "message": "Missing geometry: cannot measure ceiling height",
                "details": {},
            }

    try:
        height = float(height)
    except (TypeError, ValueError):
        return {
            "passed": None,
            "message": f"Invalid height value '{height}'",
            "details": {},
        }

    passed = height >= min_height
    label = "non-habitable" if is_non_habitable else "habitable"
    return {
        "passed": passed,
        "message": (
            f"Ceiling height={height:.3f} m {'≥' if passed else '<'} "
            f"min={min_height} m ({label})"
        ),
        "details": {
            "measured_height_m": height,
            "required_height_m": min_height,
            "space_type": space_type,
            "suggestion": (
                "" if passed
                else f"Raise ceiling by {min_height - height:.3f} m to comply with NBC §3.1"
            ),
        },
    }
