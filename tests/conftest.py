# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""Shared pytest fixtures for both core and spatial test suites.

Import from your test file:
    from conftest import demo_elements, demo_context  # via pytest fixture injection
"""
import pytest

from arc.core.context import Context
from arc.core.data_models import AABB, Element


# ---------------------------------------------------------------------------
# Reusable demo model: 8 elements matching the built-in --demo context
# ---------------------------------------------------------------------------

@pytest.fixture
def demo_elements():
    """8-element demo model (same set used by arc-check --demo)."""
    return [
        Element(guid="p1",      ifc_class="IfcSpace",   aabb=AABB([0,0,0],[3.5,3.5,2.8]), properties={"area": 12.25, "SpaceType": "Parking"}),
        Element(guid="p2",      ifc_class="IfcSpace",   aabb=AABB([0,0,0],[2.0,2.0,2.8]), properties={"area": 4.0,   "SpaceType": "Parking"}),
        Element(guid="door1",   ifc_class="IfcDoor",    aabb=AABB([0,0,0],[1.0,0.2,2.1]), properties={"Width": 1.0}),
        Element(guid="stair1",  ifc_class="IfcStair",   aabb=AABB([0,0,0],[1.8,4.0,3.0]), properties={}),
        Element(guid="room1",   ifc_class="IfcSpace",   aabb=AABB([0,0,0],[4.0,3.5,2.8]), properties={"area": 14.0,  "SpaceType": "Habitable"}),
        Element(guid="kitchen1",ifc_class="IfcSpace",   aabb=AABB([0,0,0],[2.8,2.0,2.8]), properties={"area": 5.6,   "SpaceType": "Kitchen"}),
        Element(guid="bath1",   ifc_class="IfcSpace",   aabb=AABB([0,0,0],[1.5,1.8,2.4]), properties={"area": 2.7,   "SpaceType": "Bathroom"}),
        Element(guid="railing1",ifc_class="IfcRailing", aabb=AABB([0,0,0],[3.0,0.1,1.1]), properties={}),
    ]


@pytest.fixture
def demo_context(demo_elements):
    """Context wrapping the 8-element demo model."""
    return Context(demo_elements)
