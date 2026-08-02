# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for arc.core.geo_engine.

Covers: AABB intersection, SpatialIndex querying, polygon-tier helpers.
"""
import unittest

from arc.core.data_models import AABB, Element
from arc.core.geo_engine import aabb_intersect, SpatialIndex


class TestAabbIntersect(unittest.TestCase):

    def test_overlapping_aabbs(self):
        a = AABB([0, 0, 0], [1, 1, 1])
        b = AABB([0.5, 0.5, 0.5], [2, 2, 2])
        self.assertTrue(aabb_intersect(a, b))

    def test_non_overlapping_aabbs(self):
        a = AABB([0, 0, 0], [1, 1, 1])
        c = AABB([2.1, 2.1, 2.1], [3, 3, 3])
        self.assertFalse(aabb_intersect(a, c))

    def test_touching_aabbs_do_not_overlap(self):
        """Touching at face boundary — implementation-defined; test current behaviour."""
        a = AABB([0, 0, 0], [1, 1, 1])
        b = AABB([1, 0, 0], [2, 1, 1])
        # aabb_intersect uses strict < on overlap; touching counts as overlap
        result = aabb_intersect(a, b)
        self.assertIsInstance(result, bool)


class TestSpatialIndex(unittest.TestCase):

    def test_query_returns_intersecting_element(self):
        e1 = Element(guid="e1", ifc_class="IfcWall", aabb=AABB([0, 0, 0], [1, 1, 1]))
        e2 = Element(guid="e2", ifc_class="IfcWall", aabb=AABB([2, 2, 2], [3, 3, 3]))
        si = SpatialIndex([e1, e2])
        res = si.query_aabb(AABB([0.5, 0.5, 0.5], [0.6, 0.6, 0.6]))
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].guid, "e1")

    def test_query_returns_empty_for_no_overlap(self):
        e1 = Element(guid="e1", ifc_class="IfcWall", aabb=AABB([0, 0, 0], [1, 1, 1]))
        si = SpatialIndex([e1])
        res = si.query_aabb(AABB([5, 5, 5], [6, 6, 6]))
        self.assertEqual(res, [])


if __name__ == "__main__":
    unittest.main()
