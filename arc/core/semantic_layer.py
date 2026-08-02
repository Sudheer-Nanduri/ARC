# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""Semantic layer: lightweight wrappers for element properties and IFC parsing hooks."""
from typing import Any, Dict, List
from .data_models import Element


class SemanticLayer:
    """Provides property access and simple tagging for Elements.

    Wraps IfcOpenShell/Bonsai calls through `ifc_integration`.
    Can also accept an explicit parser with a `parse(path)` method.
    """

    def __init__(self, parser: Any = None):
        self.parser = parser

    def get_properties(self, element: Element) -> Dict[str, Any]:
        return getattr(element, "properties", {}) or {}

    def set_property(self, element: Element, key: str, value: Any) -> None:
        if getattr(element, "properties", None) is None:
            element.properties = {}
        element.properties[key] = value

    def tag_confidence(self, element: Element, confidence: float) -> None:
        self.set_property(element, "confidence", float(confidence))

    def load_ifc(self, path: str) -> List[Element]:
        """Load IFC elements from a file path.

        If a custom parser with a `parse(path)` method was provided at
        construction, it will be used. Otherwise delegates to
        `arc.ifc_integration.load_ifc` which uses IfcOpenShell.
        """
        if self.parser is not None:
            return self.parser.parse(path)
        from .ifc_integration import load_ifc
        return load_ifc(path)
