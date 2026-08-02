# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""Local rule pack registry — searchable index of available rule packs.

Scans a rule-pack tree and its root directory for
pack_manifest.json files. Provides CLI-friendly listing and filtering
by jurisdiction, domain, or building type.

Usage::

    from arc.registry import RuleRegistry
    reg = RuleRegistry.scan("arc/core/rules")
    for pack in reg.search(jurisdiction="India"):
        print(pack.pack_id, pack.description)
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from .data_models import RulePackManifest
from .rule_loader import load_rule_pack_manifest


class RuleRegistry:
    """In-memory index of discovered rule packs."""

    def __init__(self):
        self.packs: List[RulePackManifest] = []

    @classmethod
    def scan(cls, rules_root: str) -> "RuleRegistry":
        """Scan a rules directory tree for pack manifests."""
        registry = cls()
        root = Path(rules_root)

        # Check for pack_manifest.json in root
        root_manifest = root / "pack_manifest.json"
        if root_manifest.exists():
            manifest = load_rule_pack_manifest(str(root_manifest))
            if manifest:
                registry.packs.append(manifest)

        # Scan packs/ subdirectory
        packs_dir = root / "packs"
        if packs_dir.exists() and packs_dir.is_dir():
            for pack_dir in sorted(packs_dir.iterdir()):
                if pack_dir.is_dir():
                    manifest_path = pack_dir / "pack_manifest.json"
                    if manifest_path.exists():
                        manifest = load_rule_pack_manifest(str(manifest_path))
                        if manifest:
                            registry.packs.append(manifest)

        return registry

    def search(
        self,
        jurisdiction: Optional[str] = None,
        governance_status: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> List[RulePackManifest]:
        """Filter packs by criteria."""
        results = list(self.packs)
        if jurisdiction:
            j_lower = jurisdiction.lower()
            results = [p for p in results if j_lower in p.jurisdiction.lower()]
        if governance_status:
            results = [p for p in results if p.governance_status == governance_status]
        if keyword:
            kw = keyword.lower()
            results = [
                p for p in results
                if kw in p.description.lower()
                or kw in p.pack_id.lower()
                or kw in p.jurisdiction.lower()
            ]
        return results

    def list_packs(self) -> List[dict]:
        """Return a summary list suitable for CLI display."""
        return [
            {
                "pack_id": p.pack_id,
                "version": p.version,
                "jurisdiction": p.jurisdiction,
                "status": p.governance_status,
                "rules": len(p.rule_ids),
                "description": p.description,
            }
            for p in self.packs
        ]
