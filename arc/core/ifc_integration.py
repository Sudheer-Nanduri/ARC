# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""Headless IFC integration for ARC-Core (no Blender dependency).

Two public functions:

- ``load_ifc(filepath)`` — open a single IFC file via IfcOpenShell
- ``load_federated_ifc(filepaths, disciplines)`` — open multiple IFC files
  with per-file discipline tags

Both return ``List[Element]``.  Falls back to empty list with a warning
if IfcOpenShell is not installed.

IFC versions supported: IFC4X3 (IFC 4.3), IFC4, and IFC2X3.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from .data_models import AABB, Element

log = logging.getLogger(__name__)

# IFC classes we extract by default
_DEFAULT_IFC_CLASSES = [
    "IfcSpace", "IfcDoor", "IfcWindow",
    "IfcWall", "IfcWallStandardCase", "IfcCurtainWall",
    "IfcSlab", "IfcRoof",
    "IfcBeam", "IfcColumn",
    "IfcStair", "IfcStairFlight",
    "IfcRamp", "IfcRampFlight",
    "IfcRailing",
    "IfcCovering",
    "IfcBuildingStorey",
    "IfcFurnishingElement",
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_ifc(filepath: str) -> List[Element]:
    """Load elements from an IFC file (headless IfcOpenShell path).

    Returns empty list (never raises) if ifcopenshell is unavailable.
    """
    try:
        import ifcopenshell  # type: ignore
    except ImportError:
        log.warning(
            "ifcopenshell not found. Install via 'pip install ifcopenshell' or "
            "run inside Blender with Bonsai and call load_from_blender_scene()."
        )
        return []
    try:
        ifc = ifcopenshell.open(filepath)
    except Exception as exc:
        log.error("Cannot open IFC file '%s': %s", filepath, exc)
        return []
    return _extract_headless(ifc)


def ifc_project_identity(filepath: str) -> Dict[str, Optional[str]]:
    """Return ``{"project_id", "model_source"}`` for an IFC file.

    ``project_id`` is the GlobalId of the first ``IfcProject``; ``model_source``
    is the IFC file name. Either may be ``None`` if the file cannot be opened.
    The deployer wires these onto ``Context`` so waivers and deltas have a
    stable identity. This function travels with arc-spatial
    when separation happens — the core engine treats both values as opaque
    strings.
    """
    out: Dict[str, Optional[str]] = {"project_id": None, "model_source": None}
    try:
        from pathlib import Path
        out["model_source"] = Path(filepath).name
    except Exception:
        out["model_source"] = filepath
    try:
        import ifcopenshell  # type: ignore
    except ImportError:
        return out
    try:
        ifc = ifcopenshell.open(filepath)
    except Exception as exc:
        log.error("Cannot open IFC file '%s' for identity: %s", filepath, exc)
        return out
    try:
        projects = ifc.by_type("IfcProject")
        if projects:
            out["project_id"] = getattr(projects[0], "GlobalId", None)
    except Exception:
        pass
    return out


def load_federated_ifc(
    filepaths: List[str],
    disciplines: Optional[List[str]] = None,
) -> List[Element]:
    """Load multiple IFC files as a federated model.

    Each file is tagged with a discipline label. If disciplines list is
    shorter than filepaths, remaining files get "unknown" discipline.

    Parameters
    ----------
    filepaths : list of file paths
    disciplines : optional list of discipline labels (e.g. ["architectural", "structural"])

    Returns
    -------
    Combined list of Element objects with discipline tags set.
    """
    disciplines = disciplines or []
    all_elements: List[Element] = []
    for i, fp in enumerate(filepaths):
        disc = disciplines[i] if i < len(disciplines) else "unknown"
        elements = load_ifc(fp)
        for el in elements:
            el.discipline = disc
        all_elements.extend(elements)
        log.info("Federated load: %d elements from %s (discipline=%s)", len(elements), fp, disc)
    return all_elements


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
                longname = getattr(entity, "LongName", "") or name
                aabb = _aabb_headless(entity, settings) if geom_enabled else None
                props, psets = _psets_from_entity(entity)
                props["LongName"] = longname
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

    Scans ``IfcRelSpaceBoundary``, ``IfcRelConnectsElements``, and
    ``IfcRelConnectsPathElements`` to populate
    ``element.metadata['connected_to']``.
    """
    if ifc_model is None:
        return

    guid_set = {e.guid for e in elements}
    connections: Dict[str, List[str]] = {}

    for rel_type in ("IfcRelSpaceBoundary", "IfcRelConnectsElements", "IfcRelConnectsPathElements"):
        try:
            for rel in ifc_model.by_type(rel_type):
                if rel_type == "IfcRelSpaceBoundary":
                    a = getattr(rel, "RelatingSpace", None)
                    b = getattr(rel, "RelatedBuildingElement", None)
                else:
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
