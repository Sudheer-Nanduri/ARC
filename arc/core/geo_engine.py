# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""Spatial engine with KD-tree acceleration.

All coordinates are in WORLD SPACE / METERS (API Contract §0.1).
Public functions never raise exceptions — they return None or empty lists
on failure (API Contract §0.2).

Tier ordering (Performance Guardrails §4):
  Level 1  AABB   — always first, very cheap
  Level 2  KD-tree radius — O(log n) proximity
  Level 3  Mesh   — deferred to Blender (not implemented here)
"""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

from .data_models import AABB, Element


# ---------------------------------------------------------------------------
# AABB helpers
# ---------------------------------------------------------------------------

def aabb_intersect(a: AABB, b: AABB) -> bool:
    """Level-1 AABB overlap test."""
    for i in range(3):
        if a.max[i] < b.min[i] or b.max[i] < a.min[i]:
            return False
    return True


def aabb_center(aabb: AABB) -> Tuple[float, float, float]:
    return (
        (aabb.min[0] + aabb.max[0]) * 0.5,
        (aabb.min[1] + aabb.max[1]) * 0.5,
        (aabb.min[2] + aabb.max[2]) * 0.5,
    )


def aabb_dimensions(aabb: AABB) -> Tuple[float, float, float]:
    """Return (dx, dy, dz) sizes."""
    return (
        aabb.max[0] - aabb.min[0],
        aabb.max[1] - aabb.min[1],
        aabb.max[2] - aabb.min[2],
    )


def aabb_floor_area(aabb: AABB) -> float:
    """Horizontal footprint area (x × y)."""
    dx = aabb.max[0] - aabb.min[0]
    dy = aabb.max[1] - aabb.min[1]
    return dx * dy


def aabb_min_horizontal_dim(aabb: AABB) -> float:
    """Minimum horizontal (x or y) dimension — used for width checks."""
    return min(aabb.max[0] - aabb.min[0], aabb.max[1] - aabb.min[1])


def aabb_max_horizontal_dim(aabb: AABB) -> float:
    return max(aabb.max[0] - aabb.min[0], aabb.max[1] - aabb.min[1])


def aabb_height(aabb: AABB) -> float:
    """Vertical (z) dimension."""
    return aabb.max[2] - aabb.min[2]


# ---------------------------------------------------------------------------
# Pure-Python KD-tree (Blender's mathutils.KDTree used when available)
# ---------------------------------------------------------------------------

class _KDNode:
    __slots__ = ("point", "element", "left", "right")

    def __init__(self, point: Tuple[float, float, float], element: Element):
        self.point = point
        self.element = element
        self.left: Optional[_KDNode] = None
        self.right: Optional[_KDNode] = None


def _build(items: list, depth: int = 0) -> Optional[_KDNode]:
    if not items:
        return None
    axis = depth % 3
    items.sort(key=lambda x: x[0][axis])
    mid = len(items) // 2
    node = _KDNode(items[mid][0], items[mid][1])
    node.left = _build(items[:mid], depth + 1)
    node.right = _build(items[mid + 1:], depth + 1)
    return node


def _radius_search(
    node: Optional[_KDNode],
    point: Tuple[float, float, float],
    radius: float,
    results: list,
    depth: int = 0,
) -> None:
    if node is None:
        return
    dx = point[0] - node.point[0]
    dy = point[1] - node.point[1]
    dz = point[2] - node.point[2]
    dist = math.sqrt(dx * dx + dy * dy + dz * dz)
    if dist <= radius:
        results.append((dist, node.element))
    axis = depth % 3
    diff = point[axis] - node.point[axis]
    near, far = (node.left, node.right) if diff <= 0 else (node.right, node.left)
    _radius_search(near, point, radius, results, depth + 1)
    if abs(diff) <= radius:
        _radius_search(far, point, radius, results, depth + 1)


# ---------------------------------------------------------------------------
# SpatialIndex
# ---------------------------------------------------------------------------

class SpatialIndex:
    """O(n log n) build, O(log n) radius query.

    Uses Blender's mathutils.KDTree when inside Blender for maximum
    performance; falls back to the pure-Python tree in headless mode.
    """

    def __init__(self, elements: Optional[List[Element]] = None):
        self.elements: List[Element] = []
        self._root: Optional[_KDNode] = None
        self._blender_kd = None  # mathutils.KDTree if available
        if elements:
            self.build(elements)

    def build(self, elements: List[Element]) -> None:
        self.elements = list(elements)
        self._root = None
        self._blender_kd = None

        items = [
            (aabb_center(e.aabb), e)
            for e in self.elements
            if e.aabb is not None
        ]
        if not items:
            return

        # Prefer Blender's native KD-tree (C speed)
        try:
            from mathutils import kdtree as _mathkd  # type: ignore
            kd = _mathkd.KDTree(len(items))
            for idx, (pt, _) in enumerate(items):
                kd.insert(pt, idx)
            kd.balance()
            self._blender_kd = (kd, [e for _, e in items])
        except Exception:
            # Pure-Python fallback
            self._root = _build(list(items))

    def query_aabb(self, aabb: AABB) -> List[Element]:
        """Level-1 AABB overlap filter — no KD-tree needed."""
        return [
            e for e in self.elements
            if e.aabb is not None and aabb_intersect(e.aabb, aabb)
        ]

    def get_nearby_elements(self, element: Element, radius: float) -> List[Element]:
        """Return elements whose AABB center lies within *radius* meters.

        O(log n) — never brute-force (Performance Guardrails §3.1).
        """
        if element.aabb is None:
            return []
        center = aabb_center(element.aabb)
        guid = element.guid

        if self._blender_kd is not None:
            kd, indexed = self._blender_kd
            return [
                indexed[idx]
                for _, idx, dist in kd.find_range(center, radius)
                if indexed[idx].guid != guid
            ]

        # Pure-Python path
        results: list = []
        _radius_search(self._root, center, radius, results)
        return [e for _, e in results if e.guid != guid]

    def nearest_elements(self, element: Element, k: int = 5) -> List[Element]:
        """Return the k nearest elements by center distance."""
        if element.aabb is None:
            return []
        center = aabb_center(element.aabb)
        guid = element.guid

        if self._blender_kd is not None:
            kd, indexed = self._blender_kd
            found = kd.find_n(center, k + 1)
            return [indexed[idx] for _, idx, _ in found if indexed[idx].guid != guid][:k]

        # Fallback: linear scan (only hit on very small models or missing tree)
        dists = []
        for e in self.elements:
            if e.aabb is None or e.guid == guid:
                continue
            c = aabb_center(e.aabb)
            d = math.sqrt(sum((center[i] - c[i]) ** 2 for i in range(3)))
            dists.append((d, e))
        dists.sort(key=lambda x: x[0])
        return [e for _, e in dists[:k]]


# ---------------------------------------------------------------------------
# Distance helpers
# ---------------------------------------------------------------------------

def distance_between(a: Element, b: Element) -> float:
    """World-space center-to-center distance. Returns inf on missing AABB."""
    if a.aabb is None or b.aabb is None:
        return float("inf")
    ca = aabb_center(a.aabb)
    cb = aabb_center(b.aabb)
    return math.sqrt(sum((ca[i] - cb[i]) ** 2 for i in range(3)))


# ---------------------------------------------------------------------------
# Shapely polygon geometry tier
# ---------------------------------------------------------------------------

def _try_shapely():
    """Import shapely if available, return None otherwise."""
    try:
        from shapely.geometry import Polygon, box as shapely_box
        return Polygon, shapely_box
    except ImportError:
        return None, None


def polygon_area(element: Element) -> Optional[float]:
    """Compute 2D polygon area using Shapely if available, else None."""
    Polygon, _ = _try_shapely()
    if Polygon is None:
        return None
    footprint = element.properties.get("footprint_polygon")
    if not footprint or not isinstance(footprint, (list, tuple)):
        return None
    try:
        poly = Polygon(footprint)
        return poly.area if poly.is_valid else None
    except Exception:
        return None


def polygon_min_width(element: Element) -> Optional[float]:
    """Estimate minimum width from 2D polygon using Shapely's minimum rotated rectangle."""
    Polygon, _ = _try_shapely()
    if Polygon is None:
        return None
    footprint = element.properties.get("footprint_polygon")
    if not footprint or not isinstance(footprint, (list, tuple)):
        return None
    try:
        poly = Polygon(footprint)
        if not poly.is_valid:
            return None
        mrr = poly.minimum_rotated_rectangle
        coords = list(mrr.exterior.coords)
        if len(coords) < 4:
            return None
        # Minimum rotated rectangle edges
        d1 = math.sqrt((coords[1][0] - coords[0][0]) ** 2 + (coords[1][1] - coords[0][1]) ** 2)
        d2 = math.sqrt((coords[2][0] - coords[1][0]) ** 2 + (coords[2][1] - coords[1][1]) ** 2)
        return min(d1, d2)
    except Exception:
        return None


def polygon_from_aabb(aabb: AABB):
    """Create a Shapely polygon from an AABB footprint. Returns None if Shapely unavailable."""
    _, shapely_box = _try_shapely()
    if shapely_box is None:
        return None
    try:
        return shapely_box(aabb.min[0], aabb.min[1], aabb.max[0], aabb.max[1])
    except Exception:
        return None


def walkable_clear_width(
    lobby_aabb: AABB,
    obstruction_aabbs: List[AABB],
) -> dict:
    """Polygon-tier corridor clearance measurement.

    Subtracts the footprints of ``obstruction_aabbs`` from the ``lobby_aabb``
    footprint and reports the largest inscribed-circle diameter on the
    resulting walkable polygon. Designed for the corridor_width rule.

    Returns a dict with keys:
        ok                    : bool — True iff a usable measurement was produced
        clear_width_m         : float — 2 × largest inscribed-circle radius
        walkable_area_m2      : float
        lobby_area_m2         : float
        walkable_area_ratio   : float
        walkable_parts        : int  — 1 for a single connected polygon, N for multipart, 0 for empty
        aabb_aspect_ratio     : float — max(dx, dy) / min(dx, dy) of the lobby AABB
        method                : str  — "polygon_inscribed_circle" / "aabb_min_dim" / "unavailable"
        tier                  : str  — "polygon" / "aabb" / "unknown"
        reason                : str  — populated when ok=False
    """
    Polygon, shapely_box = _try_shapely()
    dx = lobby_aabb.max[0] - lobby_aabb.min[0]
    dy = lobby_aabb.max[1] - lobby_aabb.min[1]
    min_dim = min(dx, dy)
    aspect = max(dx, dy) / min_dim if min_dim > 0 else float("inf")
    lobby_area = dx * dy

    if shapely_box is None:
        return {
            "ok": False,
            "clear_width_m": min_dim,
            "walkable_area_m2": lobby_area,
            "lobby_area_m2": lobby_area,
            "walkable_area_ratio": 1.0,
            "walkable_parts": 1,
            "aabb_aspect_ratio": aspect,
            "method": "aabb_min_dim",
            "tier": "aabb",
            "reason": "shapely_unavailable",
        }

    try:
        from shapely.ops import unary_union
        lobby_poly = shapely_box(
            lobby_aabb.min[0], lobby_aabb.min[1],
            lobby_aabb.max[0], lobby_aabb.max[1],
        )
        obstruction_polys = [
            shapely_box(a.min[0], a.min[1], a.max[0], a.max[1])
            for a in obstruction_aabbs
        ]
        if obstruction_polys:
            walkable = lobby_poly.difference(unary_union(obstruction_polys))
        else:
            walkable = lobby_poly
    except Exception as exc:
        return {
            "ok": False,
            "clear_width_m": min_dim,
            "walkable_area_m2": lobby_area,
            "lobby_area_m2": lobby_area,
            "walkable_area_ratio": 1.0,
            "walkable_parts": 1,
            "aabb_aspect_ratio": aspect,
            "method": "aabb_min_dim",
            "tier": "aabb",
            "reason": f"shapely_error:{exc}",
        }

    walkable_area = walkable.area if not walkable.is_empty else 0.0
    walkable_ratio = walkable_area / lobby_area if lobby_area > 0 else 0.0
    geom_type = getattr(walkable, "geom_type", "")
    if walkable.is_empty:
        n_parts = 0
        primary = None
    elif geom_type == "MultiPolygon":
        n_parts = len(walkable.geoms)
        primary = max(walkable.geoms, key=lambda p: p.area)
    else:
        n_parts = 1
        primary = walkable

    if primary is None:
        return {
            "ok": False,
            "clear_width_m": 0.0,
            "walkable_area_m2": 0.0,
            "lobby_area_m2": lobby_area,
            "walkable_area_ratio": 0.0,
            "walkable_parts": 0,
            "aabb_aspect_ratio": aspect,
            "method": "polygon_inscribed_circle",
            "tier": "polygon",
            "reason": "walkable_empty",
        }

    # Inscribed-circle radius via shapely 2.x maximum_inscribed_circle if
    # available, else negative-buffer search.
    radius = 0.0
    try:
        from shapely.ops import maximum_inscribed_circle
        seg = maximum_inscribed_circle(primary, tolerance=0.01)
        radius = float(seg.length)
    except Exception:
        bx = primary.bounds
        hi = max(bx[2] - bx[0], bx[3] - bx[1]) / 2.0
        lo = 0.0
        for _ in range(20):
            mid = (lo + hi) / 2.0
            try:
                if not primary.buffer(-mid).is_empty:
                    lo = mid
                else:
                    hi = mid
            except Exception:
                break
        radius = lo

    return {
        "ok": True,
        "clear_width_m": 2.0 * radius,
        "walkable_area_m2": walkable_area,
        "lobby_area_m2": lobby_area,
        "walkable_area_ratio": walkable_ratio,
        "walkable_parts": n_parts,
        "aabb_aspect_ratio": aspect,
        "method": "polygon_inscribed_circle",
        "tier": "polygon",
        "reason": "",
    }


def distance_point_to_element(point: Tuple[float, float, float], element: Element) -> float:
    """Approximate distance from a point to nearest AABB face."""
    if element.aabb is None:
        return float("inf")
    # Clamp point to AABB, measure distance from clamped point
    clamped = tuple(
        max(element.aabb.min[i], min(element.aabb.max[i], point[i]))
        for i in range(3)
    )
    return math.sqrt(sum((point[i] - clamped[i]) ** 2 for i in range(3)))
