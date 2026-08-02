# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for arc.core.topology_engine.

Covers: AABB-fallback edges, semantic edge preference, wall exclusion,
path lengths, edge confidence, and missing-node handling.
"""
import unittest

from arc.core.data_models import AABB, Element
from arc.core.topology_engine import TopologyEngine


class TestTopologyEngine(unittest.TestCase):

    def test_aabb_fallback_components_and_path(self):
        """Overlapping spaces connect via AABB; isolated space stays separate."""
        e1 = Element(guid="e1", ifc_class="IfcSpace", aabb=AABB([0, 0, 0], [1, 1, 1]))
        e2 = Element(guid="e2", ifc_class="IfcSpace", aabb=AABB([0.5, 0.5, 0.5], [1.5, 1.5, 1.5]))
        e3 = Element(guid="e3", ifc_class="IfcSpace", aabb=AABB([10, 10, 10], [11, 11, 11]))
        te = TopologyEngine()
        comps = te.connected_components([e1, e2, e3])
        comps_sets = [set(c) for c in comps]
        self.assertIn({"e1", "e2"}, comps_sets)
        self.assertIn({"e3"}, comps_sets)
        self.assertEqual(te.shortest_path([e1, e2, e3], "e1", "e2"), ["e1", "e2"])
        self.assertIsNone(te.shortest_path([e1, e2, e3], "e1", "e3"))

    def test_semantic_edges_preferred(self):
        """IFC relationship metadata creates edges even without AABB proximity."""
        s1 = Element(guid="s1", ifc_class="IfcSpace",
                     aabb=AABB([0, 0, 0], [1, 1, 1]),
                     metadata={"connected_to": ["door1"]})
        door = Element(guid="door1", ifc_class="IfcDoor",
                       aabb=AABB([1.5, 0, 0], [2, 0.2, 2.1]),
                       metadata={"connected_to": ["s1", "s2"]})
        s2 = Element(guid="s2", ifc_class="IfcSpace",
                     aabb=AABB([5, 0, 0], [6, 1, 1]),
                     metadata={"connected_to": ["door1"]})
        te = TopologyEngine()
        te.build_graph([s1, door, s2])
        self.assertTrue(te.has_semantic_edges)
        path = te.shortest_path([s1, door, s2], "s1", "s2")
        self.assertIsNotNone(path)
        self.assertIn("door1", path)

    def test_wall_does_not_create_aabb_edges(self):
        """IfcWall is not a spatial/connective class — it must not bridge two spaces."""
        s1 = Element(guid="s1", ifc_class="IfcSpace", aabb=AABB([0, 0, 0], [2, 3, 3]))
        wall = Element(guid="w1", ifc_class="IfcWall", aabb=AABB([2.2, 0, 0], [2.8, 3, 3]))
        s2 = Element(guid="s2", ifc_class="IfcSpace", aabb=AABB([3, 0, 0], [5, 3, 3]))
        te = TopologyEngine()
        te.build_graph([s1, wall, s2])
        self.assertIsNone(te.shortest_path([s1, wall, s2], "s1", "s2"))

    def test_shortest_path_length_positive(self):
        """Weighted path length between connected spaces is positive."""
        s1 = Element(guid="s1", ifc_class="IfcSpace",
                     aabb=AABB([0, 0, 0], [2, 2, 2]),
                     metadata={"connected_to": ["s2"]})
        s2 = Element(guid="s2", ifc_class="IfcSpace",
                     aabb=AABB([4, 0, 0], [6, 2, 2]),
                     metadata={"connected_to": ["s1"]})
        te = TopologyEngine()
        length = te.shortest_path_length([s1, s2], "s1", "s2")
        self.assertIsNotNone(length)
        self.assertGreater(length, 0)

    def test_edge_confidence_known_edge(self):
        """Edge confidence for a real edge is in [0, 1]."""
        s1 = Element(guid="s1", ifc_class="IfcSpace",
                     aabb=AABB([0, 0, 0], [1, 1, 1]),
                     metadata={"connected_to": ["s2"]})
        s2 = Element(guid="s2", ifc_class="IfcSpace",
                     aabb=AABB([0.5, 0, 0], [1.5, 1, 1]),
                     metadata={"connected_to": ["s1"]})
        te = TopologyEngine()
        te.build_graph([s1, s2])
        conf = te.edge_confidence("s1", "s2")
        self.assertGreaterEqual(conf, 0.0)
        self.assertLessEqual(conf, 1.0)

    def test_edge_confidence_missing_returns_zero(self):
        """Non-existent edge returns exactly 0.0."""
        s1 = Element(guid="s1", ifc_class="IfcSpace",
                     aabb=AABB([0, 0, 0], [1, 1, 1]),
                     metadata={"connected_to": ["s2"]})
        s2 = Element(guid="s2", ifc_class="IfcSpace",
                     aabb=AABB([0.5, 0, 0], [1.5, 1, 1]),
                     metadata={"connected_to": ["s1"]})
        te = TopologyEngine()
        te.build_graph([s1, s2])
        self.assertEqual(te.edge_confidence("s1", "missing"), 0.0)

    def test_missing_node_returns_none(self):
        """Path query with a non-existent destination returns None gracefully."""
        te = TopologyEngine()
        s1 = Element(guid="s1", ifc_class="IfcSpace", aabb=AABB([0, 0, 0], [1, 1, 1]))
        self.assertIsNone(te.shortest_path([s1], "s1", "missing"))


if __name__ == "__main__":
    unittest.main()
