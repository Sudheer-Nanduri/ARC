# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: GPL-3.0-or-later

"""Dependency installer helper for ARC add-on.

Provides `ensure_deps()` to install bundled wheels from `arc/wheelhouse/`
into a local `arc/libs/` folder and add it to `sys.path`. This approach keeps
Blender's site-packages untouched and avoids requiring admin rights.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Tuple
import shutil
from typing import Optional


def ensure_deps(wheel_dir: str | Path | None = None, libs_dir: str | Path | None = None) -> Tuple[bool, str]:
    """Ensure dependencies are installed into the add-on local libs folder.

    Returns (success: bool, message: str).
    The function will install bundled wheels from `wheel_dir` into `libs_dir`.
    If the target packages are already present inside `libs_dir` the function
    returns early.
    """
    addon_dir = Path(__file__).parent
    wheel_dir = Path(wheel_dir) if wheel_dir is not None else addon_dir / "wheelhouse"
    # Prefer add-on-local libs, but fall back to a per-user writable location
    default_libs = addon_dir / "libs"
    libs_dir = Path(libs_dir) if libs_dir is not None else default_libs

    def _ensure_writable_dir(p: Path) -> bool:
        try:
            p.mkdir(parents=True, exist_ok=True)
            # quick write check
            test = p / ".write_test"
            test.write_text("ok", encoding="utf8")
            test.unlink()
            return True
        except Exception:
            return False

    if not _ensure_writable_dir(libs_dir):
        # fallback: try Blender's per-user scripts/addons location, then home
        try:
            import bpy

            user_addons = Path(bpy.utils.user_resource("SCRIPTS", "addons"))
            fallback = user_addons / "arc_libs"
        except Exception:
            fallback = Path.home() / ".arc" / "libs"

        fallback.mkdir(parents=True, exist_ok=True)
        libs_dir = fallback

    # Check if key packages are already available within the add-on libs folder
    # We check reportlab (PDF) as the primary canary; networkx is also bundled.
    spec = importlib.util.find_spec("reportlab")
    if spec and spec.origin:
        try:
            spec_path = Path(spec.origin).resolve()
            if str(spec_path).startswith(str(libs_dir.resolve())):
                return True, "Dependencies already installed in add-on libs"
        except Exception:
            pass

    whls = sorted(wheel_dir.glob("*.whl"))
    if not whls:
        return False, f"No wheels found in {wheel_dir}"

    # Ensure pip is available
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        try:
            import ensurepip  # type: ignore

            ensurepip.bootstrap()
        except Exception as e:
            return False, f"pip not available and ensurepip failed: {e}"

    wheel_paths = [str(p) for p in whls]
    cmd = [sys.executable, "-m", "pip", "install", "--no-index", "--no-deps", "--target", str(libs_dir)] + wheel_paths
    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError as e:
        return False, f"pip install failed: {e}"

    # Add to sys.path for immediate availability
    p = str(libs_dir)
    if p not in sys.path:
        sys.path.insert(0, p)

    # Verify installation location
    spec2 = importlib.util.find_spec("reportlab")
    if spec2 and spec2.origin:
        try:
            spec_path2 = Path(spec2.origin).resolve()
            if str(spec_path2).startswith(str(libs_dir.resolve())):
                return True, "Dependencies installed and available in add-on libs"
        except Exception:
            pass

    # Final fallback: try importing. networkx is required; reportlab drives the
    # optional PDF report. Importing `reportlab` alone is not a sufficient check
    # - reportlab >= 4 pulls PIL in from reportlab.lib.utils, so the PDF path can
    # still fail when pillow is absent.
    try:
        import networkx  # type: ignore
    except Exception as e:
        return False, f"Installed but import failed: {e}"

    try:
        import reportlab.platypus  # type: ignore  # noqa: F401
    except Exception as e:
        return True, (
            "Dependencies installed; PDF export unavailable "
            f"({e}). Install pillow to enable PDF reports."
        )

    return True, "Dependencies installed and available"


def get_default_user_rules_dir() -> Path:
    """Return the preferred per-user rules directory.

    Preferred location (when running inside Blender):
      bpy.utils.user_resource("CONFIG") / "arc" / "rules"

    Fallback (headless or non-Blender):
      ~/.arc/rules
    """
    try:
        import bpy

        base = Path(bpy.utils.user_resource("CONFIG"))
        return base / "arc" / "rules"
    except Exception:
        return Path.home() / ".arc" / "rules"


def ensure_user_rules(user_dir: Optional[Path] = None) -> Path:
    """Ensure a writable per-user rules directory exists and copy packaged rules.

    - If `user_dir` is provided, use that location; otherwise use default.
    - If the user directory already contains `json_rules` or `python_rules`, do
      not overwrite existing files; only copy missing files from the packaged
      packaged `arc/core/rules` directory.
    - Returns the resolved user rules Path (may be fallback if writable test fails).
    """
    packaged = Path(__file__).resolve().parent.parent / "core" / "rules"
    target = Path(user_dir) if user_dir is not None else get_default_user_rules_dir()

    def _ensure_writable(p: Path) -> bool:
        try:
            p.mkdir(parents=True, exist_ok=True)
            test = p / ".write_test"
            test.write_text("ok", encoding="utf8")
            test.unlink()
            return True
        except Exception:
            return False

    if not _ensure_writable(target):
        # fallback to home-based path
        fallback = Path.home() / ".arc" / "rules"
        fallback.mkdir(parents=True, exist_ok=True)
        target = fallback

    # Copy packaged rules into target if missing
    try:
        for sub in ("json_rules", "python_rules"):
            src = packaged / sub
            dst = target / sub
            if not src.exists():
                continue
            if not dst.exists():
                shutil.copytree(src, dst)
            else:
                # Merge: copy files that don't yet exist in destination
                for f in src.rglob("*"):
                    if f.is_file():
                        rel = f.relative_to(src)
                        destf = dst / rel
                        destf.parent.mkdir(parents=True, exist_ok=True)
                        if not destf.exists():
                            shutil.copy2(f, destf)
    except Exception:
        # best-effort: ignore copy errors and return target
        pass

    return target
