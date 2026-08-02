# Installing and Verifying ARC

This guide covers the three ways to run ARC, how to verify the install reproduces
the results reported in the DBP26 paper, and how to resolve the dependency issues
you are most likely to hit.

If you only want to check that the engine works, jump to
[Verifying the install](#verifying-the-install).

---

## Requirements

| | Minimum | Notes |
|---|---|---|
| Python | 3.10 | 3.11+ recommended |
| OS | Windows / macOS / Linux | ARC-Core is pure Python; no compiled ARC code |
| Blender | 4.2 LTS | Only for the spatial extension (Option B); 4.1 and older via the legacy zip |
| Disk | ~200 MB | Mostly IfcOpenShell and the example models |

### Verified configurations

Everything below was run for the 0.1.0 release. Anything marked *untested* is
expected to work but was not exercised - please report what you find.

| Environment | Status |
|---|---|
| CPython 3.10 - 3.13, CLI | Tested |
| Blender 4.5 LTS (Python 3.11) - extension install, enable, panels, wheels | Tested |
| Blender 5.1 (Python 3.13) - extension install, enable, panels, wheels | Tested |
| Blender 4.2 - 4.4 | Untested; same extension API as 4.5 |
| Blender 4.1 and older | Untested; use the legacy add-on zip |
| Windows consoles (cp437 / cp932 / ascii / UTF-8) | Tested |
| macOS / Linux | Untested for the Blender UI; the CLI is pure Python and platform-neutral |

The bundled wheels are pure-Python (`py3-none-any`), so the same extension zip
works across Blender releases even though Blender changes its Python version
between them (4.5 ships 3.11, 5.1 ships 3.13).

Only `networkx` is a hard runtime dependency. Everything else is optional and
degrades gracefully - ARC reports what it could not do rather than failing.

| Package | Enables | Without it |
|---|---|---|
| `networkx` | Topology graph, pathfinding | **Required** |
| `ifcopenshell` | Reading `.ifc` files | `--demo` and programmatic elements still work |
| `shapely` | Polygon geometry tier (inscribed-circle widths) | Falls back to the AABB tier; some checks return INCONCLUSIVE |
| `reportlab` + `pillow` | PDF report | JSON, HTML, and BCF still written |
| `pytest` | Test suite | - |

---

## Option A - CLI install (recommended)

### With `uv`

```bash
git clone <repository-url> arc
cd arc

uv venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

uv pip install -e ".[dev]"         # engine + pytest
uv pip install ifcopenshell        # IFC reading
uv pip install shapely             # polygon geometry tier
uv pip install reportlab pillow    # PDF reports
```

### With plain `pip`

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -e ".[dev]"
pip install ifcopenshell shapely reportlab pillow
```

### Everything at once

The optional extras are declared in `pyproject.toml`:

```bash
pip install -e ".[dev,pdf,geometry,ifc]"
```

Confirm the entry point is on your PATH:

```bash
arc-check --help
arc-check --list-packs
```

### Reproducible install

`uv.lock` pins the exact versions used for the results reported in the paper.
To reproduce that environment rather than resolve fresh:

```bash
uv sync --extra dev --extra ifc --extra geometry --extra pdf
```

---

## Option B - Blender extension

Blender changed its add-on packaging in 4.2, so ARC ships two archives from the
same source. Build whichever your Blender needs - or `--all` for both:

```bash
python scripts/build_addon.py           # arc-0.1.0.zip          (Blender 4.2+)
python scripts/build_addon.py --legacy  # arc-0.1.0-legacy.zip   (Blender <= 4.1)
python scripts/build_addon.py --all     # both
```

### Blender 4.2 and newer - extension

1. Build `arc-0.1.0.zip` as above.
2. In Blender: **Edit -> Preferences -> Get Extensions -> ▾ (top right) -> Install
   from Disk…** and select the zip.
3. Blender installs the bundled wheels (networkx, reportlab) automatically on
   first enable. No manual dependency step.
4. Open the 3D viewport sidebar with **N** and select the **Spatial ARC** tab.

Verified end to end on Blender 4.5 LTS and 5.1: install, enable, wheel
resolution, operator and panel registration.

### Blender 4.1 and older - legacy add-on

The pre-4.2 installer does not read `blender_manifest.toml` and will not install
wheels for you.

1. Build `arc-0.1.0-legacy.zip`.
2. **Edit -> Preferences -> Add-ons -> Install…** and select the zip.
3. Enable **Spatial ARC** in the add-on list.
4. Dependencies: the add-on unpacks its bundled wheelhouse into a local `libs/`
   folder on first enable (`arc.spatial.install.ensure_deps()`), which needs no
   admin rights. If that fails, install into Blender's Python directly - see
   [PDF export](#pdf-skipped-install-reportlab-and-pillow-for-pdf-output) below
   for the per-OS interpreter paths, substituting `networkx` for `pillow`.

This path is untested against a running pre-4.2 build. **If your Blender is
older than 4.2 and the add-on gives you trouble, use Option C** - the headless
runner needs no Blender at all and produces identical results.

### Loading IFC models

ARC does not parse IFC inside Blender itself - it reads whatever is already in
the scene. To get an IFC model in, install the
[Bonsai](https://bonsaibim.org/) add-on (formerly BlenderBIM) and import through
it. ARC then reads the resulting objects and their IFC property sets.

Without Bonsai you can still run ARC-Spatial against ordinary Blender geometry,
but element classification and property-based rules will be limited.

### Refreshing the bundled wheels

```bash
python scripts/build_addon.py --download-wheels
```

This re-downloads everything listed in `requirements.txt` into `arc/wheelhouse/`.
Anything you add to `requirements.txt` is shipped inside the extension zip, so
keep that file to runtime dependencies only.

---

## Option C - Headless wrapper (works everywhere)

`run_checks.py` runs the engine straight out of a clone without installing ARC
as a package or resolving its entry point. The required `networkx` dependency
must still be installed. `uv run --frozen` creates the locked environment for
you; add the `ifc` extra when reading IFC files.

```bash
uv run --frozen python run_checks.py --demo
uv run --frozen --extra ifc python run_checks.py --ifc examples/models/sample-house.ifc --output results/

# Or, in an environment where dependencies are already installed:
python run_checks.py --demo
python run_checks.py --ifc examples/models/sample-house.ifc --output results/
```

It also runs inside Blender's own Python, which is useful when Blender is the
only interpreter you have with the dependencies already present:

```bash
blender --background --python run_checks.py -- --demo
blender --background --python run_checks.py -- --ifc model.ifc --output results/
```

The `--` separates Blender's arguments from ARC's; everything after it goes to
ARC. Verified on Blender 4.5 LTS and 5.1.

Full paths to the Blender executable, if it is not on your `PATH`:

```bash
# Windows
"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe" --background --python run_checks.py -- --demo

# macOS
/Applications/Blender.app/Contents/MacOS/Blender --background --python run_checks.py -- --demo

# Linux
/path/to/blender --background --python run_checks.py -- --demo
```

---

## Verifying the install

### 1. Run the test suite

```bash
python -m pytest tests/ -q
```

Expected: **157 passed, 1 skipped**. The skip is a Blender-only path that cannot
run outside `bpy`.

Two reporting tests write into `tests/_tmp/`, so run pytest from a location you
can write to.

### 2. Run without any model

```bash
arc-check --demo
```

This builds eight synthetic elements in memory - no IFC, no `ifcopenshell`
needed. A committed copy of this output is in `examples/reference-output/` if you
want to compare.

### 3. Reproduce the paper's numbers

Both example models are in `examples/models/`. These are the exact files used for
the evaluation.

```bash
arc-check --ifc examples/models/sample-house.ifc      --stage submission
arc-check --ifc examples/models/highrise-apartment.ifc --stage submission
```

Expected:

| Model | Elements | Pairs | PASS | FAIL | INCONCLUSIVE | HUMAN_REQUIRED |
|---|---|---|---|---|---|---|
| Sample house | 42 | 26 | 18 | 4 | 4 | 0 |
| High-rise apartment | 1,350 | 888 | 514 | 177 | 181 | 16 |

The high-rise run also reports **140 critical failures**. It takes roughly 30-40
seconds - geometry is extracted per entity, single-threaded.

If your numbers differ, the usual cause is a missing optional dependency:
without `shapely` several geometry checks downgrade to INCONCLUSIVE, which moves
counts between columns.

### 4. Reproduce the waiver behaviour

```bash
arc-check --ifc examples/models/highrise-apartment.ifc \
          --stage submission \
          --waivers examples/waivers/waiver_records.json
```

Expected: the four records resolve as **applied**, **invalid**
(`identity_mismatch`), **invalid** (`past_expiry_date`), and **superseded**.
Pair-axis counts are unchanged and FAIL stays at 177 - **176 open, 1 waived**.
Waivers annotate results; they never alter them.

Inspect the states in the JSON output:

```bash
arc-check --ifc examples/models/highrise-apartment.ifc --stage submission \
          --waivers examples/waivers/waiver_records.json --output results/
python -c "import json;print([ (r['rule_id'], r['waiver_state']) for r in json.load(open('results/compliance_results.json'))['element_results'] if r.get('waiver_state')])"
```

---

## Troubleshooting

### `PDF skipped (install reportlab and pillow for PDF output)`

`reportlab` 4.x imports `PIL` from `reportlab.lib.utils`, so installing
`reportlab` alone is not enough - `import reportlab` succeeds while every module
ARC needs fails. Install both:

```bash
pip install reportlab pillow
```

**Inside Blender**, `pillow` is not bundled with the extension because it ships
platform- and Python-version-specific wheels, which would require a per-platform
wheel matrix in the manifest. To enable PDF export in Blender, install pillow
into Blender's own Python:

```bash
# Windows
"C:\Program Files\Blender Foundation\Blender 4.5\4.5\python\bin\python.exe" -m pip install pillow

# macOS
/Applications/Blender.app/Contents/Resources/4.5/python/bin/python3.11 -m pip install pillow

# Linux
/path/to/blender-4.5/4.5/python/bin/python3.11 -m pip install pillow
```

JSON, HTML, and BCF output are unaffected and need no extra packages.

### `ModuleNotFoundError: No module named 'ifcopenshell'`

Expected if you have not installed it - `--demo` still works. For IFC input:

```bash
pip install ifcopenshell
```

If pip cannot find a wheel for your Python version, see
[docs.ifcopenshell.org](https://docs.ifcopenshell.org/) for platform builds.
IfcOpenShell releases lag new Python versions; Python 3.11 or 3.12 is the safest
choice.

### More INCONCLUSIVE results than expected

Usually a missing `shapely`. The polygon tier (used for inscribed-circle width
measurement, e.g. the lobby-width rule) falls back to axis-aligned bounding
boxes, and checks that cannot be measured reliably are reported as INCONCLUSIVE
by design rather than guessed.

```bash
pip install shapely
```

This is ARC behaving correctly: a missing capability is reported, not hidden.

### `arc-check: command not found`

The virtual environment is not active, or the package was installed without
`-e`. Re-activate and reinstall, or invoke the module directly:

```bash
python -m arc.core.cli --demo
```

### The Blender panel does not appear

1. Confirm Blender is 4.2 or newer (**Help -> About**).
2. Confirm the extension is enabled in **Preferences -> Add-ons** (search "ARC").
3. Open the sidebar with **N** in the 3D viewport.
4. Check **Window -> Toggle System Console** (Windows) or launch Blender from a
   terminal to see registration errors.

### Tests fail with a permission error

Two reporting tests write to `tests/_tmp/`. Run pytest from a writable working
directory, not from a read-only or system location.

---

## Uninstalling

```bash
pip uninstall arc-spatial
```

For the Blender extension: **Preferences -> Add-ons -> Spatial ARC -> Remove**.
