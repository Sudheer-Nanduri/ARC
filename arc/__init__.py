# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: GPL-3.0-or-later

"""ARC — Assessable Regulatory Compliance for Building Models.

Top-level package. Provides:
- arc.core: Pure-Python compliance evidence engine (no Blender dependency)
- arc.spatial: Blender 4.2+ extension for visualization and IFC integration

When loaded as a Blender extension, register()/unregister() delegate to arc.spatial.
"""
from __future__ import annotations

__version__ = "0.1.0"

# Legacy add-on metadata. Blender 4.2+ installs ARC as an extension and reads
# blender_manifest.toml instead; bl_info is retained so the package is still
# introspectable by older tooling. Keep the two in sync.
bl_info = {
    "name": "Spatial ARC",
    "author": "Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi",
    "version": (0, 1, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Spatial ARC",
    "description": "Spatial compliance evidence engine for IFC models",
    "category": "3D View",
    "support": "COMMUNITY",
}


def register():
    """Blender extension entry point — delegates to arc.spatial."""
    try:
        from .spatial import register as _register
        _register()
    except ImportError:
        pass


def unregister():
    """Blender extension teardown — delegates to arc.spatial."""
    try:
        from .spatial import unregister as _unregister
        _unregister()
    except ImportError:
        pass
