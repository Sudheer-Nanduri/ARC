# Changelog

## 0.1.0 - 2026-08 (initial public release)

Reference implementation of the ARC evidence contract, as described in the DBP26 paper.

Initial release highlights:
- Dual licensing along the Core/Spatial line: **ARC-Core is Apache-2.0**, **ARC-Spatial is GPL-3.0-or-later**. Core carries no Blender and no IFC imports and can be vendored permissively; the Blender extension and the combined Python distribution remain GPL. Every source file carries an SPDX header; see [LICENSE](LICENSE) for the path-to-licence mapping.
- `nbc_acc_01` wheelchair-approach rule rewritten: approach zones on both door sides; FAIL only on positive obstruction evidence under both pull/push assignments; ambiguous swing/orientation routes to a reviewer (hybrid-fallback principle).
- Canonical `rule_version` shared by evidence bundles and waiver identity matching.
- `--waivers records.json` CLI flag for reproducible waiver runs.
- `confidence_label` vocabulary made honest: approximate (AABB proxy) / reported (authored property) / measured (engine-computed) / counted (aggregate).
- Invariants enforced and tested: every element-scope non-binary result is routed; every element result (including gate and dormant results) carries an evidence bundle; in-rule INCONCLUSIVE without a gate tag routes as data_missing.
- Regression and evidence-contract invariants covered by the public test suite.
