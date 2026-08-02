# Third-party notices

ARC's own source is split by licence: `arc/core/` is **Apache-2.0** and
`arc/spatial/` is **GPL-3.0-or-later** (see [LICENSE](LICENSE) for the full
mapping). This file records third-party material **redistributed inside this
repository** and the terms it remains under. ARC's licences do not extend to any
of it.

Runtime dependencies that ARC merely imports — and does not redistribute — are
listed separately at the end for attribution only.

---

## Redistributed in this repository

### Python wheels bundled for the Blender extension

`arc/wheelhouse/` holds two unmodified wheels. `blender_manifest.toml` declares
them, and Blender installs them when the extension is first enabled. Each wheel
carries its own licence text in its `dist-info`, which is preserved verbatim.

| Package | Version | Licence | Licence text |
|---|---|---|---|
| [NetworkX](https://networkx.org/) | 3.6.1 | BSD-3-Clause | `networkx-3.6.1.dist-info/licenses/LICENSE.txt` inside the wheel |
| [ReportLab](https://www.reportlab.com/) | 4.4.10 | BSD-style, © 2000–2025 ReportLab Inc. | `reportlab-4.4.10.dist-info/licenses/LICENSE` inside the wheel |

The ReportLab wheel additionally bundles font files under their own terms
(Bitstream Vera, DarkGarden); see the licence files under `reportlab/fonts/`
inside the wheel.

Both wheels are pure-Python (`py3-none-any`) and are shipped unmodified.

### IFC example model

| File | Origin | Licence |
|---|---|---|
| `examples/models/sample-house.ifc` | [xBimTeam / XbimEssentials](https://github.com/xBimTeam/XbimEssentials) test file `SampleHouse4.ifc` | CDDL-1.0 |

Full provenance, including how the file was identified, is in
[examples/models/README.md](examples/models/README.md).
`examples/models/highrise-apartment.ifc` is this project's own work and is
licensed with ARC.

### Regulation documents — deliberately NOT redistributed

The rule pack encodes requirements derived from NBC 2016, Mumbai DCPR 2034, and
UDCPR. Those documents are third-party publications and are **not** included in
this repository. See [regulations/README.md](regulations/README.md) for sources
and how clauses map to rules.

---

## Imported but not redistributed

ARC depends on these at runtime without bundling or linking their code into the
distributed package. Listed for attribution; their licences govern their own
distributions, not this one.

| Project | Role in ARC | Licence |
|---|---|---|
| [Blender](https://www.blender.org/) | Host application for ARC-Spatial | GPL-2.0-or-later |
| [Bonsai](https://bonsaibim.org/) (formerly BlenderBIM) | Imports IFC models into the Blender scene that ARC-Spatial then reads | GPL-3.0-or-later |
| [IfcOpenShell](https://ifcopenshell.org/) | IFC parsing for the headless adapter (optional import) | LGPL-3.0-or-later |
| [Shapely](https://shapely.readthedocs.io/) | Polygon geometry tier (optional import) | BSD-3-Clause |
| [pytest](https://pytest.org/) | Test suite only; not shipped | MIT |

If you believe something is attributed incorrectly or is missing, please open an
issue — see [CONTRIBUTING.md](CONTRIBUTING.md).
