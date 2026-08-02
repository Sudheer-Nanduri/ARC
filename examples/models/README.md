# Example models

The two IFC4 models used for the reference evaluation reported in the DBP26
paper. Both are kept byte-exact (see `.gitattributes`) so the published results
can be reproduced from this repository.

```bash
arc-check --ifc examples/models/sample-house.ifc      --stage submission
arc-check --ifc examples/models/highrise-apartment.ifc --stage submission
```

| Model | Elements | Origin | Licence |
|---|---|---|---|
| `sample-house.ifc` | 42 | Third party — xBimTeam | CDDL-1.0 (not ARC's GPL) |
| `highrise-apartment.ifc` | 1,350 | This project | GPL-3.0-or-later, with ARC |

---

## sample-house.ifc — third-party file

This is **not** an ARC file. It is the `SampleHouse4.ifc` test model from the
[xBim Toolkit](https://docs.xbim.net/), specifically
[`XbimEssentials/Tests/TestFiles/SampleHouse4.ifc`](https://github.com/xBimTeam/XbimEssentials/blob/master/Tests/TestFiles/SampleHouse4.ifc),
re-serialized by the Xbim File Processor (the header records
`Xbim File Processor version 4.0.0.0`, 2016-11-11). The original was exported
from Autodesk Revit 2015. Identity is confirmed by the shared `IfcProject` GUID
`1o0c33arXF9AEePDXPKItb`.

The xBim Toolkit is released under the **CDDL-1.0**. No separate licence
statement accompanies the test files, so they are taken to fall under the
repository licence.

**ARC's GPL-3.0-or-later does not extend to this file.** It is redistributed
here unmodified in substance, under its own terms, purely so the paper's
sample-house results stay reproducible. If you reuse it, honour CDDL-1.0 and
credit xBimTeam — not this project.

Site coordinates in the file (≈51.50, −0.13, London) and the project strings
(`Project Number`, `Project Name`, `Default`) are authoring-tool defaults, not a
real location or project.

## highrise-apartment.ifc — this project's model

Authored by the ARC team in Autodesk Revit 2023 and exported to IFC4. This is the
"real high-rise apartment model" referred to in the paper: a genuine residential
building design, not a synthetic test case.

It carries no client, project, or site identification. The postal address field
holds the Revit placeholder `Enter address here`; the project, site, and building
names are template defaults; and the site coordinates (≈42.36, −71.06) are
Revit's default project location in Boston, Massachusetts — **not** the building's
real location.

Licensed with ARC under GPL-3.0-or-later.

---

## Reproducing the reported results

Expected output at the submission stage:

| Model | Pairs | PASS | FAIL | INCONCLUSIVE | HUMAN_REQUIRED |
|---|---|---|---|---|---|
| Sample house | 26 | 18 | 4 | 4 | 0 |
| High-rise apartment | 888 | 514 | 177 | 181 | 16 |

The high-rise run also reports 140 critical failures. Differences usually mean a
missing optional dependency — without `shapely` several geometry checks
downgrade to INCONCLUSIVE, which shifts counts between columns. See
[INSTALL.md](../../INSTALL.md#verifying-the-install).
