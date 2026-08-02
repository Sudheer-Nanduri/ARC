# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: GPL-3.0-or-later

"""Visualizer helpers — rich spatial compliance visualization.

This module provides Blender-safe helpers to create compliance
volumes with semantic meaning. Each visualization primitive carries custom
properties so users can click an object and immediately understand what rule
it represents, what was measured, and what's required.

In headless or test environments (no ``bpy`` available) the module emits
JSON metadata to ``results/volumes/`` so tests and headless runs can still
inspect debug geometry.

Visualization Descriptor System
-------------------------------
Rules can return ``details["viz"]`` — a list of visualization descriptors.
Each descriptor is a dict with a ``"type"`` key that maps to a rendering
function.  Supported types:

    bbox             Wireframe box (legacy, from clearance_aabb/debug_aabb)
    clearance_zone   Translucent floor-level quad showing required clearance
    turning_circle   Flat ring at floor level (wheelchair turning space etc.)
    probe_cylinder   Upright cylinder representing an occupant or probe
    sweep_path       Chain of probes connected along a route
    dimension_line   Line segment with arrowheads and measurement label
    annotation       3D text label attached to a location
    highlight_zone   Solid translucent volume for emphasis
    marker           Small sphere marker (legacy)
    path_segment     Cylinder connecting two points (legacy, from path)

Rules that do NOT supply ``details["viz"]`` still get backwards-compatible
box / blocker / path rendering from the legacy keys (clearance_aabb, path,
blocking_elements).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

try:
    import bpy  # type: ignore
except Exception:
    bpy = None


# -- Constants ----------------------------------------------------------------

_COLLECTION_NAME = "ARC_Compliance_Volumes"

# Semantic color palette (R, G, B, Alpha)
COLORS = {
    "fail":       (0.91, 0.25, 0.21, 0.35),   # Red - failing element
    "blocker":    (0.97, 0.51, 0.15, 0.55),   # Orange - blocking element
    "clearance":  (0.16, 0.63, 0.98, 0.22),   # Blue - required clearance zone
    "probe":      (0.65, 0.32, 0.87, 0.50),   # Purple - occupant / probe
    "path":       (0.16, 0.63, 0.98, 0.45),   # Blue - connectivity path
    "marker":     (0.98, 0.76, 0.18, 0.85),   # Yellow - measurement point
    "dimension":  (0.98, 0.76, 0.18, 0.90),   # Yellow - dimension line
    "annotation": (1.00, 1.00, 1.00, 1.00),   # White - text
    "link":       (0.98, 0.76, 0.18, 0.35),   # Yellow - link line
    "highlight":  (0.91, 0.25, 0.21, 0.20),   # Red translucent - highlight zone
    "pass":       (0.18, 0.80, 0.44, 0.25),   # Green - pass reference
}

# Material name prefix
_MAT_PREFIX = "ARC_Viz_"


# -- File I/O helpers ---------------------------------------------------------

def _ensure_out_dir(out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)


def _default_results_dir() -> Path:
    try:
        import bpy as _bpy  # type: ignore
        base = Path(_bpy.utils.user_resource("CONFIG")) / "arc" / "results" / "volumes"
    except Exception:
        base = Path.home() / ".arc" / "results" / "volumes"
    return base


def create_volume_metadata(result: Dict[str, Any], out_dir: str | Path | None = None) -> Path:
    """Write a minimal JSON metadata file for a failing RuleResult."""
    out = Path(out_dir) if out_dir is not None else _default_results_dir()
    _ensure_out_dir(out)
    try:
        if bpy is not None:
            model_path = None
            try:
                model_path = bpy.context.scene.BIMProperties.ifc_file
            except Exception:
                model_path = None
            if model_path:
                md = result.get("metadata") or {}
                md["model_path"] = str(model_path)
                result["metadata"] = md
    except Exception:
        pass
    rid = result.get("rule_id") or result.get("ruleId") or "unknown"
    eid = result.get("element_id") or result.get("element_guid") or "unknown"
    filename = out / f"volume_{rid}_{eid}.json"
    with open(filename, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
    return filename


# -- Public API ---------------------------------------------------------------

def show_compliance_volumes(
    results: List[Dict[str, Any]],
    out_dir: str | Path | None = None,
    clear_existing: bool = True,
) -> List[Path]:
    """Write metadata for failing results; in Blender also creates rich viz geometry."""
    paths: List[Path] = []
    for r in results:
        is_fail = (r.get("status") == "FAIL") or (r.get("passed") is False)
        if is_fail:
            p = create_volume_metadata(r, out_dir=out_dir)
            paths.append(p)

    if bpy is not None:
        _create_blender_volumes(results, clear_existing=clear_existing)

    return paths


def clear_visualization(out_dir: str | Path | None = None) -> None:
    """Remove previously created metadata files and Blender objects."""
    out = Path(out_dir) if out_dir is not None else _default_results_dir()
    if out.exists() and out.is_dir():
        for f in out.iterdir():
            try:
                f.unlink()
            except Exception:
                continue

    if bpy is None:
        return

    coll = bpy.data.collections.get(_COLLECTION_NAME)
    if not coll:
        return
    for ob in list(coll.objects):
        mesh_data = getattr(ob, "data", None)
        bpy.data.objects.remove(ob, do_unlink=True)
        # Clean orphan meshes
        try:
            if mesh_data and hasattr(mesh_data, "users") and mesh_data.users == 0:
                bpy.data.meshes.remove(mesh_data)
        except Exception:
            pass
    try:
        bpy.data.collections.remove(coll)
    except Exception:
        pass


# -- Blender helpers ----------------------------------------------------------

def _get_or_create_collection():
    coll = bpy.data.collections.get(_COLLECTION_NAME)
    if coll is None:
        coll = bpy.data.collections.new(_COLLECTION_NAME)
        bpy.context.scene.collection.children.link(coll)
    return coll


_ANNOTATION_OFFSETS: Dict[Tuple[float, float], int] = {}

def _clear_collection_objects(coll) -> None:
    _ANNOTATION_OFFSETS.clear()
    for ob in list(coll.objects):
        mesh_data = getattr(ob, "data", None)
        bpy.data.objects.remove(ob, do_unlink=True)
        try:
            if mesh_data and hasattr(mesh_data, "users") and mesh_data.users == 0:
                bpy.data.meshes.remove(mesh_data)
        except Exception:
            pass


def _set_alpha_blend(mat) -> None:
    """Request alpha-blended rendering across Blender's material APIs.

    EEVEE Next (Blender 4.2+) drives transparency through
    ``surface_render_method``; ``blend_method`` is the legacy property and is
    still present but no longer authoritative. Setting whichever exists keeps
    compliance volumes translucent on old and new Blender alike.
    """
    try:
        mat.surface_render_method = "BLENDED"
    except (AttributeError, TypeError):
        pass
    try:
        mat.blend_method = "BLEND"
    except (AttributeError, TypeError):
        pass


def _get_or_create_material(semantic_key: str) -> Any:
    """Get or create a material for the given semantic color key."""
    name = f"{_MAT_PREFIX}{semantic_key}"
    rgba = COLORS.get(semantic_key, COLORS["fail"])
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name=name)
        mat.use_nodes = True
    _set_alpha_blend(mat)
    mat.diffuse_color = rgba
    bsdf = mat.node_tree.nodes.get("Principled BSDF") if mat.node_tree else None
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (rgba[0], rgba[1], rgba[2], 1.0)
        bsdf.inputs["Alpha"].default_value = rgba[3]
        try:
            bsdf.inputs["Emission Color"].default_value = (rgba[0], rgba[1], rgba[2], 1.0)
            bsdf.inputs["Emission Strength"].default_value = 0.2
        except Exception:
            pass
    return mat


def _assign_material(ob, mat) -> None:
    if ob.data and hasattr(ob.data, "materials"):
        if ob.data.materials:
            ob.data.materials.clear()
        ob.data.materials.append(mat)
    try:
        ob.show_transparent = True
    except Exception:
        pass
    try:
        ob.show_in_front = True
    except Exception:
        pass


def _move_to_collection(ob, coll) -> None:
    """Move object to collection, removing from any current collections."""
    for col in list(ob.users_collection):
        col.objects.unlink(ob)
    coll.objects.link(ob)


def _tag_object(
    ob,
    rule_id: str = "",
    element_guid: str = "",
    viz_type: str = "",
    description: str = "",
    measured: Optional[float] = None,
    required: Optional[float] = None,
) -> None:
    """Add custom properties to an ARC visualization object for discoverability."""
    ob["ARC_volume_type"] = viz_type
    ob["ARC_rule_id"] = rule_id
    ob["ARC_element_guid"] = element_guid
    ob["ARC_description"] = description[:256] if description else ""
    if measured is not None:
        ob["ARC_measured"] = round(measured, 4)
    if required is not None:
        ob["ARC_required"] = round(required, 4)
    # Mark as ARC-managed for cleanup
    ob["ARC_managed"] = True


# -- Center / AABB utilities -------------------------------------------------

def _center_from_aabb(aabb: Dict[str, Any]) -> Tuple[float, float, float]:
    mn = aabb.get("min", [0, 0, 0])
    mx = aabb.get("max", [1, 1, 1])
    return (
        (mn[0] + mx[0]) / 2.0,
        (mn[1] + mx[1]) / 2.0,
        (mn[2] + mx[2]) / 2.0,
    )


def _object_world_aabb(obj):
    try:
        from mathutils import Vector  # type: ignore
        corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
        xs = [corner.x for corner in corners]
        ys = [corner.y for corner in corners]
        zs = [corner.z for corner in corners]
        return {
            "min": [min(xs), min(ys), min(zs)],
            "max": [max(xs), max(ys), max(zs)],
        }
    except Exception:
        return None


def _find_object_center_by_guid(guid: str):
    if not guid:
        return None
    try:
        from .ui_panel import _get_object_guid  # type: ignore
    except Exception:
        return None
    for obj in bpy.context.scene.objects:
        try:
            if _get_object_guid(obj) == guid:
                aabb = _object_world_aabb(obj)
                if aabb:
                    return _center_from_aabb(aabb)
                mw = obj.matrix_world.translation
                return (mw.x, mw.y, mw.z)
        except Exception:
            continue
    return None


def _find_object_by_guid(guid: str):
    if not guid:
        return None
    try:
        from .ui_panel import _get_object_guid  # type: ignore
    except Exception:
        return None
    for obj in bpy.context.scene.objects:
        try:
            if _get_object_guid(obj) == guid:
                return obj
        except Exception:
            continue
    return None


# -- Viz Primitive Renderers --------------------------------------------------

def _create_bbox(
    aabb: Dict[str, Any],
    name: str,
    coll,
    semantic_color: str = "fail",
    rule_id: str = "",
    element_guid: str = "",
    description: str = "",
    measured: Optional[float] = None,
    required: Optional[float] = None,
) -> bool:
    """Wireframe box from AABB — used for element bounding boxes and clearance zones."""
    try:
        mn = aabb.get("min", [0, 0, 0])
        mx = aabb.get("max", [1, 1, 1])
        cx, cy, cz = _center_from_aabb(aabb)
        sx = max(mx[0] - mn[0], 0.05)
        sy = max(mx[1] - mn[1], 0.05)
        sz = max(mx[2] - mn[2], 0.05)
        bpy.ops.mesh.primitive_cube_add(size=1, location=(cx, cy, cz))
        ob = bpy.context.active_object
        ob.scale = (sx / 2.0, sy / 2.0, sz / 2.0)
        ob.name = name
        ob.display_type = "WIRE"
        _move_to_collection(ob, coll)
        _assign_material(ob, _get_or_create_material(semantic_color))
        _tag_object(ob, rule_id=rule_id, element_guid=element_guid,
                    viz_type="bbox", description=description,
                    measured=measured, required=required)
        return True
    except Exception:
        return False


def _create_clearance_zone(
    aabb: Dict[str, Any],
    name: str,
    coll,
    label: str = "",
    rule_id: str = "",
    element_guid: str = "",
    description: str = "",
) -> bool:
    """Translucent floor-plane quad showing a required clearance area."""
    try:
        mn = aabb.get("min", [0, 0, 0])
        mx = aabb.get("max", [1, 1, 1])
        cx = (mn[0] + mx[0]) / 2.0
        cy = (mn[1] + mx[1]) / 2.0
        # Floor level - use min Z
        cz = mn[2] + 0.01  # slightly above floor
        sx = max(mx[0] - mn[0], 0.1)
        sy = max(mx[1] - mn[1], 0.1)
        bpy.ops.mesh.primitive_plane_add(size=1, location=(cx, cy, cz))
        ob = bpy.context.active_object
        ob.scale = (sx / 2.0, sy / 2.0, 1.0)
        ob.name = name
        _move_to_collection(ob, coll)
        _assign_material(ob, _get_or_create_material("clearance"))
        desc = label or description or "Required clearance zone"
        _tag_object(ob, rule_id=rule_id, element_guid=element_guid,
                    viz_type="clearance_zone", description=desc)
        # Add label if provided
        if label:
            _create_text_annotation(
                (cx, cy, cz + 0.15), label, coll,
                rule_id=rule_id, element_guid=element_guid,
                scale=0.12,
            )
        return True
    except Exception:
        return False


def _create_turning_circle(
    center: Tuple[float, float, float],
    radius: float,
    name: str,
    coll,
    label: str = "",
    rule_id: str = "",
    element_guid: str = "",
    status: str = "fail",
) -> bool:
    """Flat ring at floor level representing a required turning circle."""
    try:
        # Use a torus with very flat minor radius for a ring
        bpy.ops.mesh.primitive_torus_add(
            major_radius=radius,
            minor_radius=0.03,
            location=(center[0], center[1], center[2] + 0.02),
        )
        ob = bpy.context.active_object
        ob.name = name
        _move_to_collection(ob, coll)
        color_key = "fail" if status == "fail" else "clearance"
        _assign_material(ob, _get_or_create_material(color_key))
        desc = label or f"Turning circle r={radius:.2f}m"
        _tag_object(ob, rule_id=rule_id, element_guid=element_guid,
                    viz_type="turning_circle", description=desc,
                    required=radius * 2.0)

        # Center marker
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.06, location=center)
        marker = bpy.context.active_object
        marker.name = f"{name}_center"
        _move_to_collection(marker, coll)
        _assign_material(marker, _get_or_create_material("marker"))
        _tag_object(marker, rule_id=rule_id, element_guid=element_guid,
                    viz_type="turning_circle_center", description="Circle center")

        if label:
            _create_text_annotation(
                (center[0], center[1], center[2] + radius + 0.15), label, coll,
                rule_id=rule_id, element_guid=element_guid,
                scale=0.12,
            )
        return True
    except Exception:
        return False


def _create_probe_cylinder(
    center: Tuple[float, float, float],
    radius: float,
    height: float,
    name: str,
    coll,
    label: str = "",
    rule_id: str = "",
    element_guid: str = "",
) -> bool:
    """Upright translucent cylinder representing a person, wheelchair, or spatial probe."""
    try:
        loc = (center[0], center[1], center[2] + height / 2.0)
        bpy.ops.mesh.primitive_cylinder_add(radius=radius, depth=height, location=loc)
        ob = bpy.context.active_object
        ob.name = name
        _move_to_collection(ob, coll)
        _assign_material(ob, _get_or_create_material("probe"))
        desc = label or f"Spatial probe r={radius:.2f}m h={height:.2f}m"
        _tag_object(ob, rule_id=rule_id, element_guid=element_guid,
                    viz_type="probe_cylinder", description=desc)

        if label:
            _create_text_annotation(
                (center[0], center[1], center[2] + height + 0.15), label, coll,
                rule_id=rule_id, element_guid=element_guid,
                scale=0.10,
            )
        return True
    except Exception:
        return False


def _create_dimension_line(
    start: Tuple[float, float, float],
    end: Tuple[float, float, float],
    name: str,
    coll,
    measured: Optional[float] = None,
    required: Optional[float] = None,
    label: str = "",
    rule_id: str = "",
    element_guid: str = "",
) -> bool:
    """Line segment with measurement — shows measured vs required dimension."""
    try:
        from mathutils import Vector  # type: ignore
        sv = Vector(start)
        ev = Vector(end)
        delta = ev - sv
        length = delta.length
        if length <= 1e-6:
            return False
        midpoint = sv.lerp(ev, 0.5)

        # Main line
        bpy.ops.mesh.primitive_cylinder_add(radius=0.02, depth=length, location=midpoint)
        ob = bpy.context.active_object
        ob.name = name
        ob.rotation_mode = "QUATERNION"
        ob.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(delta.normalized())
        _move_to_collection(ob, coll)
        _assign_material(ob, _get_or_create_material("dimension"))

        if measured is not None and required is not None:
            desc = f"{label}: {measured:.3f}m (required: {required:.3f}m)"
        elif label:
            desc = label
        else:
            desc = f"Dimension: {length:.3f}m"

        _tag_object(ob, rule_id=rule_id, element_guid=element_guid,
                    viz_type="dimension_line", description=desc,
                    measured=measured, required=required)

        # End markers (small spheres at each end)
        for pt, suffix in [(start, "_start"), (end, "_end")]:
            bpy.ops.mesh.primitive_uv_sphere_add(radius=0.04, location=pt)
            m = bpy.context.active_object
            m.name = f"{name}{suffix}"
            _move_to_collection(m, coll)
            _assign_material(m, _get_or_create_material("marker"))
            _tag_object(m, rule_id=rule_id, element_guid=element_guid,
                        viz_type="dimension_endpoint", description="Measurement point")

        # Label
        label_text = desc
        label_loc = (midpoint.x, midpoint.y, midpoint.z + 0.15)
        _create_text_annotation(
            label_loc, label_text, coll,
            rule_id=rule_id, element_guid=element_guid,
            scale=0.08,
        )
        return True
    except Exception:
        return False


def _create_text_annotation(
    location: Tuple[float, float, float],
    text: str,
    coll,
    rule_id: str = "",
    element_guid: str = "",
    scale: float = 0.12,
) -> bool:
    """3D text label at a given location — makes volumes self-documenting."""
    try:
        # Shift overlapping annotations diagonally up (Z) and sideways (Y)
        loc_2d = (round(location[0], 2), round(location[1], 2))
        count = _ANNOTATION_OFFSETS.get(loc_2d, 0)
        _ANNOTATION_OFFSETS[loc_2d] = count + 1

        y_offset = float(count) * 0.25
        z_offset = float(count) * 0.20
        final_location = (location[0], location[1] + y_offset, location[2] + z_offset)

        # Truncate text for readability
        display_text = text[:80] if len(text) > 80 else text

        font_curve = bpy.data.curves.new(name=f"ARC_Text_{rule_id}", type="FONT")
        font_curve.body = display_text
        font_curve.size = scale
        font_curve.align_x = "CENTER"
        font_curve.align_y = "BOTTOM"
        # Extrude slightly for visibility
        font_curve.extrude = 0.005

        ob = bpy.data.objects.new(f"ARC_Annot_{rule_id}_{element_guid[:8]}", font_curve)
        ob.location = final_location

        _move_to_collection(ob, coll)
        _assign_material(ob, _get_or_create_material("annotation"))
        _tag_object(ob, rule_id=rule_id, element_guid=element_guid,
                    viz_type="annotation", description=display_text)
        try:
            ob.show_in_front = True
        except Exception:
            pass
        return True
    except Exception:
        return False


def _create_highlight_zone(
    aabb: Dict[str, Any],
    name: str,
    coll,
    rule_id: str = "",
    element_guid: str = "",
    description: str = "",
) -> bool:
    """Solid translucent volume for emphasis (unlike wireframe bbox)."""
    try:
        mn = aabb.get("min", [0, 0, 0])
        mx = aabb.get("max", [1, 1, 1])
        cx, cy, cz = _center_from_aabb(aabb)
        sx = max(mx[0] - mn[0], 0.05)
        sy = max(mx[1] - mn[1], 0.05)
        sz = max(mx[2] - mn[2], 0.05)
        # size=1 creates a 1x1x1 cube, so scale matches dimensions exactly
        bpy.ops.mesh.primitive_cube_add(size=1, location=(cx, cy, cz))
        ob = bpy.context.active_object
        ob.scale = (sx, sy, sz)
        ob.name = name
        ob.display_type = "SOLID"  # solid, not wireframe
        _move_to_collection(ob, coll)
        _assign_material(ob, _get_or_create_material("highlight"))
        _tag_object(ob, rule_id=rule_id, element_guid=element_guid,
                    viz_type="highlight_zone", description=description)
        return True
    except Exception:
        return False


def _create_marker(
    location: Tuple[float, float, float],
    name: str,
    coll,
    rule_id: str = "",
    element_guid: str = "",
    description: str = "",
) -> bool:
    """Small sphere marker — measurement point or waypoint."""
    try:
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.18, location=location)
        ob = bpy.context.active_object
        ob.name = name
        _move_to_collection(ob, coll)
        _assign_material(ob, _get_or_create_material("marker"))
        _tag_object(ob, rule_id=rule_id, element_guid=element_guid,
                    viz_type="marker", description=description or "Marker")
        return True
    except Exception:
        return False


def _create_path_segment(
    start: Tuple[float, float, float],
    end: Tuple[float, float, float],
    name: str,
    coll,
    rule_id: str = "",
    element_guid: str = "",
) -> bool:
    """Cylinder connecting two points — used for paths and links."""
    try:
        from mathutils import Vector  # type: ignore
        sv = Vector(start)
        ev = Vector(end)
        delta = ev - sv
        length = delta.length
        if length <= 1e-6:
            return False
        midpoint = sv.lerp(ev, 0.5)
        bpy.ops.mesh.primitive_cylinder_add(radius=0.08, depth=length, location=midpoint)
        ob = bpy.context.active_object
        ob.name = name
        ob.rotation_mode = "QUATERNION"
        ob.rotation_quaternion = Vector((0.0, 0.0, 1.0)).rotation_difference(delta.normalized())
        _move_to_collection(ob, coll)
        _assign_material(ob, _get_or_create_material("path"))
        _tag_object(ob, rule_id=rule_id, element_guid=element_guid,
                    viz_type="path_segment", description="Path segment")
        return True
    except Exception:
        return False


# -- Viz Descriptor Dispatcher ------------------------------------------------

def _render_viz_descriptor(
    desc: Dict[str, Any],
    coll,
    result: Dict[str, Any],
) -> int:
    """Render a single viz descriptor dict into Blender geometry. Returns count created."""
    viz_type = desc.get("type", "")
    rule_id = result.get("rule_id", "unknown")
    element_guid = result.get("element_id", "")
    label = desc.get("label", "")
    created = 0

    if viz_type == "clearance_zone":
        aabb = desc.get("aabb")
        if aabb and _create_clearance_zone(
            aabb,
            f"ARC_ClearZone_{rule_id}_{element_guid[:8]}",
            coll,
            label=label,
            rule_id=rule_id,
            element_guid=element_guid,
            description=label,
        ):
            created += 1

    elif viz_type == "turning_circle":
        center = desc.get("center")
        radius = desc.get("radius", 0.75)
        status = desc.get("status", "fail")
        if center and _create_turning_circle(
            tuple(center), radius,
            f"ARC_TurnCircle_{rule_id}_{element_guid[:8]}",
            coll,
            label=label,
            rule_id=rule_id,
            element_guid=element_guid,
            status=status,
        ):
            created += 1

    elif viz_type == "probe_cylinder":
        center = desc.get("center")
        radius = desc.get("radius", 0.375)
        height = desc.get("height", 1.2)
        if center and _create_probe_cylinder(
            tuple(center), radius, height,
            f"ARC_Probe_{rule_id}_{element_guid[:8]}",
            coll,
            label=label,
            rule_id=rule_id,
            element_guid=element_guid,
        ):
            created += 1

    elif viz_type == "dimension_line":
        start = desc.get("start")
        end = desc.get("end")
        measured = desc.get("measured")
        required = desc.get("required")
        if start and end and _create_dimension_line(
            tuple(start), tuple(end),
            f"ARC_Dim_{rule_id}_{element_guid[:8]}",
            coll,
            measured=measured,
            required=required,
            label=label,
            rule_id=rule_id,
            element_guid=element_guid,
        ):
            created += 1

    elif viz_type == "annotation":
        location = desc.get("location")
        text = desc.get("text", label)
        if location and text and _create_text_annotation(
            tuple(location), text, coll,
            rule_id=rule_id,
            element_guid=element_guid,
        ):
            created += 1

    elif viz_type == "highlight_zone":
        aabb = desc.get("aabb")
        if aabb and _create_highlight_zone(
            aabb,
            f"ARC_HiZone_{rule_id}_{element_guid[:8]}",
            coll,
            rule_id=rule_id,
            element_guid=element_guid,
            description=label,
        ):
            created += 1

    elif viz_type == "bbox":
        aabb = desc.get("aabb")
        if aabb and _create_bbox(
            aabb,
            f"ARC_BBox_{rule_id}_{element_guid[:8]}",
            coll,
            semantic_color=desc.get("color", "fail"),
            rule_id=rule_id,
            element_guid=element_guid,
            description=label,
        ):
            created += 1

    elif viz_type == "marker":
        location = desc.get("location")
        if location and _create_marker(
            tuple(location),
            f"ARC_Marker_{rule_id}_{element_guid[:8]}",
            coll,
            rule_id=rule_id,
            element_guid=element_guid,
            description=label,
        ):
            created += 1

    elif viz_type == "sweep_path":
        # Chain of probes along a route - future extensibility
        centers = desc.get("centers", [])
        probe_radius = desc.get("probe_radius", 0.5)
        for idx, center in enumerate(centers):
            if idx > 0:
                _create_path_segment(
                    tuple(centers[idx - 1]), tuple(center),
                    f"ARC_SweepSeg_{rule_id}_{idx}",
                    coll,
                    rule_id=rule_id,
                    element_guid=element_guid,
                )
                created += 1
        # Mark failure points specifically
        failure_points = desc.get("failure_points", [])
        for fpi, fp in enumerate(failure_points):
            loc = fp.get("location") or fp.get("center")
            if loc:
                _create_marker(
                    tuple(loc),
                    f"ARC_SweepFail_{rule_id}_{fpi}",
                    coll,
                    rule_id=rule_id,
                    element_guid=element_guid,
                    description=fp.get("reason", "Failure point"),
                )
                created += 1

    return created


# -- Legacy result rendering --------------------------------------------------

def _create_box_for_result(result: Dict[str, Any], coll) -> bool:
    """Legacy: create wireframe box from clearance_aabb/debug_aabb."""
    details = result.get("details", {}) or {}
    aabb = (
        details.get("clearance_aabb")
        or details.get("debug_aabb")
        or result.get("aabb")
        or details.get("aabb")
    )
    if not aabb:
        return False
    rule_id = result.get("rule_id", "unknown")
    element_guid = result.get("element_id", "")
    message = result.get("message", "")
    measured = result.get("measured_value")
    required = result.get("expected_value")
    return _create_bbox(
        aabb,
        f"ARC_Vol_{rule_id}_{element_guid[:8]}",
        coll,
        semantic_color="fail",
        rule_id=rule_id,
        element_guid=element_guid,
        description=message,
        measured=measured,
        required=required,
    )


def _create_route_path(result: Dict[str, Any], coll) -> int:
    """Legacy: create cylinder chain through path nodes."""
    details = result.get("details", {}) or {}
    path_guids = details.get("path")
    if not isinstance(path_guids, list) or len(path_guids) < 2:
        return 0
    rule_id = result.get("rule_id", "unknown")
    element_guid = result.get("element_id", "")
    centers = [_find_object_center_by_guid(guid) for guid in path_guids]
    if any(center is None for center in centers):
        return 0
    created = 0
    for idx in range(len(centers) - 1):
        if _create_path_segment(
            centers[idx], centers[idx + 1],
            f"ARC_Path_{rule_id}_{idx}",
            coll, rule_id=rule_id, element_guid=element_guid,
        ):
            created += 1
    _create_marker(centers[0], f"ARC_Path_Start_{rule_id}", coll,
                   rule_id=rule_id, element_guid=element_guid,
                   description="Path start")
    _create_marker(centers[-1], f"ARC_Path_End_{rule_id}", coll,
                   rule_id=rule_id, element_guid=element_guid,
                   description="Path end")
    return created


def _create_blocker_visuals(result: Dict[str, Any], coll) -> int:
    """Legacy: create wireframe boxes around blocking elements + link lines."""
    details = result.get("details", {}) or {}
    blockers = details.get("blocking_elements")
    if not isinstance(blockers, list) or not blockers:
        return 0
    rule_id = result.get("rule_id", "unknown")
    element_guid = result.get("element_id", "")
    source_center = _find_object_center_by_guid(element_guid)
    created = 0

    for idx, blocker_guid in enumerate(blockers):
        blocker_obj = _find_object_by_guid(blocker_guid)
        blocker_center = _find_object_center_by_guid(blocker_guid)
        if blocker_obj is not None:
            try:
                blocker_aabb = _object_world_aabb(blocker_obj)
                if blocker_aabb is None:
                    raise ValueError("missing blocker aabb")
                if _create_bbox(
                    blocker_aabb,
                    f"ARC_Blocker_{rule_id}_{idx}",
                    coll,
                    semantic_color="blocker",
                    rule_id=rule_id,
                    element_guid=blocker_guid,
                    description=f"Blocking element — obstructs {rule_id}",
                ):
                    created += 1
            except Exception:
                pass
        elif blocker_center is not None:
            if _create_marker(
                blocker_center,
                f"ARC_Blocker_{rule_id}_{idx}",
                coll,
                rule_id=rule_id,
                element_guid=blocker_guid,
                description=f"Blocker — obstructs {rule_id}",
            ):
                created += 1
        if source_center is not None and blocker_center is not None:
            if _create_path_segment(
                source_center, blocker_center,
                f"ARC_BlockerLink_{rule_id}_{idx}",
                coll, rule_id=rule_id, element_guid=element_guid,
            ):
                created += 1
    return created


# -- Main entry point ---------------------------------------------------------

def _create_blender_volumes(
    results: List[Dict[str, Any]],
    clear_existing: bool = True,
) -> None:
    """Create viewport geometry for failing results.

    Processing order:
    1. If result has details["viz"], use the descriptor dispatcher
    2. Otherwise, fall back to legacy box/path/blocker rendering
    3. Always add a text annotation with the failure message
    """
    coll = _get_or_create_collection()
    if clear_existing:
        _clear_collection_objects(coll)

    for r in results:
        is_fail = (r.get("status") == "FAIL") or (r.get("passed") is False)
        if not is_fail:
            continue

        rule_id = r.get("rule_id", "unknown")
        element_guid = r.get("element_id", "")
        details = r.get("details", {}) or {}
        message = r.get("message", "")

        # -- 1. Try rich viz descriptors --
        viz_list = details.get("viz")
        used_viz = False
        if isinstance(viz_list, list) and viz_list:
            for desc in viz_list:
                if isinstance(desc, dict):
                    _render_viz_descriptor(desc, coll, r)
            used_viz = True

        # -- 2. Legacy fallbacks --
        if not used_viz:
            _create_box_for_result(r, coll)
            _create_route_path(r, coll)
            _create_blocker_visuals(r, coll)

            # Add an annotation with the failure message for context
            if message:
                aabb = (
                    details.get("clearance_aabb")
                    or details.get("debug_aabb")
                    or r.get("aabb")
                )
                if aabb:
                    ann_loc = _center_from_aabb(aabb)
                    mn = aabb.get("min", [0, 0, 0])
                    mx = aabb.get("max", [1, 1, 1])
                    ann_loc = (ann_loc[0], ann_loc[1], mx[2] + 0.25)
                    _create_text_annotation(
                        ann_loc, message[:60], coll,
                        rule_id=rule_id, element_guid=element_guid,
                        scale=0.08,
                    )

    # Refresh viewports
    try:
        for area in bpy.context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
    except Exception:
        pass


# -- VizContext helper for Python rules ---------------------------------------

class VizContext:
    """Helper class for Python rule authors to build visualization descriptors.

    Usage in a rule's ``run(context, element)`` function::

        viz = VizContext()
        viz.clearance_zone(aabb, label="1.5m approach zone")
        viz.turning_circle(center, radius=0.75, label="Wheelchair turning")
        viz.probe(center, radius=0.375, height=1.2, label="Occupant")
        viz.dimension(start, end, measured=0.85, required=0.9)
        viz.annotate(location, "Clear 1.5×1.5m required")
        return {"passed": False, "details": {"viz": viz.to_list()}, ...}
    """

    def __init__(self):
        self._descriptors: List[Dict[str, Any]] = []

    def clearance_zone(self, aabb: Dict[str, Any], label: str = "") -> "VizContext":
        self._descriptors.append({"type": "clearance_zone", "aabb": aabb, "label": label})
        return self

    def turning_circle(
        self, center, radius: float = 0.75, label: str = "", status: str = "fail",
    ) -> "VizContext":
        self._descriptors.append({
            "type": "turning_circle",
            "center": list(center),
            "radius": radius,
            "label": label,
            "status": status,
        })
        return self

    def probe(
        self, center, radius: float = 0.375, height: float = 1.2, label: str = "",
    ) -> "VizContext":
        self._descriptors.append({
            "type": "probe_cylinder",
            "center": list(center),
            "radius": radius,
            "height": height,
            "label": label,
        })
        return self

    def dimension(
        self, start, end, measured: Optional[float] = None,
        required: Optional[float] = None, label: str = "",
    ) -> "VizContext":
        self._descriptors.append({
            "type": "dimension_line",
            "start": list(start),
            "end": list(end),
            "measured": measured,
            "required": required,
            "label": label,
        })
        return self

    def annotate(self, location, text: str) -> "VizContext":
        self._descriptors.append({
            "type": "annotation",
            "location": list(location),
            "text": text,
        })
        return self

    def highlight(self, aabb: Dict[str, Any], label: str = "") -> "VizContext":
        self._descriptors.append({"type": "highlight_zone", "aabb": aabb, "label": label})
        return self

    def bbox(self, aabb: Dict[str, Any], color: str = "fail", label: str = "") -> "VizContext":
        self._descriptors.append({"type": "bbox", "aabb": aabb, "color": color, "label": label})
        return self

    def marker(self, location, label: str = "") -> "VizContext":
        self._descriptors.append({"type": "marker", "location": list(location), "label": label})
        return self

    def sweep_path(
        self, centers, probe_radius: float = 0.5,
        failure_points: Optional[List[Dict]] = None, label: str = "",
    ) -> "VizContext":
        self._descriptors.append({
            "type": "sweep_path",
            "centers": [list(c) for c in centers],
            "probe_radius": probe_radius,
            "failure_points": failure_points or [],
            "label": label,
        })
        return self

    def to_list(self) -> List[Dict[str, Any]]:
        return list(self._descriptors)
