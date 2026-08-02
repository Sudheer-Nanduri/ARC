# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""Extension package protocol for ARC.

Enables third-party check type packages to register themselves via Python
entry points. A package like ``arc-fire-checks`` can declare::

    [project.entry-points."arc.check_types"]
    fire_spread = "arc_fire_checks:register"

Then, when ARC loads, calling ``discover_extensions()`` will find and
invoke all registered entry points, which should call
``register_check_type()`` to add their check types to the engine.

Usage::

    from arc.extensions import discover_extensions
    discover_extensions()   # call once at startup
"""
from __future__ import annotations

import logging
from typing import List

log = logging.getLogger(__name__)

_DISCOVERED = False


def discover_extensions() -> List[str]:
    """Discover and load ARC check type extensions via entry points.

    Returns list of successfully loaded extension names.
    Safe to call multiple times — only loads once.
    """
    global _DISCOVERED
    if _DISCOVERED:
        return []
    _DISCOVERED = True

    loaded: List[str] = []
    try:
        from importlib.metadata import entry_points
    except ImportError:
        log.debug("importlib.metadata not available; skipping extension discovery")
        return loaded

    try:
        # Python 3.12+ and 3.10+ with different APIs
        eps = entry_points()
        if hasattr(eps, "select"):
            arc_eps = eps.select(group="arc.check_types")
        elif isinstance(eps, dict):
            arc_eps = eps.get("arc.check_types", [])
        else:
            arc_eps = [ep for ep in eps if ep.group == "arc.check_types"]
    except Exception as exc:
        log.debug("Entry point discovery failed: %s", exc)
        return loaded

    for ep in arc_eps:
        try:
            register_fn = ep.load()
            register_fn()
            loaded.append(ep.name)
            log.info("Loaded ARC extension: %s", ep.name)
        except Exception as exc:
            log.warning("Failed to load extension '%s': %s", ep.name, exc)

    return loaded


def list_check_types() -> List[str]:
    """Return all registered check type names."""
    from .rule_engine import _CHECK_REGISTRY
    return sorted(_CHECK_REGISTRY.keys())
