# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""DCR_FIRE_03: Egress Path Distance - Travel to nearest fire exit.

Uses the topology graph to find a route from a space to the nearest tagged
fire exit. Path length is estimated from center-to-center distances along
that route, with a hop-count fallback when geometry is incomplete.
"""


def run(context, element):
    def _center(el):
        return (
            (el.aabb.min[0] + el.aabb.max[0]) * 0.5,
            (el.aabb.min[1] + el.aabb.max[1]) * 0.5,
            (el.aabb.min[2] + el.aabb.max[2]) * 0.5,
        )

    def _distance(a, b):
        ac = _center(a)
        bc = _center(b)
        dx = ac[0] - bc[0]
        dy = ac[1] - bc[1]
        dz = ac[2] - bc[2]
        return (dx * dx + dy * dy + dz * dz) ** 0.5

    def _is_exit_candidate(candidate):
        if str(candidate.properties.get("IsFireExit", "")).lower() in ("true", "1", "yes"):
            return True
        if str(candidate.properties.get("FireExit", "")).lower() in ("true", "1", "yes"):
            return True
        for key in ("Name", "LongName", "DoorType", "Tag"):
            value = str(candidate.properties.get(key, "")).lower()
            if "exit" in value or "egress" in value or "fire escape" in value:
                return True
        return False

    if "fire_exit_ids" not in context.metadata:
        context.metadata["fire_exit_ids"] = [e.guid for e in context.elements if _is_exit_candidate(e)]
    exit_ids = context.metadata["fire_exit_ids"]

    if not exit_ids:
        return {
            "passed": None,
            "message": "No fire exit elements found in model -> tag exit doors with IsFireExit=True.",
            "details": {"missing": "fire_exit_elements"},
        }

    topology = context.topology
    best_path = None
    for exit_id in exit_ids:
        path = topology.shortest_path(context.elements, element.guid, exit_id)
        if path and (best_path is None or len(path) < len(best_path)):
            best_path = path

    if best_path is None:
        return {
            "passed": None,
            "message": "No connected path to a fire exit was found.",
            "details": {"element": element.guid},
        }

    hop_count = len(best_path) - 1
    segment_lengths = []
    used_fallback = False
    path_centers = []
    for idx in range(len(best_path)):
        el_node = context.element_by_id(best_path[idx])
        if el_node and el_node.aabb:
            path_centers.append(list(_center(el_node)))
    for idx in range(len(best_path) - 1):
        src = context.element_by_id(best_path[idx])
        dst = context.element_by_id(best_path[idx + 1])
        if src is None or dst is None or src.aabb is None or dst.aabb is None:
            used_fallback = True
            break
        segment_lengths.append(_distance(src, dst))

    approx_dist_m = sum(segment_lengths) if segment_lengths and not used_fallback else hop_count * 3.0
    max_dist_m = 30.0
    passed = approx_dist_m <= max_dist_m

    # -- Build visualization descriptors --
    viz = []
    if path_centers and len(path_centers) >= 2:
        viz.append({
            "type": "sweep_path",
            "centers": path_centers,
            "probe_radius": 0.4,
            "label": f"Egress route ({approx_dist_m:.1f}m)",
            "failure_points": [] if passed else [{
                "location": path_centers[0],
                "reason": f"Travel distance {approx_dist_m:.1f}m > {max_dist_m}m max",
            }],
        })
    # Annotate start and end
    if path_centers:
        viz.append({
            "type": "annotation",
            "location": [path_centers[0][0], path_centers[0][1], path_centers[0][2] + 0.3],
            "text": f"Start: {approx_dist_m:.1f}m to exit" if not passed else "Start",
        })
        if len(path_centers) > 1:
            viz.append({
                "type": "marker",
                "location": path_centers[-1],
                "label": "Fire exit",
            })

    return {
        "passed": passed,
        "message": (
            f"Egress distance ~{approx_dist_m:.1f} m ({hop_count} hops) "
            f"{'<=' if passed else '>'} max={max_dist_m:.1f} m"
        ),
        "details": {
            "hop_count": hop_count,
            "approx_distance_m": approx_dist_m,
            "path_length_m": approx_dist_m,
            "segment_lengths_m": segment_lengths,
            "max_distance_m": max_dist_m,
            "path": best_path,
            "destination_exit_id": best_path[-1],
            "measurement_method": "topology_path_centerline" if not used_fallback else "topology_hop_proxy",
            "viz": viz,
            "suggestion": (
                "" if passed
                else f"Add a nearer exit or improve connectivity; current route is ~{approx_dist_m:.1f} m."
            ),
        },
    }
