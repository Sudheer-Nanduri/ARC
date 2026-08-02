# Contributing to ARC

ARC is an open compliance-evidence engine, released alongside a DBP26 conference
paper as a reference implementation. It is deliberately built so that most useful
work - new jurisdictions, new check capabilities, new adapters, new integrations -
can be done **without modifying the core engine**.

Contributions, forks, criticism, and independent replication are all welcome.

---

## Ways to contribute

You do not have to touch the engine to be useful.

| Contribution | Where it goes | Core changes needed |
|---|---|---|
| A rule pack for another jurisdiction | `arc/core/rules/` or your own package | None |
| A new check type | Entry-point extension package | None |
| An adapter for another model format | Your own package | None |
| Enrichment / simulation / geometry service | External service feeding ARC | None |
| Integration (Revit, Speckle, web, CI) | Your own repo | None |
| Reproducing or disputing the paper's results | Issue or paper | None |
| Engine fixes and features | Pull request here | Yes - read the invariants below |

See [docs/integrator-guide.md](docs/integrator-guide.md) for the nine external
actor categories and worked examples of each.

---

## Where help is most valuable

These are the limitations the paper states about itself. Each is an open
invitation:

1. **More jurisdictions.** The shipped pack is 12 rules from NBC 2016 and Mumbai
   DCPR 2034, purposively chosen to exercise all six statuses. A second
   jurisdiction is the strongest test of whether the core is genuinely
   regulation-agnostic.
2. **Expert-labelled ground truth.** The evaluation validates *functionality*, not
   professional correctness. Hand-verified rule-element pairs - where a qualified
   reviewer states the right answer - would let ARC be tested on verdict accuracy,
   not just mechanics. This is the single most valuable contribution available.
3. **Richer geometry.** Checks currently lean on AABB and polygon tiers. Complex
   spatial requirements need mesh-based measurement.
4. **Real waiver data.** The waiver mechanism was exercised with four synthetic
   records because no public authority waiver dataset was available. Real
   deviation records would test it properly.
5. **More models.** Two IFC4 models is a small evidence base. Models that break
   ARC are useful bug reports.
6. **Practitioner validation.** Whether structured evidence actually improves
   reviewer understanding or decision quality is an open question requiring
   user studies.
7. **Standards integration.** ARC is intended to complement, not replace, IDS
   model-readiness validation, BCF issue exchange, semantic enrichment, rule
   extraction from regulation text, and permit platforms. Adapters in any of
   these directions are welcome.

If you are considering academic collaboration on any of the above, contact the
authors listed in [CITATION.cff](CITATION.cff).

---

## Development setup

```bash
git clone <repository-url> arc
cd arc

python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate

pip install -e ".[dev,pdf,geometry,ifc]"
```

Full instructions, per-OS notes, and troubleshooting: [INSTALL.md](INSTALL.md).

### Before opening a pull request

```bash
python -m pytest tests/ -q
```

Expected: **157 passed, 1 skipped**. Also confirm the reference evaluation still
reproduces - see [INSTALL.md](INSTALL.md#verifying-the-install). If your change
moves those numbers, say so explicitly in the PR and explain why the new numbers
are correct. Silent drift in the reference results is the one thing that would
undermine the paper.

---

## Invariants - do not break these

These are not style preferences. They are the contract the paper describes, and
the test suite enforces them.

1. **ARC produces evidence, not verdicts.** A `PASS` is not an approval. Nothing
   in the engine should imply institutional authority.
2. **The six statuses are engine-enforced and mutually exclusive.** Checkability
   gates run in a fixed order and the first failing gate determines the status.
   Status logic belongs in the engine, not in individual rules.
3. **Every element-scope non-binary result is routed** to a responsible actor.
4. **Every element result carries an evidence bundle** - including gate results
   and dormant-rule results.
5. **Waivers annotate; they never alter.** A waived FAIL stays a FAIL. Waiver
   processing must not change pair-axis counts.
6. **`INCONCLUSIVE` and `HUMAN_REQUIRED` are not interchangeable.** A data gap is
   not a judgment call.
7. **`arc/core/` imports no Blender and no IFC libraries.** The core must keep
   running anywhere Python runs. Blender-dependent code lives in `arc/spatial/`
   and guards `import bpy`.
8. **Missing capability is reported, never guessed.** If a measurement cannot be
   made reliably, the correct output is `INCONCLUSIVE` or `HUMAN_REQUIRED` with a
   reason - not an approximation presented as a result.

New behaviour that touches any of these should arrive with a test that would fail
without it. `tests/core/test_evidence_invariants.py` is the model to follow - it
encodes these invariants directly.

---

## Code conventions

- Python 3.10+, standard library first; every third-party dependency beyond
  `networkx` must be optional and degrade gracefully.
- Match the surrounding style. Comments explain *why*, not *what*.
- Reference shipped specification documents (`docs/specifications/…`) rather than
  private notes - a reader must be able to follow every pointer.
- Text output must survive narrow console encodings; see `_make_console_safe()`
  in `arc/core/cli.py`.
- Anything added to `requirements.txt` is shipped inside the Blender extension
  zip, so keep that file to runtime dependencies only. Dev and test dependencies
  belong in `pyproject.toml`.

---

## Rule packs

Rules are governed artifacts, not just code. A pack carries a manifest declaring
identifier, version, jurisdiction, and governance status, plus a clause ledger
recording which clauses are covered and - importantly - which are **deliberately
excluded from automation, with the reason**.

Python rules are statically screened and executed in a restricted namespace.
Treat third-party rule packs as you would any executable dependency.

See [docs/rule-authoring-guide.md](docs/rule-authoring-guide.md) and
[regulations/README.md](regulations/README.md).

---

## Reporting bugs

Useful reports include: ARC version, Python version, OS, Blender version if
relevant, the command you ran, and what you expected versus what happened. A
minimal IFC file that reproduces the problem is worth more than a description.

Findings that contradict the paper's reported results are especially welcome -
please include enough detail to reproduce.

---

## Licence

ARC is split by licence, following the Core/Spatial architecture:

| Path | Licence |
|---|---|
| `arc/core/`, `run_checks.py`, `tests/core/` | **Apache-2.0** |
| `arc/spatial/`, `arc/__init__.py`, `scripts/build_addon.py`, `tests/spatial/` | **GPL-3.0-or-later** |

See [LICENSE](LICENSE) for the full mapping and the reasoning. Contributions are
accepted under the licence that already governs the file you are changing. By
submitting a pull request you confirm you have the right to license your
contribution accordingly.

**Two rules that must not break:**

1. Every new source file carries an `SPDX-License-Identifier` header matching
   its directory. Copy the header from a neighbouring file.
2. `arc/core/` must never import GPL-licensed code. That includes `bpy` and
   anything that pulls it in. A GPL import inside core would make the combined
   work GPL and destroy the split — it is the one change that cannot be undone
   by a later commit.

Note that regulation documents themselves are third-party publications and are
not redistributed in this repository - see [regulations/README.md](regulations/README.md).
Do not add copyrighted regulation text to a pull request; cite the clause instead.

---

## Citing ARC

If ARC contributes to academic work, please cite the DBP26 paper - see
[CITATION.cff](CITATION.cff).
