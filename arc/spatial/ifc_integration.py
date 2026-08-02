# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: GPL-3.0-or-later

"""IFC integration for ARC — Blender/Bonsai path.

This module provides the Blender-specific IFC loading path via Bonsai.
The headless IfcOpenShell path lives in ``arc.core.ifc_integration`` and
is re-exported here for backward compatibility.

IFC version supported: IFC4X3 (IFC 4.3), IFC4, and IFC2X3.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from ..core.data_models import AABB, Element

# Re-export headless functions from core for backward compatibility
from ..core.ifc_integration import (  # noqa: F401
    load_ifc,
    load_federated_ifc,
    _DEFAULT_IFC_CLASSES,
    _psets_from_entity,
    _infer_discipline,
    _enrich_topology_metadata,
)

log = logging.getLogger(__name__)


def load_from_blender_scene(scope: str = "entire_model") -> List[Element]:
    """Extract ARC Elements from a Bonsai-imported IFC scene.

    Parameters
    ----------
    scope:
        "entire_model" — all mesh objects with IFC entities
        "selected"     — only bpy.context.selected_objects
        "visible"      — only viewport-visible objects
    """
    try:
        import bpy  # type: ignore
    except ImportError:
        log.warning("Not running inside Blender; use load_ifc() instead.")
        return []

    ifc_model = _get_bonsai_ifc_model()
    if ifc_model is None:
        log.warning("No IFC model loaded in Bonsai. Import an IFC file first.")
        return []

    if scope == "selected":
        candidates = list(bpy.context.selected_objects)
    elif scope == "visible":
        candidates = [o for o in bpy.context.scene.objects if not o.hide_viewport]
    else:
        candidates = list(bpy.context.scene.objects)

    elements: List[Element] = []
    seen: set = set()

    for obj in candidates:
        if obj.type != "MESH":
            continue
        entity = _get_ifc_entity(obj, ifc_model)
        if entity is None:
            continue
        try:
            guid: str = entity.GlobalId
        except Exception:
            continue
        if guid in seen:
            continue
        seen.add(guid)

        ifc_class = entity.is_a()
        name = getattr(entity, "Name", None) or obj.name
        aabb = _aabb_from_blender_object(obj)
        props, psets = _psets_from_entity(entity)
        discipline = _infer_discipline(ifc_class)
        has_geom = aabb is not None

        elements.append(Element(
            guid=guid,
            ifc_class=ifc_class,
            discipline=discipline,
            aabb=aabb,
            properties=props,
            property_sets=psets,
            metadata={"name": name, "blender_name": obj.name},
            confidence={
                "semantic": 0.9,
                "geometry": 0.95 if has_geom else 0.0,
                "topology": 0.5,
            },
        ))

    log.info("Loaded %d elements from Blender scene (scope=%s)", len(elements), scope)
    _enrich_topology_metadata(elements, ifc_model)
    return elements


def blender_project_identity() -> Dict[str, Optional[str]]:
    """Return ``{"project_id", "model_source"}`` from the Bonsai-loaded model.

    The Context needs a stable project identity so waivers
    bind correctly. ``project_id`` comes from the active ``IfcProject.GlobalId``,
    ``model_source`` from the IFC file path that Bonsai has loaded. Either may
    be ``None`` if Bonsai is not available — the engine then falls back to
    ``"unknown"`` and logs a warning when waivers are present.
    """
    out: Dict[str, Optional[str]] = {"project_id": None, "model_source": None}
    ifc_model = _get_bonsai_ifc_model()
    if ifc_model is None:
        return out
    try:
        projects = ifc_model.by_type("IfcProject")
        if projects:
            out["project_id"] = getattr(projects[0], "GlobalId", None)
    except Exception:
        pass
    try:
        import bpy  # type: ignore
        path = getattr(bpy.context.scene.BIMProperties, "ifc_file", "") or ""
        if path:
            from pathlib import Path
            out["model_source"] = Path(path).name
    except Exception:
        pass
    return out


def validate_model(elements: List[Element]) -> Dict[str, Any]:
    """Pre-execution model quality check.

    Returns {"valid": bool, "warnings": [...], "errors": [...], ...}.
    Never raises.
    """
    errors: List[str] = []
    warnings: List[str] = []

    if not elements:
        errors.append("No elements loaded. Import an IFC file via Bonsai first.")
        return {"valid": False, "warnings": warnings, "errors": errors,
                "element_count": 0, "data_quality_pct": 0}

    seen_guids: set = set()
    no_aabb = 0
    no_class = 0
    dupes = 0

    for e in elements:
        if not e.guid:
            errors.append(f"Element missing GlobalId (name={e.metadata.get('name', '?')})")
        if e.guid in seen_guids:
            dupes += 1
        seen_guids.add(e.guid)
        if not e.ifc_class:
            no_class += 1
        if e.aabb is None:
            no_aabb += 1

    if dupes:
        warnings.append(f"{dupes} duplicate GUIDs — model may have copy-paste errors.")
    if no_class:
        warnings.append(f"{no_class} elements have no IFC class — semantic rules may return INCONCLUSIVE.")
    if no_aabb:
        warnings.append(f"{no_aabb} elements have no geometry — spatial rules cannot evaluate them.")

    pct_ok = int(100 * (len(elements) - no_class) / max(len(elements), 1))

    return {
        "valid": len(errors) == 0,
        "warnings": warnings,
        "errors": errors,
        "element_count": len(elements),
        "data_quality_pct": pct_ok,
    }


# ---------------------------------------------------------------------------
# Blender / Bonsai helpers
# ---------------------------------------------------------------------------

def _get_bonsai_ifc_model():
    """Return the live IfcOpenShell model from Bonsai, or None."""
    # Bonsai >= 0.8 (ships with Blender 4.2+)
    try:
        import bonsai.tool as tool  # type: ignore
        return tool.Ifc.get()
    except Exception:
        pass
    # Legacy BlenderBIM naming
    try:
        import blenderbim.bim.ifc as _ifc  # type: ignore
        return _ifc.IfcStore.get_file()
    except Exception:
        pass
    # Last resort: read path from scene property
    try:
        import bpy  # type: ignore
        import ifcopenshell  # type: ignore
        path = bpy.context.scene.BIMProperties.ifc_file
        if path:
            return ifcopenshell.open(path)
    except Exception:
        pass
    return None


def _get_ifc_entity(obj, ifc_model):
    """Resolve a Blender object to its IfcOpenShell entity."""
    try:
        import bonsai.tool as tool  # type: ignore
        return tool.Ifc.get_entity(obj)
    except Exception:
        pass
    try:
        ifc_id = obj.BIMObjectProperties.ifc_definition_id
        if ifc_id and ifc_model:
            return ifc_model.by_id(ifc_id)
    except Exception:
        pass
    return None


def _aabb_from_blender_object(obj) -> Optional[AABB]:
    """World-space AABB from a Blender object's eight bounding-box corners."""
    try:
        import mathutils  # type: ignore
        mat = obj.matrix_world
        corners = [mat @ mathutils.Vector(c) for c in obj.bound_box]
        xs = [v.x for v in corners]
        ys = [v.y for v in corners]
        zs = [v.z for v in corners]
        return AABB(
            min=[min(xs), min(ys), min(zs)],
            max=[max(xs), max(ys), max(zs)],
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# IfcOpenShell headless helpers
# ---------------------------------------------------------------------------

def _extract_headless(ifc) -> List[Element]:
    """Pull elements from an IfcOpenShell model object (no Blender required)."""
    try:
        import ifcopenshell.geom as geom  # type: ignore
        settings = geom.settings()
        settings.set(settings.USE_WORLD_COORDS, True)
        geom_enabled = True
    except Exception:
        settings = None
        geom_enabled = False

    elements: List[Element] = []
    for cls in _DEFAULT_IFC_CLASSES:
        try:
            entities = ifc.by_type(cls)
        except Exception:
            continue
        for entity in entities:
            try:
                guid = entity.GlobalId
                name = getattr(entity, "Name", "") or guid
                aabb = _aabb_headless(entity, settings) if geom_enabled else None
                props, psets = _psets_from_entity(entity)
                discipline = _infer_discipline(cls)
                elements.append(Element(
                    guid=guid,
                    ifc_class=cls,
                    discipline=discipline,
                    aabb=aabb,
                    properties=props,
                    property_sets=psets,
                    metadata={"name": name},
                    confidence={
                        "semantic": 0.9,
                        "geometry": 0.85 if aabb else 0.0,
                        "topology": 0.5,
                    },
                ))
            except Exception as exc:
                log.debug("Skipping %s: %s", getattr(entity, "GlobalId", "?"), exc)

    log.info("Loaded %d elements headless from IFC model", len(elements))
    _enrich_topology_metadata(elements, ifc)
    return elements


def _aabb_headless(entity, settings) -> Optional[AABB]:
    try:
        import ifcopenshell.geom as geom  # type: ignore
        shape = geom.create_shape(settings, entity)
        verts = shape.geometry.verts
        if not verts:
            return None
        xs = verts[0::3]
        ys = verts[1::3]
        zs = verts[2::3]
        return AABB(min=[min(xs), min(ys), min(zs)], max=[max(xs), max(ys), max(zs)])
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Topology metadata enrichment
# ---------------------------------------------------------------------------

def _enrich_topology_metadata(elements: List[Element], ifc_model) -> None:
    """Extract IFC relationship data and store in element metadata.

    Scans ``IfcRelSpaceBoundary`` and ``IfcRelConnectsElements`` to populate
    ``element.metadata['connected_to']`` — a list of GUIDs that the element
    is topologically connected to (e.g. a door connecting two spaces).

    When IfcRelSpaceBoundary is unavailable (common with Revit exports that
    omit IfcSpace or space boundaries), the function silently returns and
    the topology engine falls back to AABB-proximity inference.
    """
    if ifc_model is None:
        return

    guid_set = {e.guid for e in elements}
    connections: Dict[str, List[str]] = {}  # guid -> [connected guids]

    # --- IfcRelSpaceBoundary: space <-> bounding element -----------------
    try:
        for rel in ifc_model.by_type("IfcRelSpaceBoundary"):
            space = getattr(rel, "RelatingSpace", None)
            elem = getattr(rel, "RelatedBuildingElement", None)
            if space is None or elem is None:
                continue
            s_guid = getattr(space, "GlobalId", None)
            e_guid = getattr(elem, "GlobalId", None)
            if not s_guid or not e_guid:
                continue
            if s_guid in guid_set and e_guid in guid_set:
                connections.setdefault(s_guid, []).append(e_guid)
                connections.setdefault(e_guid, []).append(s_guid)
    except Exception:
        pass  # IFC schema may not have this type

    # --- IfcRelConnectsElements: element <-> element ---------------------
    try:
        for rel in ifc_model.by_type("IfcRelConnectsElements"):
            a = getattr(rel, "RelatingElement", None)
            b = getattr(rel, "RelatedElement", None)
            if a is None or b is None:
                continue
            a_guid = getattr(a, "GlobalId", None)
            b_guid = getattr(b, "GlobalId", None)
            if not a_guid or not b_guid:
                continue
            if a_guid in guid_set and b_guid in guid_set:
                connections.setdefault(a_guid, []).append(b_guid)
                connections.setdefault(b_guid, []).append(a_guid)
    except Exception:
        pass

    # --- IfcRelConnectsPathElements: wall-to-wall connections ------------
    try:
        for rel in ifc_model.by_type("IfcRelConnectsPathElements"):
            a = getattr(rel, "RelatingElement", None)
            b = getattr(rel, "RelatedElement", None)
            if a is None or b is None:
                continue
            a_guid = getattr(a, "GlobalId", None)
            b_guid = getattr(b, "GlobalId", None)
            if not a_guid or not b_guid:
                continue
            if a_guid in guid_set and b_guid in guid_set:
                connections.setdefault(a_guid, []).append(b_guid)
                connections.setdefault(b_guid, []).append(a_guid)
    except Exception:
        pass

    # Deduplicate and store
    enriched = 0
    for el in elements:
        conn = connections.get(el.guid)
        if conn:
            el.metadata["connected_to"] = list(set(conn))
            el.confidence["topology"] = 0.9
            enriched += 1

    if enriched:
        log.info("Topology enrichment: %d elements with IFC relationship edges", enriched)


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------

def _psets_from_entity(entity) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return (flat_props, structured_psets). Never raises."""
    try:
        import ifcopenshell.util.element as ue  # type: ignore
        psets = ue.get_psets(entity)
    except Exception:
        return {}, {}
    flat: Dict[str, Any] = {}
    for pset_props in psets.values():
        for k, v in pset_props.items():
            flat[k] = v
    return flat, psets


def _infer_discipline(ifc_class: str) -> str:
    cls = ifc_class.lower()
    if any(k in cls for k in (
        "wall", "slab", "column", "beam", "stair", "ramp",
        "door", "window", "covering", "space", "railing",
        "furnishing", "buildingstorey", "roof", "curtain",
    )):
        return "architectural"
    if any(k in cls for k in ("flow", "distribution", "pipe", "duct",
                               "cable", "electric", "sensor", "equipment")):
        return "mep"
    if any(k in cls for k in ("structural", "member", "tendon")):
        return "structural"
    return "unknown"
