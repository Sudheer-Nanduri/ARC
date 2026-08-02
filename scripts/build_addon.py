# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: GPL-3.0-or-later

"""Build script: package Spatial ARC for Blender.

Blender changed its add-on packaging in 4.2, so this script emits two layouts
from the same source tree.

Extension zip (Blender 4.2+, the default). Blender extracts the archive into
its own directory and reads blender_manifest.toml, so the package contents sit
at the root of the zip:

  __init__.py             <- arc/__init__.py (has register/unregister)
  blender_manifest.toml   <- at the root, sibling to __init__.py
  core/                   <- engine, rules, reports
  spatial/                <- Blender operators, UI, visualization
  wheelhouse/             <- bundled .whl files, declared in the manifest

Legacy add-on zip (Blender 4.1 and older, --legacy). The old installer reads
bl_info and expects the importable package folder itself:

  arc/
    __init__.py
    core/
    spatial/
    wheelhouse/

The legacy installer does not process the manifest, so wheels are not installed
automatically; arc.spatial.install.ensure_deps() unpacks the bundled wheelhouse
on first enable instead.

Usage:
  python scripts/build_addon.py                    -> arc-0.1.0.zip
  python scripts/build_addon.py --legacy           -> arc-0.1.0-legacy.zip
  python scripts/build_addon.py --all              -> both
  python scripts/build_addon.py --download-wheels  -> refresh arc/wheelhouse/
"""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARC_PKG = ROOT / "arc"
MANIFEST = ROOT / "blender_manifest.toml"
OUT_ZIP = ROOT / "arc-0.1.0.zip"
OUT_ZIP_LEGACY = ROOT / "arc-0.1.0-legacy.zip"

# Dirs / suffixes to exclude
EXCLUDE_DIRS = {"__pycache__", "libs", ".mypy_cache"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


def _should_exclude(rel_path: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in rel_path.parts):
        return True
    if rel_path.suffix in EXCLUDE_SUFFIXES:
        return True
    return False


def _package_files():
    """Yield (source path, path relative to the arc package) for every shipped file."""
    for src in sorted(ARC_PKG.rglob("*")):
        if src.is_dir():
            continue
        rel = src.relative_to(ARC_PKG)
        if _should_exclude(rel):
            continue
        yield src, rel


def build_extension_zip(out_zip: Path = OUT_ZIP) -> Path:
    """Build a Blender 4.2+ extension zip.

    The package contents sit at the root of the zip alongside the manifest;
    Blender's extension system extracts it as the `arc` package (id = "arc").
    """
    if out_zip.exists():
        out_zip.unlink()

    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        # 1. blender_manifest.toml at the root of the zip
        zf.write(MANIFEST, "blender_manifest.toml")

        # 2. All files from arc/ - preserving directory structure
        for src, rel in _package_files():
            zf.write(src, str(rel).replace("\\", "/"))

    return out_zip


def build_legacy_addon_zip(out_zip: Path = OUT_ZIP_LEGACY) -> Path:
    """Build a legacy add-on zip for Blender 4.1 and older.

    Everything is nested under an ``arc/`` directory, which is what the
    pre-4.2 "Install from File" flow expects. The manifest is carried along
    harmlessly so a single archive stays self-describing.
    """
    if out_zip.exists():
        out_zip.unlink()

    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.write(MANIFEST, "arc/blender_manifest.toml")
        for src, rel in _package_files():
            zf.write(src, "arc/" + str(rel).replace("\\", "/"))

    return out_zip


def build_wheelhouse(req_file: str = "requirements.txt", out_dir: str = "arc/wheelhouse"):
    """Download wheels listed in requirements.txt into the wheelhouse directory.

    Only pure-Python wheels are fetched. Blender ships a different Python with
    each release (4.5 uses 3.11, 5.1 uses 3.13), so a platform- or
    version-specific wheel here would only work on one of them.
    """
    req = ROOT / req_file
    out = ROOT / out_dir
    out.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "pip", "download", "-r", str(req), "-d", str(out),
           "--only-binary", ":all:"]
    subprocess.check_call(cmd)


def _report(result: Path, root_prefix: str = "") -> None:
    with zipfile.ZipFile(result) as zf:
        names = zf.namelist()

    print(f"\nCreated: {result}")
    print(f"Files: {len(names)}")

    print("\nStructure:")
    top = sorted({n[len(root_prefix):].split("/")[0] for n in names if n.startswith(root_prefix)})
    for d in top:
        full = root_prefix + d
        count = sum(1 for n in names if n.startswith(full + "/"))
        print(f"  {d}/ ({count} files)" if count else f"  {d}")

    print("\nWheels:")
    for n in sorted(n for n in names if n.endswith(".whl")):
        print(f"  {n}")


if __name__ == "__main__":
    if "--download-wheels" in sys.argv:
        print("Downloading wheels ...")
        build_wheelhouse()
        print("Done.")

    want_legacy = "--legacy" in sys.argv or "--all" in sys.argv
    want_extension = "--legacy" not in sys.argv or "--all" in sys.argv

    if want_extension:
        print(f"Building extension zip (Blender 4.2+) -> {OUT_ZIP.name} ...")
        _report(build_extension_zip())

    if want_legacy:
        print(f"\nBuilding legacy add-on zip (Blender <= 4.1) -> {OUT_ZIP_LEGACY.name} ...")
        _report(build_legacy_addon_zip(), root_prefix="arc/")
