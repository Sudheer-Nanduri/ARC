#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""ARC — Headless compliance runner (thin wrapper).

This file delegates to arc.core.cli.main() so the same logic is available both
as ``python run_checks.py`` and as ``arc-check`` (pip entry point).

Usage:
    python run_checks.py --rules arc/core/rules --output results/ --demo
    python run_checks.py --ifc path/to/model.ifc --output results/

Usage (inside Blender, nothing installed):
    blender --background --python run_checks.py -- --demo
    blender --background --python run_checks.py -- --ifc model.ifc --output results/
"""
import sys
from pathlib import Path

# Blender runs scripts without putting the script's own directory on sys.path,
# unlike the plain interpreter, so make the repository importable first. This
# also lets the wrapper run straight from a clone that was never pip-installed.
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from arc.core.cli import main  # noqa: E402  (import follows the sys.path setup)

if __name__ == "__main__":
    main()
