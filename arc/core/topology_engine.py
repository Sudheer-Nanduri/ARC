# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""Topology engine: connectivity graph and pathfinding.

Uses NetworkX when available (bundled in wheelhouse), with a pure-Python
fallback.  Graph construction is **hybrid**:

1. **Semantic edges** (high confidence) — derived from IFC relationship
   metadata (``IfcRelSpaceBoundary``, ``IfcRelConnectsElements``) stored in
   ``element.metadata['connected_to']`` during IFC loading.
2. **AABB-proximity edges** (low confidence) — added only between elements
   that lack semantic edges *and* whose bounding boxes overlap.  These edges
   carry a ``confidence`` weight of 0.3 so pathfinding algorithms can prefer
   semantic edges.

Edge weights are Euclidean centre-to-centre distance, enabling realistic
travel-distance estimation via Dijkstra.

The public API (``build_graph``, ``connected_components``, ``shortest_path``,
``shortest_path_length``) is kept backward-compatible so existing Python
rules (e.g. ``dcr_fire_03.py``) continue to work without changes.
"""
from __future__ import annotations

import logging
import math
from collections import deque
from typing import Dict, List, Optional, Set, Tuple

from .data_models import AABB, Element
from .geo_engine import aabb_intersect

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# NetworkX import (optional - pure-Python fallback if missing)
# ---------------------------------------------------------------------------
try:
    import networkx as nx  # type: ignore

    _HAS_NX = True
except ImportError:
    _HAS_NX = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _center(aabb: AABB) -> Tuple[float, float, float]:
    return (
        (aabb.min[0] + aabb.max[0]) * 0.5,
        (aabb.min[1] + aabb.max[1]) * 0.5,
        (aabb.min[2] + aabb.max[2]) * 0.5,
    )


def _euclidean(a: Tuple[float, ...], b: Tuple[float, ...]) -> float:
    return math.sqrt(sum((ai - bi) ** 2 for ai, bi in zip(a, b)))


# IFC classes that represent traversable connections (doors, openings)
_CONNECTIVE_CLASSES = frozenset({
    "IfcDoor", "IfcOpening", "IfcOpeningElement",
})

# IFC classes that represent spaces people move through
_SPACE_CLASSES = frozenset({
    "IfcSpace", "IfcStair", "IfcStairFlight",
    "IfcRamp", "IfcRampFlight", "IfcBuildingStorey",
})


class TopologyEngine:
    """Hybrid semantic + AABB connectivity graph with weighted pathfinding."""

    def __init__(self):
        self._adj: Optional[Dict[str, Set[str]]] = None
        self._nx_graph = None  # NetworkX graph (when available)
        self._elements_by_id: Dict[str, Element] = {}
        self._has_semantic_edges: bool = False

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    def build_graph(self, elements: List[Element]) -> Dict[str, Set[str]]:
        """Build (or return cached) connectivity graph.

        Returns an adjacency dict ``{id: set_of_neighbour_ids}`` for
        backward compatibility.  Internally also builds a weighted
        NetworkX graph when the library is available.
        """
        if self._adj is not None:
            return self._adj

        self._elements_by_id = {e.guid: e for e in elements}
        adj: Dict[str, Set[str]] = {e.guid: set() for e in elements}

        # --- Pass 1: semantic edges from IFC relationships ----------------
        semantic_pairs: Set[Tuple[str, str]] = set()
        for el in elements:
            connected_to = el.metadata.get("connected_to")
            if not connected_to:
                continue
            for target_id in connected_to:
                if target_id in self._elements_by_id:
                    pair = (min(el.guid, target_id), max(el.guid, target_id))
                    semantic_pairs.add(pair)

        if semantic_pairs:
            self._has_semantic_edges = True
            log.info(
                "Topology: %d semantic edges from IFC relationships",
                len(semantic_pairs),
            )

        for a_id, b_id in semantic_pairs:
            adj[a_id].add(b_id)
            adj[b_id].add(a_id)

        # --- Pass 2: AABB-proximity fallback for elements w/o semantic ----
        #
        # Only connect *spatial* elements (spaces, doors, stairs, ramps)
        # to keep the graph meaningful - otherwise walls and slabs would
        # create spurious connections.
        spatial_elements = [
            e for e in elements
            if e.aabb is not None
            and (e.ifc_class in _SPACE_CLASSES or e.ifc_class in _CONNECTIVE_CLASSES)
        ]

        # Elements already fully connected via semantic edges don't need
        # AABB fallback.
        ids_with_semantic = set()
        for a_id, b_id in semantic_pairs:
            ids_with_semantic.add(a_id)
            ids_with_semantic.add(b_id)

        aabb_candidates = [
            e for e in spatial_elements if e.guid not in ids_with_semantic
        ]

        aabb_edge_count = 0
        for i, a in enumerate(aabb_candidates):
            for b in aabb_candidates[i + 1:]:
                if aabb_intersect(a.aabb, b.aabb):
                    adj[a.guid].add(b.guid)
                    adj[b.guid].add(a.guid)
                    aabb_edge_count += 1

        # Also connect AABB-candidate spaces to nearby connective elements
        # (doors) that *do* have semantic edges - bridges the two layers.
        connective_with_semantic = [
            e for e in elements
            if e.guid in ids_with_semantic and e.ifc_class in _CONNECTIVE_CLASSES
            and e.aabb is not None
        ]
        for space in aabb_candidates:
            if space.ifc_class not in _SPACE_CLASSES:
                continue
            for door in connective_with_semantic:
                if aabb_intersect(space.aabb, door.aabb):
                    adj[space.guid].add(door.guid)
                    adj[door.guid].add(space.guid)
                    aabb_edge_count += 1

        if aabb_edge_count:
            log.info(
                "Topology: %d AABB-fallback edges (low confidence) for "
                "elements without IFC relationships",
                aabb_edge_count,
            )

        self._adj = adj

        # --- Build weighted NetworkX graph --------------------------------
        if _HAS_NX:
            G = nx.Graph()
            for eid in adj:
                G.add_node(eid)
            for a_id in adj:
                for b_id in adj[a_id]:
                    if G.has_edge(a_id, b_id):
                        continue
                    a_el = self._elements_by_id.get(a_id)
                    b_el = self._elements_by_id.get(b_id)
                    dist = 3.0  # default hop distance when geometry missing
                    if a_el and b_el and a_el.aabb and b_el.aabb:
                        dist = _euclidean(_center(a_el.aabb), _center(b_el.aabb))
                    is_semantic = (min(a_id, b_id), max(a_id, b_id)) in semantic_pairs
                    G.add_edge(
                        a_id, b_id,
                        weight=dist,
                        confidence=0.9 if is_semantic else 0.3,
                    )
            self._nx_graph = G

        return self._adj

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def has_semantic_edges(self) -> bool:
        """True if the graph was built with IFC relationship data."""
        return self._has_semantic_edges

    def connected_components(self, elements: List[Element]) -> List[Set[str]]:
        """Return list of connected component sets (element ids)."""
        self.build_graph(elements)

        if _HAS_NX and self._nx_graph is not None:
            return [set(c) for c in nx.connected_components(self._nx_graph)]

        # Pure-Python DFS fallback
        adj = self._adj
        visited: Set[str] = set()
        components: List[Set[str]] = []
        for node in adj:
            if node in visited:
                continue
            stack = [node]
            comp: Set[str] = set()
            visited.add(node)
            while stack:
                cur = stack.pop()
                comp.add(cur)
                for nb in adj[cur]:
                    if nb not in visited:
                        visited.add(nb)
                        stack.append(nb)
            components.append(comp)
        return components

    def shortest_path(
        self,
        elements: List[Element],
        src_id: str,
        dst_id: str,
    ) -> Optional[List[str]]:
        """Shortest path by hop count (backward-compatible BFS).

        Uses NetworkX when available (still hop-based via unweighted
        shortest path) for consistency with existing rule code that
        counts hops.
        """
        self.build_graph(elements)

        if _HAS_NX and self._nx_graph is not None:
            if src_id not in self._nx_graph or dst_id not in self._nx_graph:
                return None
            try:
                return nx.shortest_path(self._nx_graph, src_id, dst_id)
            except nx.NetworkXNoPath:
                return None

        # Pure-Python BFS fallback
        adj = self._adj
        if src_id not in adj or dst_id not in adj:
            return None
        q = deque([src_id])
        prev: Dict[str, Optional[str]] = {src_id: None}
        while q:
            cur = q.popleft()
            if cur == dst_id:
                path: List[str] = []
                while cur is not None:
                    path.append(cur)
                    cur = prev[cur]
                path.reverse()
                return path
            for nb in adj[cur]:
                if nb not in prev:
                    prev[nb] = cur
                    q.append(nb)
        return None

    def shortest_path_length(
        self,
        elements: List[Element],
        src_id: str,
        dst_id: str,
    ) -> Optional[float]:
        """Weighted shortest path length (Euclidean distance along route).

        Returns ``None`` if no path exists.  Falls back to hop_count * 3.0
        when NetworkX is unavailable.
        """
        self.build_graph(elements)

        if _HAS_NX and self._nx_graph is not None:
            if src_id not in self._nx_graph or dst_id not in self._nx_graph:
                return None
            try:
                return nx.shortest_path_length(
                    self._nx_graph, src_id, dst_id, weight="weight",
                )
            except nx.NetworkXNoPath:
                return None

        # Fallback: BFS path * default hop distance
        path = self.shortest_path(elements, src_id, dst_id)
        if path is None:
            return None
        return (len(path) - 1) * 3.0

    def edge_confidence(self, a_id: str, b_id: str) -> float:
        """Return confidence of the edge between two nodes, or 0.0."""
        if _HAS_NX and self._nx_graph is not None:
            try:
                return self._nx_graph[a_id][b_id].get("confidence", 0.0)
            except KeyError:
                return 0.0
        # Pure-Python fallback: check adjacency list
        if self._adj is not None and a_id in self._adj and b_id in self._adj.get(a_id, set()):
            return 0.3  # AABB-only edge, low confidence
        return 0.0
