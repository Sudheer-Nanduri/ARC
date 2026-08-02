#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""ARC — Headless compliance runner (pip-installable entry point).

Usage (standard Python):
    arc-check --rules arc/core/rules --output results/ --demo
    arc-check --ifc path/to/model.ifc --output results/

Usage (Blender headless):
    blender --background --python run_checks.py -- \\
        --ifc path/to/model.ifc --output results/

Arguments:
    --ifc PATH       IFC file to check (uses IfcOpenShell)
    --rules DIR      Rules directory (default: arc/core/rules)
    --output DIR     Output directory for reports (default: results/)
    --demo           Run with built-in demo elements (no IFC required)
    --stage STAGE    concept | schematic | submission  (default: concept). Alias: --mode.
    --categories     Comma-separated categories to enable (default: all)
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Dict

if TYPE_CHECKING:  # imported lazily at runtime inside _load_waiver_file
    from .data_models import WaiverRecord


# ---------------------------------------------------------------------------
# Console encoding
# ---------------------------------------------------------------------------

def _make_console_safe() -> None:
    """Stop report text from crashing on consoles with a narrow codec.

    Reports carry typographic characters, and element names come from the
    model, so they can hold any script. Windows consoles frequently default to
    cp437/cp932 and Python raises UnicodeEncodeError rather than degrading.
    Switching only the error handler keeps full output wherever the terminal
    can render it, and substitutes replacement characters where it cannot.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            # Non-reconfigurable stream (older Python, or a redirected object).
            pass


# ---------------------------------------------------------------------------
# Argument parsing - handle both plain Python and Blender's '--' separator
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    # Blender passes user args after '--'
    argv = sys.argv[:]
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = argv[1:]

    p = argparse.ArgumentParser(
        description="ARC — Headless Compliance Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--ifc",        default=None, nargs="+", help="IFC file(s) to analyse — IFC4X3, IFC4, or IFC2X3 (multiple for federated)")
    p.add_argument("--disciplines", default=None,          help="Comma-separated discipline labels for federated IFC files")
    p.add_argument("--list-packs",  action="store_true",   help="List available rule packs and exit")
    p.add_argument("--rules",      default="arc/core/rules",   help="Rules directory (default: arc/core/rules)")
    p.add_argument("--output",     default="results",     help="Output directory (default: results/)")
    p.add_argument("--demo",       action="store_true",   help="Use built-in demo elements")
    p.add_argument("--stage", "--mode", dest="stage", default="concept",
                   choices=["concept", "schematic", "submission"],
                   help="Design stage: concept, schematic, or submission (default: concept). "
                        "Alias --mode kept for backward compatibility.")
    p.add_argument("--categories", default="all",
                   help="Comma-separated categories (accessibility,fire_egress,spatial,parking,ventilation,safety)")
    p.add_argument("--regulation-date", default=None,
                   help="ISO date (YYYY-MM-DD) to filter rules by effective/superseded date")
    p.add_argument("--baseline", default=None,
                   help="Path to baseline results JSON for delta comparison")
    p.add_argument("--waivers", default=None,
                   help="Path to a JSON file with waiver records (list of objects "
                        "or {waiver_id: object} map) to consider during the run")
    return p.parse_args(argv)


def _load_waiver_file(path: str) -> Dict[str, "WaiverRecord"]:
    """Load waiver records from a JSON file into {waiver_id: WaiverRecord}.

    Accepts either a JSON list of record objects or a mapping keyed by
    waiver id. Unknown fields are collected into ``extra`` so deployment-
    specific fields never crash the CLI. Records without a waiver_id are
    skipped with a warning.
    """
    import dataclasses
    import json as _json
    from .data_models import WaiverRecord

    raw = _json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        entries = list(raw.values())
    elif isinstance(raw, list):
        entries = raw
    else:
        raise ValueError("Waiver file must be a JSON list or object")

    known = {f.name for f in dataclasses.fields(WaiverRecord)}
    out: Dict[str, WaiverRecord] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("waiver_id"):
            print(f"  [WARN] Skipping waiver entry without waiver_id: {entry!r}"[:120])
            continue
        kwargs = {k: v for k, v in entry.items() if k in known}
        extra = {k: v for k, v in entry.items() if k not in known}
        if extra:
            kwargs.setdefault("extra", {}).update(extra)
        rec = WaiverRecord(**kwargs)
        out[rec.waiver_id] = rec
    return out


# ---------------------------------------------------------------------------
# Demo context
# ---------------------------------------------------------------------------

def _demo_context():
    from .context import Context
    from .data_models import AABB, Element

    return Context([
        Element(
            guid="p1", ifc_class="IfcSpace",
            aabb=AABB([0, 0, 0], [3.5, 3.5, 2.8]),
            properties={"area": 12.25, "SpaceType": "Parking"},
        ),
        Element(
            guid="p2", ifc_class="IfcSpace",
            aabb=AABB([0, 0, 0], [2.0, 2.0, 2.8]),
            properties={"area": 4.0, "SpaceType": "Parking"},
        ),
        Element(
            guid="door1", ifc_class="IfcDoor",
            aabb=AABB([0, 0, 0], [1.0, 0.2, 2.1]),
            properties={"Width": 1.0},
        ),
        Element(
            guid="stair1", ifc_class="IfcStair",
            aabb=AABB([0, 0, 0], [1.8, 4.0, 3.0]),
            properties={},
        ),
        Element(
            guid="room1", ifc_class="IfcSpace",
            aabb=AABB([0, 0, 0], [4.0, 3.5, 2.8]),
            properties={"area": 14.0, "SpaceType": "Habitable"},
        ),
        Element(
            guid="kitchen1", ifc_class="IfcSpace",
            aabb=AABB([0, 0, 0], [2.8, 2.0, 2.8]),
            properties={"area": 5.6, "SpaceType": "Kitchen"},
        ),
        Element(
            guid="bath1", ifc_class="IfcSpace",
            aabb=AABB([0, 0, 0], [1.5, 1.8, 2.4]),
            properties={"area": 2.7, "SpaceType": "Bathroom"},
        ),
        Element(
            guid="railing1", ifc_class="IfcRailing",
            aabb=AABB([0, 0, 0], [3.0, 0.1, 1.1]),
            properties={},
        ),
    ])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    _make_console_safe()
    args = _parse_args()

    # --- List packs mode ---
    if args.list_packs:
        from .registry import RuleRegistry
        reg = RuleRegistry.scan(args.rules)
        packs = reg.list_packs()
        if not packs:
            print("No rule packs found.")
        else:
            print(f"{'Pack ID':<30} {'Version':<10} {'Jurisdiction':<15} {'Status':<12} {'Rules':<6} Description")
            print("-" * 100)
            for p in packs:
                print(f"{p['pack_id']:<30} {p['version']:<10} {p['jurisdiction']:<15} {p['status']:<12} {p['rules']:<6} {p['description'][:40]}")
        return

    # --- Extension discovery ---
    from .extensions import discover_extensions
    discover_extensions()

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- Load elements ---
    if args.demo or args.ifc is None:
        context = _demo_context()
        elements = list(context.elements)
        source_label = "Demo elements"
    elif len(args.ifc) == 1:
        from .ifc_integration import load_ifc, ifc_project_identity
        from .context import Context
        elements = load_ifc(args.ifc[0])
        if not elements:
            _print_error(f"Could not load IFC elements from: {args.ifc[0]}")
            sys.exit(1)
        ident = ifc_project_identity(args.ifc[0])
        context = Context(
            elements,
            project_id=ident["project_id"],
            model_source=ident["model_source"],
        )
        source_label = Path(args.ifc[0]).name
    else:
        # Federated model loading - anchor identity on the first file
        from .ifc_integration import load_federated_ifc, ifc_project_identity
        from .context import Context
        disciplines = args.disciplines.split(",") if args.disciplines else []
        elements = load_federated_ifc(args.ifc, disciplines)
        if not elements:
            _print_error(f"Could not load IFC elements from federated files")
            sys.exit(1)
        ident = ifc_project_identity(args.ifc[0])
        context = Context(
            elements,
            project_id=ident["project_id"],
            model_source=f"federated:{Path(args.ifc[0]).name}",
        )
        source_label = f"Federated ({len(args.ifc)} files)"

    # --- Load waiver records (optional) ---
    if args.waivers:
        try:
            waivers = _load_waiver_file(args.waivers)
            context.waivers.update(waivers)
            print(f"  Loaded {len(waivers)} waiver record(s) from {args.waivers}")
        except Exception as exc:
            _print_error(f"Could not load waiver file {args.waivers}: {exc}")
            sys.exit(1)

    # --- Load rules ---
    from .rule_loader import load_rules, load_json_rules
    rules_path = Path(args.rules)
    if rules_path.is_dir() and (rules_path / "json_rules").exists():
        rules = load_rules(str(rules_path))
    else:
        rules = load_json_rules(str(rules_path))

    # Filter categories if requested
    if args.categories != "all":
        enabled = {c.strip().lower() for c in args.categories.split(",")}
        rules = [r for r in rules if r.get("category", "").lower() in enabled]

    if not rules:
        _print_error("No rules loaded. Check --rules path.")
        sys.exit(1)

    # --- Execute ---
    from .rule_engine import RuleEngine
    engine = RuleEngine(rules, regulation_date=args.regulation_date, execution_stage=args.stage)
    results = engine.execute(context)

    payload = [r.to_dict() for r in results]
    ts = datetime.now(timezone.utc).isoformat()

    # --- Coverage data ---
    from .rule_loader import load_clause_ledger, build_clause_ledger_from_rules

    coverage_gaps_dicts = [g.to_dict() for g in engine.coverage_gaps]

    # Merge file-based ledger with auto-generated entries
    ledger_path = rules_path / "clause_ledger.json"
    file_ledger = load_clause_ledger(str(ledger_path))
    auto_ledger = build_clause_ledger_from_rules(rules)
    seen_refs = {cl.clause_ref for cl in file_ledger}
    merged_ledger = list(file_ledger) + [cl for cl in auto_ledger if cl.clause_ref not in seen_refs]
    clause_ledger_dicts = [
        {"clause_ref": cl.clause_ref, "rule_ids": cl.rule_ids,
         "automatable": cl.automatable, "rationale": cl.rationale}
        for cl in merged_ledger
    ]

    # --- JSON report ---
    from .report_generator import generate_json_report, generate_pdf_report, generate_html_report
    ifc_paths = args.ifc or []
    report_metadata = {
        "model_path": str(Path(ifc_paths[0]).resolve()) if len(ifc_paths) == 1 else None,
        "model_name": Path(ifc_paths[0]).name if len(ifc_paths) == 1 else source_label,
        "source": source_label,
        "stage": args.stage,
        "run_context": "CLI (headless)",
        "rule_preset": "custom",
        # Preserve the user-supplied form so shared reports do not disclose a
        # local checkout path merely because the default rule pack was used.
        "rules_path": str(rules_path),
    }
    json_path = out_dir / "compliance_results.json"
    generate_json_report(
        payload,
        str(json_path),
        metadata=report_metadata,
        coverage_gaps=coverage_gaps_dicts,
        clause_ledger=clause_ledger_dicts,
        rules=rules,
        elements=elements,
    )

    # --- HTML report ---
    html_path = out_dir / "compliance_report.html"
    generate_html_report(
        payload,
        str(html_path),
        metadata=report_metadata,
        coverage_gaps=coverage_gaps_dicts,
        clause_ledger=clause_ledger_dicts,
        rules=rules,
        elements=elements,
    )

    # --- PDF report ---
    pdf_path = out_dir / "compliance_report.pdf"
    try:
        generate_pdf_report(
            payload, str(pdf_path), metadata=report_metadata,
            coverage_gaps=coverage_gaps_dicts, clause_ledger=clause_ledger_dicts,
            rules=rules, elements=elements,
        )
        pdf_written = True
    except RuntimeError:
        pdf_written = False

    # --- BCF archive ---
    from .bcf_exporter import export_bcf
    bcf_path = out_dir / "issues.bcfzip"
    export_bcf(payload, str(bcf_path))

    # --- Compliance volumes metadata ---
    try:
        from ..spatial.visualizer import show_compliance_volumes
        show_compliance_volumes(payload, out_dir=str(out_dir / "volumes"))
    except ImportError:
        pass  # spatial module not available in core-only installs

    # --- Delta comparison ---
    delta_result = None
    if args.baseline:
        from .delta import load_baseline, compare_results
        baseline = load_baseline(args.baseline)
        if baseline:
            delta_result = compare_results(baseline, payload)
            delta_path = out_dir / "delta_report.json"
            import json as _json
            delta_path.write_text(_json.dumps(delta_result, indent=2), encoding="utf-8")

    # --- Summary ---
    from .data_models import (
        STATUS_PASS, STATUS_FAIL, STATUS_INCONCLUSIVE,
        STATUS_HUMAN_REQUIRED, STATUS_NOT_APPLICABLE, STATUS_UNSUPPORTED,
    )
    # Split element-scope (pair-axis) from aggregate-scope (class/model)
    # results so dormant-rule rows do not dilute the checkability ratio.
    element_results = [r for r in results if r.scope not in ("class", "model")]
    aggregate_results = [r for r in results if r.scope in ("class", "model")]

    pass_n    = sum(1 for r in element_results if r.status == STATUS_PASS)
    fail_n    = sum(1 for r in element_results if r.status == STATUS_FAIL)
    inconcl_n = sum(1 for r in element_results if r.status == STATUS_INCONCLUSIVE)
    human_n   = sum(1 for r in element_results if r.status == STATUS_HUMAN_REQUIRED)
    na_n      = sum(1 for r in element_results if r.status == STATUS_NOT_APPLICABLE)
    unsupp_n  = sum(1 for r in element_results if r.status == STATUS_UNSUPPORTED)
    pair_n    = len(element_results)
    agg_n     = len(aggregate_results)
    total_n   = pair_n  # backward-compat denominator for pct()
    critical_failures = sum(
        1 for r in results
        if r.status == STATUS_FAIL and r.severity == "critical"
    )
    # Per-element (pair-axis) checkability - pairs that landed on PASS or FAIL
    checkable_n = pass_n + fail_n
    checkability_pct = f"{checkable_n * 100 // pair_n}%" if pair_n else "0%"

    # Per-rule checkability - rules that produced at least one PASS or FAIL
    rule_has_definitive = {}
    for r in element_results:
        if r.status in (STATUS_PASS, STATUS_FAIL):
            rule_has_definitive[r.rule_id] = True
    active_rule_ids = {r.get("id") for r in rules if r.get("id")}
    executable_rules = sum(1 for rid in active_rule_ids if rule_has_definitive.get(rid))
    per_rule_pct = (
        f"{executable_rules * 100 // len(active_rule_ids)}%"
        if active_rule_ids else "0%"
    )

    def pct(n):
        return f"{n * 100 // pair_n}%" if pair_n else "0%"

    summary_lines = [
        "=" * 60,
        "  ARC — Headless Compliance Report",
        "=" * 60,
        f"  Source:         {source_label}",
        f"  Rules applied:  {len(rules)} rule(s)",
        f"  Stage:          {args.stage}",
        f"  Elements:       {len(context.elements)}",
        f"  Timestamp:      {ts}",
        "",
        "  RESULTS (pair axis — one entry per rule-element pair):",
        f"    PASS:            {pass_n:>5}  ({pct(pass_n)})",
        f"    FAIL:            {fail_n:>5}  ({pct(fail_n)})",
        f"    INCONCLUSIVE:    {inconcl_n:>5}  ({pct(inconcl_n)})",
        f"    HUMAN_REQUIRED:  {human_n:>5}  ({pct(human_n)})",
        f"    NOT_APPLICABLE:  {na_n:>5}  ({pct(na_n)})",
        f"    UNSUPPORTED:     {unsupp_n:>5}  ({pct(unsupp_n)})",
    ]
    if agg_n:
        summary_lines.append(
            f"    (+ {agg_n} aggregate-scope rows, not in the pair-axis totals)"
        )
    summary_lines += [
        "",
        f"  Per-element checkability: {checkable_n}/{pair_n} pairs fully checkable ({checkability_pct})",
        f"  Per-rule checkability:    {executable_rules}/{len(active_rule_ids)} rules executable ({per_rule_pct})",
        f"  Critical failures:        {critical_failures}",
        f"  Coverage gaps:            {len(coverage_gaps_dicts)}",
    ]
    if engine.stage_exit_result:
        ser = engine.stage_exit_result
        from .data_models import DEFAULT_STAGE_CRITERIA
        criteria = DEFAULT_STAGE_CRITERIA[args.stage]
        summary_lines += [
            "",
            f"  STAGE EXIT CRITERIA ({args.stage}):",
            f"    Checkability:  {ser['checkability_pct']}% (min {criteria.min_checkability_pct}%) {'[OK]' if ser['checkability_met'] else '[FAIL]'}",
            f"    Critical:      {ser['critical_unresolved']} unresolved (max {criteria.max_unresolved_critical}) {'[OK]' if ser['critical_met'] else '[FAIL]'}",
            f"    Overall:       {'MET' if ser['met'] else 'NOT MET'}",
        ]
    if delta_result:
        ds = delta_result["summary"]
        summary_lines += [
            "",
            "  DELTA (vs baseline):",
            f"    New failures:      {ds['new_failure_count']}",
            f"    Resolved failures: {ds['resolved_failure_count']}",
            f"    Status changes:    {ds['status_change_count']}",
        ]
    summary_lines += [
        "",
        "  Reports saved to:",
        f"    {json_path}",
        f"    {html_path}",
    ]
    if pdf_written:
        summary_lines.append(f"    {pdf_path}")
    else:
        summary_lines.append("    PDF skipped (install reportlab and pillow for PDF output)")
    summary_lines += [
        f"    {bcf_path}",
        "=" * 60,
    ]

    summary_text = "\n".join(summary_lines)
    print(summary_text)

    # Write summary.txt
    summary_file = out_dir / "summary.txt"
    summary_file.write_text(summary_text, encoding="utf-8")
    print(f"\n  Summary written to: {summary_file}")


def _print_error(msg: str):
    print(f"\n[ERROR] {msg}\n", file=sys.stderr)


if __name__ == "__main__":
    main()
