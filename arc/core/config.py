# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""Configuration handling for ARC."""
import json
from typing import Any, Dict


class Config:
    def __init__(self, project_name: str = "arc_project", rules_path: str = "arc/core/rules", output_path: str = "results"):
        self.project_name = project_name
        self.rules_path = rules_path
        self.output_path = output_path
        self._frozen = False

    @classmethod
    def load(cls, path: str) -> "Config":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.merge(cls(), data)

    @classmethod
    def merge(cls, base: "Config", override: Dict[str, Any]) -> "Config":
        cfg = cls(
            project_name=override.get("project_name", base.project_name),
            rules_path=override.get("rules_path", base.rules_path),
            output_path=override.get("output_path", base.output_path),
        )
        return cfg

    def freeze(self) -> None:
        self._frozen = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_name": self.project_name,
            "rules_path": self.rules_path,
            "output_path": self.output_path,
        }

    def __setattr__(self, name, value):
        if getattr(self, "_frozen", False) and name != "_frozen":
            raise AttributeError("Config is frozen; cannot modify attributes")
        super().__setattr__(name, value)

    def __repr__(self):
        return f"Config(project_name={self.project_name!r}, rules_path={self.rules_path!r}, output_path={self.output_path!r})"


# ---------------------------------------------------------------------------
# Cross-Discipline Tolerances
# ---------------------------------------------------------------------------

# Default tolerances (meters) by discipline pairing.
# Keys are frozensets of two discipline strings.
DISCIPLINE_TOLERANCES: Dict[frozenset, float] = {
    frozenset({"architectural", "structural"}): 0.05,   # 50 mm
    frozenset({"architectural", "mechanical"}): 0.025,   # 25 mm
    frozenset({"structural", "mechanical"}): 0.025,
    frozenset({"architectural", "electrical"}): 0.01,    # 10 mm
}

DEFAULT_TOLERANCE: float = 0.01  # 10 mm fallback


def get_tolerance(discipline_a: str, discipline_b: str) -> float:
    """Get tolerance for a discipline pairing."""
    key = frozenset({discipline_a.lower(), discipline_b.lower()})
    return DISCIPLINE_TOLERANCES.get(key, DEFAULT_TOLERANCE)
