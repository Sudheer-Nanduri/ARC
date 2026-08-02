# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""Orchestrator context for ARC rule execution.

Wires together the element list, spatial index, and topology engine so rules
can query neighbours and connectivity without importing engine modules directly.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from .data_models import Element, WaiverRecord


class Context:
    """Central context passed to every rule.

    Attributes
    ----------
    elements:
        All elements in the model.
    metadata:
        Arbitrary key-value store for execution-time annotations.
    project_id:
        Identifier for the project; waivers are bound to it and never auto-apply
        across projects. Defaults to ``model_source`` when not supplied, falling
        back to ``"unknown"`` only when nothing is available.
    model_source:
        Opaque source identifier — file name, Speckle stream id, Revit project
        name — used for round-tripping deltas and as a project_id fallback.
    waivers:
        Map of waiver_id → WaiverRecord. Loaded by the deployer at runtime; not
        committed to code. The engine reads but never mutates this map.
    external_data:
        Per-key external inputs gated by ``requires_external`` on rules.
    external_routing:
        Per-deployment override of ``ROUTING_REGISTRY`` for this run (reserved).
    """

    def __init__(
        self,
        elements: Optional[List[Element]] = None,
        project_id: Optional[str] = None,
        model_source: Optional[str] = None,
        waivers: Optional[Dict[str, WaiverRecord]] = None,
        external_data: Optional[Dict[str, Any]] = None,
        external_routing: Optional[Dict[str, Any]] = None,
    ):
        self.elements: List[Element] = list(elements or [])
        self.metadata: Dict[str, Any] = {}
        self._spatial_index = None
        self._topology = None
        self.model_source: Optional[str] = model_source
        # project_id falls back to model_source, then "unknown"
        self.project_id: str = project_id or model_source or "unknown"
        self.waivers: Dict[str, WaiverRecord] = dict(waivers or {})
        self.external_data: Dict[str, Any] = dict(external_data or {})
        self.external_routing: Dict[str, Any] = dict(external_routing or {})

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_element(self, element: Element) -> None:
        self.elements.append(element)
        # Invalidate cached indexes so they are rebuilt on next access
        self._spatial_index = None
        self._topology = None

    # ------------------------------------------------------------------
    # Lazy engine accessors
    # ------------------------------------------------------------------

    @property
    def spatial_index(self):
        """Return a built SpatialIndex, constructing it if needed."""
        if self._spatial_index is None:
            from .geo_engine import SpatialIndex
            self._spatial_index = SpatialIndex(self.elements)
        return self._spatial_index

    @property
    def topology(self):
        """Return a TopologyEngine pre-loaded with current elements."""
        if self._topology is None:
            from .topology_engine import TopologyEngine
            self._topology = TopologyEngine()
        return self._topology

    # ------------------------------------------------------------------
    # Convenience query helpers
    # ------------------------------------------------------------------

    def elements_by_class(self, ifc_class: str) -> List[Element]:
        """Return all elements whose ifc_class matches (case-insensitive)."""
        key = ifc_class.lower()
        return [e for e in self.elements if e.ifc_class.lower() == key]

    # Spec alias
    filter_by_ifc_class = elements_by_class

    def filter_by_property(self, key: str, value: Any) -> List[Element]:
        """Return elements whose properties[key] equals value (string-insensitive)."""
        val_str = str(value).lower()
        return [
            e for e in self.elements
            if str(e.properties.get(key, "")).lower() == val_str
        ]

    def element_by_id(self, guid: str) -> Optional[Element]:
        """Return the element with the given guid, or None."""
        for e in self.elements:
            if e.guid == guid:
                return e
        return None

    def neighbours(self, element: Element) -> List[Element]:
        """Return elements whose AABBs intersect the given element's AABB."""
        if element.aabb is None:
            return []
        return self.spatial_index.query_aabb(element.aabb)

    def query_bbox(self, min_coords: List[float], max_coords: List[float]) -> List[Element]:
        """Return elements intersecting an arbitrary bounding box."""
        from .data_models import AABB
        return self.spatial_index.query_aabb(AABB(min=min_coords, max=max_coords))

    def get_nearby_elements(self, element: Element, radius: float) -> List[Element]:
        """Return elements within *radius* metres of element centre. O(log n)."""
        return self.spatial_index.get_nearby_elements(element, radius)

    def connected_components(self) -> List[Set[str]]:
        """Return connected components (sets of guids) based on connectivity."""
        return self.topology.connected_components(self.elements)

    def shortest_path(self, src_id: str, dst_id: str) -> Optional[list]:
        """Shortest path between two elements by hop count."""
        return self.topology.shortest_path(self.elements, src_id, dst_id)

    def distance_between(self, src_id: str, dst_id: str) -> Optional[float]:
        """Weighted shortest path length (metres) between two elements."""
        return self.topology.shortest_path_length(self.elements, src_id, dst_id)

    def __len__(self) -> int:
        return len(self.elements)

    def __repr__(self) -> str:
        return f"Context(elements={len(self.elements)})"
