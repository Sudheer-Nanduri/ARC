# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""External data validators for ARC.

Provides structural validation for GeoJSON, JSON external data sources,
and rule schema validation. Produces structured warnings rather than
raising exceptions, so the engine can proceed with partial data.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def validate_geojson(data: Any) -> List[str]:
    """Validate basic GeoJSON structure. Returns list of warning strings."""
    warnings: List[str] = []
    if not isinstance(data, dict):
        warnings.append("GeoJSON data must be a dict/object")
        return warnings

    geo_type = data.get("type")
    if geo_type not in (
        "Point", "MultiPoint", "LineString", "MultiLineString",
        "Polygon", "MultiPolygon", "GeometryCollection",
        "Feature", "FeatureCollection",
    ):
        warnings.append(f"Unknown GeoJSON type: {geo_type}")

    if geo_type == "FeatureCollection":
        features = data.get("features")
        if not isinstance(features, list):
            warnings.append("FeatureCollection missing 'features' array")
        else:
            for i, feat in enumerate(features):
                if not isinstance(feat, dict):
                    warnings.append(f"Feature [{i}] is not an object")
                elif feat.get("type") != "Feature":
                    warnings.append(f"Feature [{i}] has type '{feat.get('type')}' instead of 'Feature'")
                elif not isinstance(feat.get("geometry"), dict):
                    warnings.append(f"Feature [{i}] missing 'geometry' object")

    elif geo_type == "Feature":
        if not isinstance(data.get("geometry"), dict):
            warnings.append("Feature missing 'geometry' object")

    elif geo_type in ("Point", "LineString", "Polygon", "MultiPoint", "MultiLineString", "MultiPolygon"):
        coords = data.get("coordinates")
        if coords is None:
            warnings.append(f"{geo_type} missing 'coordinates'")
        elif not isinstance(coords, list):
            warnings.append(f"{geo_type} 'coordinates' must be an array")

    return warnings


def validate_external_json(data: Any, required_keys: Optional[List[str]] = None) -> List[str]:
    """Validate external JSON data has required keys. Returns warnings."""
    warnings: List[str] = []
    if not isinstance(data, dict):
        warnings.append("External data must be a dict/object")
        return warnings
    for key in (required_keys or []):
        if key not in data:
            warnings.append(f"Missing required key: '{key}'")
    return warnings


def validate_rule_schema(rule: Dict[str, Any]) -> List[str]:
    """Validate a rule dict has required fields. Returns warnings."""
    warnings: List[str] = []
    if not rule.get("id"):
        warnings.append("Rule missing 'id' field")
    if not rule.get("check_type") and rule.get("language") != "python":
        warnings.append(f"Rule '{rule.get('id', '?')}' missing 'check_type'")
    if not rule.get("selector"):
        warnings.append(f"Rule '{rule.get('id', '?')}' missing 'selector'")
    return warnings
