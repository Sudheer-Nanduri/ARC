# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: GPL-3.0-or-later

"""Full N-Panel UI for Spatial ARC — Blender 4.2+ (tested on 4.5 LTS and 5.1).

Panel hierarchy (all in 3D Viewport sidebar, tab "Spatial ARC"):

  ARC_PT_Main              — Section tabs + status summary (always visible)
  ARC_PT_Help              — Keyboard shortcuts reference (collapsible)

  SETUP section:
    ARC_PT_ModelStatus     — IFC model health at a glance
    ARC_PT_RuleConfig      — Preset, mode, scope, run button
      ARC_PT_Categories    — Category checkboxes (collapsible sub-panel)
      ARC_PT_RuleSelection — Per-rule toggles (collapsible, default closed)
      ARC_PT_CustomRules   — Rules dir, import/export (collapsible, default closed)
      ARC_PT_Directories   — Results + rules directory paths (collapsible)

  REVIEW section:
    ARC_PT_Results         — Summary dashboard + category breakdown
    ARC_PT_IssueBrowser    — Per-issue drill-down + review notes
    ARC_PT_ElementDetail   — Per-element compliance (always visible)

  EXPORT section:
    ARC_PT_Export          — BCF / PDF / HTML / JSON export

Properties (stored on bpy.types.Scene.arc):
  See ARC_Props PropertyGroup below.
"""
from __future__ import annotations

import json
from pathlib import Path

try:
    import bpy
    from bpy.types import Panel, PropertyGroup
    from bpy.props import (
        BoolProperty, EnumProperty, FloatProperty,
        IntProperty, StringProperty,
    )
    _BPY = True
except ImportError:
    _BPY = False

# Module-level results cache (populated by ARC_OT_RunChecks)
_results_cache: list = []        # List[RuleResult.to_dict()]
_summary_cache: dict = {}        # ModelSummary.to_dict()
_run_timestamp: str = ""
_run_ifc_name: str = ""
_validation_warnings: list = []

if _BPY:

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    class ARC_Props(PropertyGroup):
        # Rule set
        rule_preset: EnumProperty(
            name="Preset",
            description="Pre-configured rule set",
            items=[
                ("mumbai_dcr_nbc", "Mumbai DCR / NBC", "Mumbai Development Control Regulations + National Building Code 2016"),
                ("custom", "Custom Rules", "Load rules from a user-selected rule-pack folder"),
            ],
            default="mumbai_dcr_nbc",
        )

        # Execution mode
        exec_mode: EnumProperty(
            name="Stage",
            description="Design stage determines check strictness",
            items=[
                ("concept",    "Concept",    "Early-stage advisory checks, tolerant of missing data"),
                ("schematic",  "Schematic",  "Design development checks, moderate strictness"),
                ("submission", "Submission", "Final submission checks, strict data requirements"),
            ],
            default="concept",
        )

        # Scope
        scope: EnumProperty(
            name="Scope",
            items=[
                ("entire_model", "Entire Model",       "Check all IFC elements"),
                ("selected",     "Selected Elements",   "Only currently selected objects"),
                ("visible",      "Visible Only",        "Only viewport-visible objects"),
            ],
            default="entire_model",
        )

        # Category toggles
        cat_accessibility: BoolProperty(name="Accessibility (NBC Part 3)",  default=True)
        cat_fire:          BoolProperty(name="Fire & Egress (DCR + NBC 4)", default=True)
        cat_spatial:       BoolProperty(name="Spatial Dimensions (DCR)",    default=True)
        cat_parking:       BoolProperty(name="Parking (DCR Sched. IV)",     default=True)
        cat_ventilation:   BoolProperty(name="Ventilation (DCR + NBC 8)",   default=True)
        cat_safety:        BoolProperty(name="Safety (NBC Part 4)",         default=True)

        # Execution state (updated by modal operator)
        is_running:      BoolProperty(default=False)
        progress:        FloatProperty(min=0, max=1, default=0, subtype="FACTOR")
        current_rule:    StringProperty(default="")
        elements_loaded: IntProperty(default=0)

        # Results summary (written after run; displayed in panels)
        last_run_ts:    StringProperty(default="")
        pass_count:         IntProperty(default=0)
        fail_count:         IntProperty(default=0)
        inconcl_count:      IntProperty(default=0)
        human_req_count:    IntProperty(default=0)
        not_applic_count:   IntProperty(default=0)
        unsupported_count:  IntProperty(default=0)
        total_checks:       IntProperty(default=0)
        # Routing / deviation summary
        waived_fail_count:        IntProperty(default=0)
        invalid_waiver_count:     IntProperty(default=0)
        superseded_waiver_count:  IntProperty(default=0)
        aggregate_count:      IntProperty(default=0)  # class + model scope findings
        rules_evaluated_count: IntProperty(default=0)
        rules_skipped_count:   IntProperty(default=0)

        # Element detail - show results for selected object's GUID
        selected_guid:  StringProperty(default="")
        selected_issue_key: StringProperty(default="")
        issue_search: StringProperty(
            name="Issue Search",
            description="Filter failed issues by rule id, element id, or message",
            default="",
        )
        ui_section: EnumProperty(
            name="Section",
            description="Switch between setup, review, and export workflows",
            items=[
                ("setup", "① Setup", "Configure rules and run checks"),
                ("review", "② Review", "Browse results, inspect issues, and visualize"),
                ("export", "③ Export", "Export reports and result files"),
            ],
            default="setup",
        )
        rule_search: StringProperty(
            name="Rule Search",
            description="Filter rules by id, title, category, or source",
            default="",
        )
        disabled_rule_ids_json: StringProperty(default="[]")
        review_status: EnumProperty(
            name="Review Status",
            items=[
                ("open", "Open", "Not yet reviewed"),
                ("planned", "Planned", "Change identified and queued"),
                ("reviewed", "Reviewed", "Reviewed and accepted"),
                ("resolved", "Resolved", "Issue addressed in the model"),
            ],
            default="open",
        )
        review_assignee: StringProperty(name="Assignee", default="")
        review_note: StringProperty(name="Note", default="")
        review_log_json: StringProperty(default="{}")

        # UI state
        show_categories: BoolProperty(name="Show Categories", default=True)
        show_results:    BoolProperty(default=True)

        # User-configurable results directory (for metadata, exports, volumes)
        results_dir: StringProperty(
            name="Results Directory",
            description="Directory to store results and load existing metadata",
            subtype='DIR_PATH',
            default="",
        )
        # Optional user-writable rules directory (initialised on first run)
        rules_dir: StringProperty(
            name="Rules Directory",
            description="Optional custom rules folder to store and load user-editable rules",
            subtype='DIR_PATH',
            default="",
        )

    # ------------------------------------------------------------------
    # Helper: get props safely
    # ------------------------------------------------------------------

    def _props(context) -> "ARC_Props | None":
        try:
            return context.scene.arc
        except Exception:
            return None

    def _compliance_pct(props) -> float:
        total = props.pass_count + props.fail_count
        return (props.pass_count / total * 100) if total else 0.0

    def _rules_root(props) -> str:
        try:
            if props and props.rules_dir:
                return props.rules_dir
        except Exception:
            pass
        return str(Path(__file__).resolve().parent.parent / "core" / "rules")

    def _available_rules(props) -> list:
        try:
            from ..core.rule_loader import load_rules
            return load_rules(_rules_root(props))
        except Exception:
            return []

    def _disabled_rule_ids(props) -> set:
        if props is None:
            return set()
        try:
            data = json.loads(props.disabled_rule_ids_json or "[]")
            return {str(item) for item in data if isinstance(item, str)}
        except Exception:
            return set()

    def _review_map(props) -> dict:
        if props is None:
            return {}
        try:
            data = json.loads(props.review_log_json or "{}")
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _review_for_issue(props, issue_key: str) -> dict:
        review = _review_map(props).get(issue_key, {})
        return review if isinstance(review, dict) else {}

    def _in_section(context, section: str) -> bool:
        props = _props(context)
        return bool(props and props.ui_section == section)

    def _rule_display_name(rule: dict) -> str:
        """Return a human-readable name for a rule."""
        title = rule.get("title")
        if title:
            return str(title)
        # Fallback: format ID as title case
        rid = str(rule.get("id", "unknown"))
        return rid.replace("_", " ").replace("-", " ").title()

    # ==================================================================
    # MAIN NAVIGATION PANEL (always visible - top of sidebar)
    # ==================================================================

    class ARC_PT_Main(Panel):
        bl_label = "Spatial ARC"
        bl_idname = "ARC_PT_main"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "Spatial ARC"
        bl_order = 0

        def draw(self, context):
            layout = self.layout
            props = _props(context)
            if props is None:
                layout.label(text="ARC not initialised", icon="ERROR")
                return

            # Section tabs
            layout.prop(props, "ui_section", expand=True)

            # Status summary - always visible regardless of tab
            status_row = layout.row(align=True)
            if props.elements_loaded:
                status_row.label(text=f"{props.elements_loaded} elements", icon="MESH_DATA")
            else:
                status_row.label(text="No model loaded", icon="MESH_DATA")

            if props.last_run_ts:
                pct = _compliance_pct(props)
                if pct >= 80:
                    icon = "CHECKMARK"
                elif pct >= 50:
                    icon = "ERROR"
                else:
                    icon = "CANCEL"
                status_row.label(text=f"{pct:.0f}% compliant", icon=icon)
            else:
                status_row.label(text="Not checked yet", icon="TIME")

    # ==================================================================
    # HELP SUB-PANEL (always visible, collapsed by default)
    # ==================================================================

    class ARC_PT_Help(Panel):
        bl_label = "Keyboard Shortcuts"
        bl_idname = "ARC_PT_help"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "Spatial ARC"
        bl_parent_id = "ARC_PT_main"
        bl_options = {"DEFAULT_CLOSED"}
        bl_order = 99

        def draw(self, context):
            layout = self.layout
            col = layout.column(align=True)
            col.label(text="Ctrl+Shift+R    Run checks", icon="PLAY")
            col.label(text="Ctrl+Shift+H    Toggle heatmap", icon="COLORSET_13_VEC")
            col.label(text="Ctrl+Shift+C    Clear overlays", icon="TRASH")
            col.label(text="Ctrl+Shift+E    Export BCF", icon="EXPORT")

    # ==================================================================
    # SETUP SECTION
    # ==================================================================

    # ------------------------------------------------------------------
    # 1. Model Status
    # ------------------------------------------------------------------

    class ARC_PT_ModelStatus(Panel):
        bl_label = "Model Status"
        bl_idname = "ARC_PT_model_status"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "Spatial ARC"
        bl_order = 1

        @classmethod
        def poll(cls, context):
            return _in_section(context, "setup")

        def draw(self, context):
            layout = self.layout
            props = _props(context)

            # IFC / element count
            count = props.elements_loaded if props else 0
            if count:
                layout.label(text=f"{count} IFC elements loaded", icon="CHECKMARK")
            else:
                box = layout.box()
                box.label(text="No IFC model detected", icon="INFO")
                col = box.column(align=True)
                col.label(text="Getting started:")
                col.label(text="  1. Install the Bonsai add-on", icon="BLANK1")
                col.label(text="  2. File → Import → IFC (.ifc)", icon="BLANK1")
                col.label(text="  3. Return here to configure & run", icon="BLANK1")

            # Validation warnings from last load
            if _validation_warnings:
                box = layout.box()
                box.label(text=f"{len(_validation_warnings)} model warning(s):", icon="ERROR")
                for w in _validation_warnings[:5]:
                    box.label(text=f"  {w[:60]}", icon="DOT")
                if len(_validation_warnings) > 5:
                    box.label(text=f"  … and {len(_validation_warnings) - 5} more")

            row = layout.row()
            row.operator("arc.validate_model", text="Validate Model", icon="VIEWZOOM")
            if _validation_warnings:
                row.alert = True

    # ------------------------------------------------------------------
    # 2. Rule Configuration (parent panel)
    # ------------------------------------------------------------------

    class ARC_PT_RuleConfig(Panel):
        bl_label = "Rule Configuration"
        bl_idname = "ARC_PT_rule_config"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "Spatial ARC"
        bl_order = 2

        @classmethod
        def poll(cls, context):
            return _in_section(context, "setup")

        def draw(self, context):
            layout = self.layout
            props = _props(context)
            if props is None:
                layout.label(text="ARC not initialised", icon="ERROR")
                return

            # Preset
            layout.prop(props, "rule_preset")

            # Mode
            layout.separator()
            layout.label(text="Check Mode:", icon="PREFERENCES")
            layout.prop(props, "exec_mode", expand=True)
            if props.exec_mode == "concept":
                layout.label(text="Advisory — tolerant of missing data", icon="INFO")

            # Scope
            layout.separator()
            layout.prop(props, "scope")

            # -- RUN BUTTON (merged from ExecControls) --
            layout.separator()
            if props.is_running:
                col = layout.column(align=True)
                col.label(text=f"Checking: {props.current_rule or '…'}", icon="TIME")
                col.prop(props, "progress", text="Progress", slider=True)
                col.operator("arc.cancel_checks", text="Cancel", icon="X")
            else:
                row = layout.row()
                row.scale_y = 1.8
                row.operator("arc.run_checks", text="▶  Run Model Check", icon="PLAY")

    # ------------------------------------------------------------------
    # 2a. Categories (collapsible sub-panel)
    # ------------------------------------------------------------------

    class ARC_PT_Categories(Panel):
        bl_label = "Rule Categories"
        bl_idname = "ARC_PT_categories"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "Spatial ARC"
        bl_parent_id = "ARC_PT_rule_config"
        bl_order = 0

        @classmethod
        def poll(cls, context):
            return _in_section(context, "setup")

        def draw(self, context):
            layout = self.layout
            props = _props(context)
            if props is None:
                return

            col = layout.column(align=True)
            col.prop(props, "cat_accessibility", toggle=False)
            col.prop(props, "cat_fire",          toggle=False)
            col.prop(props, "cat_spatial",       toggle=False)
            col.prop(props, "cat_parking",       toggle=False)
            col.prop(props, "cat_ventilation",   toggle=False)
            col.prop(props, "cat_safety",        toggle=False)
            row = layout.row(align=True)
            row.operator("arc.enable_all_categories", text="All On")
            row.operator("arc.disable_all_categories", text="All Off")

    # ------------------------------------------------------------------
    # 2b. Rule Selection (collapsible, default closed)
    # ------------------------------------------------------------------

    class ARC_PT_RuleSelection(Panel):
        bl_label = "Individual Rules"
        bl_idname = "ARC_PT_rule_selection"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "Spatial ARC"
        bl_parent_id = "ARC_PT_rule_config"
        bl_options = {"DEFAULT_CLOSED"}
        bl_order = 1

        @classmethod
        def poll(cls, context):
            return _in_section(context, "setup")

        def draw(self, context):
            layout = self.layout
            props = _props(context)
            if props is None:
                return

            layout.prop(props, "rule_search", text="", icon="VIEWZOOM")
            rules = _available_rules(props)
            disabled = _disabled_rule_ids(props)
            enabled_count = sum(1 for rule in rules if str(rule.get("id", "")) not in disabled)
            layout.label(text=f"{enabled_count} of {len(rules)} rules enabled")
            action_row = layout.row(align=True)
            action_row.operator("arc.enable_all_rules", text="Enable All")
            action_row.operator("arc.disable_all_rules", text="Disable All")

            if rules:
                grouped = {}
                for rule in rules:
                    grouped.setdefault(rule.get("category", "general"), []).append(rule)
                query = (props.rule_search or "").strip().lower()
                for category in sorted(grouped.keys()):
                    category_rules = []
                    for rule in grouped[category]:
                        label = " ".join([
                            str(rule.get("id", "")),
                            str(rule.get("title", "")),
                            str(rule.get("category", "")),
                            "python" if rule.get("language") == "python" else "json",
                        ]).lower()
                        if query and query not in label:
                            continue
                        category_rules.append(rule)
                    if not category_rules:
                        continue

                    # Category header
                    box = layout.box()
                    box.label(
                        text=category.replace("_", " ").title(),
                        icon="FILTER",
                    )
                    for rule in category_rules:
                        rid = str(rule.get("id", ""))
                        is_enabled = rid not in disabled
                        row = box.row(align=True)
                        icon = "CHECKBOX_HLT" if is_enabled else "CHECKBOX_DEHLT"
                        op = row.operator(
                            "arc.toggle_rule_enabled",
                            text=_rule_display_name(rule),
                            icon=icon,
                            emboss=True,
                        )
                        op.rule_id = rid
                        # Language badge
                        lang = "PY" if rule.get("language") == "python" else "JSON"
                        sub = row.row()
                        sub.scale_x = 0.4
                        sub.label(text=lang)

    # ------------------------------------------------------------------
    # 2c. Custom Rules Management (collapsible, default closed)
    # ------------------------------------------------------------------

    class ARC_PT_CustomRules(Panel):
        bl_label = "Custom Rules"
        bl_idname = "ARC_PT_custom_rules"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "Spatial ARC"
        bl_parent_id = "ARC_PT_rule_config"
        bl_options = {"DEFAULT_CLOSED"}
        bl_order = 2

        @classmethod
        def poll(cls, context):
            return _in_section(context, "setup")

        def draw(self, context):
            layout = self.layout
            props = _props(context)
            if props is None:
                return

            layout.prop(props, "rules_dir", text="Rules Folder")
            col = layout.column(align=True)
            col.operator("arc.init_user_rules", text="Initialize User Rules", icon="COPYDOWN")
            row = layout.row(align=True)
            row.operator("arc.import_rule_pack", text="Import Pack", icon="IMPORT")
            row.operator("arc.export_rule_pack", text="Export Pack", icon="EXPORT")

    # ------------------------------------------------------------------
    # 2d. Directories (collapsible, default closed)
    # ------------------------------------------------------------------

    class ARC_PT_Directories(Panel):
        bl_label = "Output Directories"
        bl_idname = "ARC_PT_directories"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "Spatial ARC"
        bl_parent_id = "ARC_PT_rule_config"
        bl_options = {"DEFAULT_CLOSED"}
        bl_order = 3

        @classmethod
        def poll(cls, context):
            return _in_section(context, "setup")

        def draw(self, context):
            layout = self.layout
            props = _props(context)
            if props is None:
                return

            layout.label(text="Where to save reports & results:")
            layout.prop(props, "results_dir", text="")
            row = layout.row(align=True)
            row.operator("arc.browse_results_dir", text="Choose Folder", icon="FILE_FOLDER")
            row.operator("arc.load_results", text="Load Previous Run", icon="FILE_REFRESH")

    # ==================================================================
    # REVIEW SECTION
    # ==================================================================

    # ------------------------------------------------------------------
    # 3. Results Dashboard
    # ------------------------------------------------------------------

    class ARC_PT_Results(Panel):
        bl_label = "Results Dashboard"
        bl_idname = "ARC_PT_results"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "Spatial ARC"
        bl_order = 4

        @classmethod
        def poll(cls, context):
            return _in_section(context, "review")

        def draw(self, context):
            layout = self.layout
            props = _props(context)
            if props is None:
                return

            if not props.last_run_ts:
                box = layout.box()
                box.label(text="No results yet", icon="INFO")
                box.label(text="Run a compliance check first.")
                row = box.row()
                row.scale_y = 1.3
                row.operator("arc.run_checks", text="▶  Run Model Check", icon="PLAY")
                return

            layout.label(text=f"Last run: {props.last_run_ts}", icon="TIME")

            total = (props.pass_count + props.fail_count
                     + props.inconcl_count + props.human_req_count
                     + props.not_applic_count + props.unsupported_count)
            if total == 0:
                layout.label(text="No elements were checked.")
                return

            # Summary counts - human-readable
            box = layout.box()
            pct = _compliance_pct(props)
            # Compliance bar header
            header = box.row()
            header.label(text=f"Compliance: {pct:.0f}%", icon="FUND")
            header.label(text=f"{total} total checks")

            col = box.column(align=True)
            col.label(text=f"{props.pass_count} Passed ({props.pass_count * 100 // total}%)", icon="CHECKMARK")
            col.label(text=f"{props.fail_count} Failed ({props.fail_count * 100 // total}%)", icon="CANCEL")
            if props.inconcl_count:
                col.label(text=f"{props.inconcl_count} Inconclusive ({props.inconcl_count * 100 // total}%)", icon="DOT")
            if props.human_req_count:
                col.label(text=f"{props.human_req_count} Human Required ({props.human_req_count * 100 // total}%)", icon="USER")
            if props.not_applic_count:
                col.label(text=f"{props.not_applic_count} Not Applicable ({props.not_applic_count * 100 // total}%)", icon="REMOVE")
            if props.unsupported_count:
                col.label(text=f"{props.unsupported_count} Unsupported ({props.unsupported_count * 100 // total}%)", icon="ERROR")

            # Waiver / deviation summary - only render when there's anything to show
            if (props.waived_fail_count or props.invalid_waiver_count
                    or props.superseded_waiver_count):
                wbox = layout.box()
                wbox.label(text="Waivers & Deviations:", icon="MOD_MASK")
                if props.waived_fail_count:
                    wbox.label(text=f"{props.waived_fail_count} waived FAIL(s)", icon="CHECKMARK")
                if props.invalid_waiver_count:
                    wbox.label(text=f"{props.invalid_waiver_count} invalid waiver(s)", icon="TIME")
                if props.superseded_waiver_count:
                    wbox.label(text=f"{props.superseded_waiver_count} superseded waiver(s)", icon="REMOVE")

            # Aggregate (class/model-scope) summary
            if props.aggregate_count:
                abox = layout.box()
                abox.label(text="Aggregate Findings:", icon="GROUP")
                abox.label(text=f"{props.aggregate_count} class/model-scope result(s)")
                abox.label(text=f"{props.rules_evaluated_count} rules evaluated · "
                                f"{props.rules_skipped_count} skipped")

            layout.separator()

            # Visualization buttons - semantic labels
            viz_box = layout.box()
            viz_box.label(text="Visualization:", icon="RESTRICT_RENDER_OFF")
            viz_row = viz_box.row(align=True)
            viz_row.operator("arc.show_volumes", text="Show Failures", icon="CUBE")
            viz_row.operator("arc.toggle_heatmap", text="Heatmap", icon="COLORSET_13_VEC")
            viz_row.operator("arc.clear_visualization", text="Clear All", icon="TRASH")

            # Inline legend
            leg_box = viz_box.box()
            leg_box.label(text="What you'll see:", icon="INFO")
            col = leg_box.column(align=True)
            col.scale_y = 0.8
            col.label(text="🔴 Red wireframe — Failing element", icon="BLANK1")
            col.label(text="🟠 Orange wireframe — Blocking element", icon="BLANK1")
            col.label(text="🔵 Blue zone — Required clearance area", icon="BLANK1")
            col.label(text="🟣 Purple cylinder — Spatial probe/occupant", icon="BLANK1")
            col.label(text="🟡 Yellow — Measurement points & dimensions", icon="BLANK1")
            col.label(text="📝 Text — Rule explanation label", icon="BLANK1")
            col.label(text="", icon="BLANK1")
            col.label(text="Click any ARC object → see details in Properties", icon="BLANK1")

            layout.separator()

            # Category breakdown from cache
            if _results_cache:
                box = layout.box()
                box.label(text="Results by Category:", icon="LINENUMBERS_ON")
                cat_counts = _category_breakdown()
                for cat, (p, f) in sorted(cat_counts.items()):
                    cat_row = box.row()
                    cat_name = cat.replace("_", " ").title()
                    if f == 0:
                        cat_row.label(text=f"{cat_name}", icon="CHECKMARK")
                        cat_row.label(text=f"All {p} passed")
                    else:
                        cat_row.label(text=f"{cat_name}", icon="CANCEL")
                        cat_row.label(text=f"{f} failed, {p} passed")

    def _category_breakdown():
        """Count pass/fail per category from the global results cache."""
        counts = {}
        for r in _results_cache:
            cat = r.get("category", "general")
            status = r.get("status", "INCONCLUSIVE")
            p, f = counts.get(cat, (0, 0))
            if status == "PASS":
                counts[cat] = (p + 1, f)
            elif status == "FAIL":
                counts[cat] = (p, f + 1)
            else:
                counts[cat] = (p, f)
        return counts

    # ------------------------------------------------------------------
    # 4. Issue Browser
    # ------------------------------------------------------------------

    def _issue_key(result) -> str:
        return f"{result.get('element_id', '')}|{result.get('rule_id', '')}"

    def _failed_results():
        severity_rank = {"critical": 0, "major": 1, "minor": 2}
        failures = [r for r in _results_cache if r.get("status") == "FAIL"]
        return sorted(
            failures,
            key=lambda r: (
                severity_rank.get(r.get("severity", "major"), 99),
                r.get("rule_id", ""),
                r.get("element_id", ""),
            ),
        )

    def _filtered_failed_results(props) -> list:
        failures = _failed_results()
        query = (props.issue_search or "").strip().lower() if props else ""
        if not query:
            return failures
        filtered = []
        for result in failures:
            haystack = " ".join([
                str(result.get("rule_id", "")),
                str(result.get("element_id", "")),
                str(result.get("message", "")),
                str(result.get("category", "")),
                str(result.get("severity", "")),
            ]).lower()
            if query in haystack:
                filtered.append(result)
        return filtered

    def _selected_issue(props):
        key = getattr(props, "selected_issue_key", "") if props else ""
        if not key:
            return None
        for result in _results_cache:
            if _issue_key(result) == key:
                return result
        return None

    def _draw_result_details(layout, result, show_actions: bool = True):
        details = result.get("details", {}) or {}
        rule_name = _rule_display_name(result)
        layout.label(text=f"Rule: {rule_name}", icon="DOT")

        sev = result.get("severity", "major")
        sev_icon = "ERROR" if sev == "critical" else "INFO"
        layout.label(text=f"Severity: {sev.title()}", icon=sev_icon)
        layout.label(text=f"Category: {result.get('category', 'general').replace('_', ' ').title()}")

        msg = str(result.get("message", ""))
        if msg:
            for chunk in [msg[i:i+56] for i in range(0, min(len(msg), 168), 56)]:
                layout.label(text=chunk, icon="BLANK1")
        meas = result.get("measured_value")
        exp = result.get("expected_value")
        if meas is not None and exp is not None:
            layout.label(text=f"Measured: {meas:.3f}  |  Required: {exp:.3f}")
            delta = result.get("delta")
            if delta is not None:
                layout.label(text=f"Delta: {delta:+.3f}")
        blockers = details.get("blocking_elements") or []
        if blockers:
            layout.label(text=f"Blockers: {', '.join(str(v) for v in blockers[:4])}", icon="ERROR")
        path = details.get("path") or []
        if path:
            layout.label(text=f"Route: {' → '.join(str(v) for v in path[:4])}", icon="SORTTIME")
        method = details.get("measurement_method")
        if method:
            layout.label(text=f"Method: {method}", icon="INFO")
        suggestion = details.get("suggestion")
        if suggestion:
            layout.label(text=f"Fix: {str(suggestion)[:72]}", icon="LIGHT")
        if show_actions:
            row = layout.row(align=True)
            op = row.operator("arc.inspect_issue", text="Inspect", icon="RESTRICT_SELECT_OFF")
            op.guid = result.get("element_id", "")
            op.rule_id = result.get("rule_id", "")
            op = row.operator("arc.show_rule_volume", text="Show", icon="CUBE")
            op.guid = result.get("element_id", "")
            op.rule_id = result.get("rule_id", "")
            op = row.operator("arc.isolate_issue", text="Isolate", icon="LIGHT_SUN")
            op.guid = result.get("element_id", "")
            op.rule_id = result.get("rule_id", "")
            op = row.operator("arc.create_bcf_issue", text="BCF", icon="EXPORT")
            op.guid = result.get("element_id", "")
            op.rule_id = result.get("rule_id", "")

    class ARC_PT_IssueBrowser(Panel):
        bl_label = "Issue Browser"
        bl_idname = "ARC_PT_issue_browser"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "Spatial ARC"
        bl_order = 5

        @classmethod
        def poll(cls, context):
            return _in_section(context, "review")

        def draw(self, context):
            layout = self.layout
            props = _props(context)
            if props is None or not props.last_run_ts:
                box = layout.box()
                box.label(text="Run a check to browse issues", icon="INFO")
                row = box.row()
                row.scale_y = 1.3
                row.operator("arc.run_checks", text="▶  Run Model Check", icon="PLAY")
                return
            if not _results_cache:
                layout.label(text="No results cached", icon="INFO")
                return

            layout.prop(props, "issue_search", text="", icon="VIEWZOOM")
            failures = _filtered_failed_results(props)
            if not failures:
                layout.label(text="No failed issues found!", icon="CHECKMARK")
                if props.issue_search:
                    layout.label(text="Try clearing the search filter.", icon="INFO")
                return

            layout.label(text=f"{len(failures)} failed issue(s):", icon="CANCEL")

            selected = _selected_issue(props)
            if selected is None:
                selected = failures[0]

            list_box = layout.box()
            for result in failures[:12]:
                row = list_box.row(align=True)
                is_selected = selected and _issue_key(result) == _issue_key(selected)
                icon = "RADIOBUT_ON" if is_selected else "RADIOBUT_OFF"

                # Show rule title + element ID
                rule_name = _rule_display_name(result)
                eid = str(result.get("element_id", "?"))
                if len(eid) > 12:
                    eid = eid[:10] + "…"
                row.label(text=f"{rule_name}", icon=icon)

                sev = result.get("severity", "major")
                sev_label = "!" if sev == "critical" else ""
                sub = row.row()
                sub.scale_x = 0.3
                if sev_label:
                    sub.label(text=sev_label, icon="ERROR")

                review = _review_for_issue(props, _issue_key(result))
                if review.get("status"):
                    sub2 = row.row()
                    sub2.scale_x = 0.35
                    sub2.label(text="", icon="BOOKMARKS")

                op = row.operator("arc.inspect_issue", text="", icon="RESTRICT_SELECT_OFF")
                op.guid = result.get("element_id", "")
                op.rule_id = result.get("rule_id", "")
                op = row.operator("arc.show_rule_volume", text="", icon="CUBE")
                op.guid = result.get("element_id", "")
                op.rule_id = result.get("rule_id", "")
                op = row.operator("arc.isolate_issue", text="", icon="LIGHT_SUN")
                op.guid = result.get("element_id", "")
                op.rule_id = result.get("rule_id", "")

            if len(failures) > 12:
                list_box.label(text=f"… and {len(failures) - 12} more", icon="DOT")

            # Selected issue detail
            detail = layout.box()
            detail.label(text="Selected Issue", icon="MENU_PANEL")
            eid_full = selected.get("element_id", "?")
            detail.label(text=f"Element: {eid_full}", icon="OBJECT_DATA")
            _draw_result_details(detail, selected, show_actions=True)
            if props.selected_issue_key == _issue_key(selected):
                detail.separator()
                detail.label(text="Review Notes", icon="GREASEPENCIL")
                detail.prop(props, "review_status")
                detail.prop(props, "review_assignee")
                detail.prop(props, "review_note")
                review_row = detail.row(align=True)
                review_row.operator("arc.save_issue_review", text="Save Review", icon="FILE_TICK")
                review_row.operator("arc.clear_issue_review", text="Clear Review", icon="TRASH")
            else:
                detail.label(text="Click Inspect to pin issue for review notes.", icon="INFO")

    # ------------------------------------------------------------------
    # 5. Element Detail (always visible on Review tab)
    # ------------------------------------------------------------------

    class ARC_PT_ElementDetail(Panel):
        bl_label = "Element Detail"
        bl_idname = "ARC_PT_element_detail"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "Spatial ARC"
        bl_order = 6

        @classmethod
        def poll(cls, context):
            # Always visible on Review tab - shows guidance when nothing selected
            return _in_section(context, "review")

        def draw(self, context):
            layout = self.layout
            props = _props(context)
            obj = context.active_object
            if obj is None:
                box = layout.box()
                box.label(text="No object selected", icon="INFO")
                box.label(text="Select an element in the viewport")
                box.label(text="to see its compliance details.")
                return

            # Try to get GUID
            guid = _get_object_guid(obj)
            if not guid:
                layout.label(text="Not an IFC element", icon="INFO")
                layout.label(text="Select an imported IFC object.")
                return

            layout.label(text=f"{obj.name}", icon="OBJECT_DATA")
            guid_display = f"{guid[:20]}…" if len(guid) > 20 else guid
            layout.label(text=f"GUID: {guid_display}", icon="BLANK1")

            if not _results_cache:
                layout.label(text="Run a check to see results", icon="INFO")
                return

            # Find results for this GUID
            el_results = [r for r in _results_cache if r.get("element_id") == guid]
            if not el_results:
                layout.label(text="No results for this element", icon="CHECKMARK")
                return

            failed = [r for r in el_results if r.get("status") == "FAIL"]
            passed = len(el_results) - len(failed)
            if not failed:
                layout.label(text=f"All {len(el_results)} checks passed", icon="CHECKMARK")
                return

            layout.label(text=f"{len(failed)} failed, {passed} passed", icon="CANCEL")
            box = layout.box()
            for r in failed[:6]:
                sub = box.box()
                _draw_result_details(sub, r, show_actions=True)
            if len(failed) > 6:
                box.label(text=f"… and {len(failed) - 6} more", icon="DOT")

    def _get_object_guid(obj) -> str:
        """Try multiple Bonsai API paths to get an object's IFC GUID."""
        # Bonsai >= 0.8
        try:
            import bonsai.tool as tool  # type: ignore
            entity = tool.Ifc.get_entity(obj)
            if entity:
                return entity.GlobalId
        except Exception:
            pass
        # Legacy BlenderBIM
        try:
            ifc_id = obj.BIMObjectProperties.ifc_definition_id
            if ifc_id:
                from . import ifc_integration  # type: ignore
                model = ifc_integration._get_bonsai_ifc_model()
                if model:
                    return model.by_id(ifc_id).GlobalId
        except Exception:
            pass
        # Custom property fallback
        return obj.get("GlobalId", "") or obj.get("IFC_guid", "")

    # ==================================================================
    # EXPORT SECTION
    # ==================================================================

    class ARC_PT_Export(Panel):
        bl_label = "Export & Reports"
        bl_idname = "ARC_PT_export"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "Spatial ARC"
        bl_order = 7

        @classmethod
        def poll(cls, context):
            return _in_section(context, "export")

        def draw(self, context):
            layout = self.layout
            props = _props(context)

            if not (props and props.last_run_ts):
                box = layout.box()
                box.label(text="No results to export", icon="INFO")
                box.label(text="Run a compliance check first.")
                row = box.row()
                row.scale_y = 1.3
                row.operator("arc.run_checks", text="▶  Run Model Check", icon="PLAY")
                return

            pct = _compliance_pct(props)
            layout.label(text=f"Last run: {props.last_run_ts}  ({pct:.0f}% compliant)", icon="TIME")

            layout.separator()
            layout.label(text="Export Audit Reports:", icon="FILE")
            col = layout.column(align=True)
            col.operator("arc.export_bcf",  text="BCF Issue Archive (.bcfzip)", icon="EXPORT")
            col.operator("arc.export_pdf",  text="PDF Audit Report (.pdf)", icon="FILE_TEXT")
            col.operator("arc.export_html", text="HTML Dashboard (.html)", icon="FILEBROWSER")
            col.operator("arc.export_json", text="JSON Data (.json)", icon="FILE")

            layout.separator()
            layout.label(text="Review Log:", icon="TEXT")
            layout.operator("arc.export_review_log", text="Export Review Notes (.json)", icon="TEXT")

    # ------------------------------------------------------------------
    # Class registration list
    # ------------------------------------------------------------------

    _PANEL_CLASSES = [
        ARC_Props,
        ARC_PT_Main,
        ARC_PT_Help,
        ARC_PT_ModelStatus,
        ARC_PT_RuleConfig,
        ARC_PT_Categories,
        ARC_PT_RuleSelection,
        ARC_PT_CustomRules,
        ARC_PT_Directories,
        ARC_PT_Results,
        ARC_PT_IssueBrowser,
        ARC_PT_ElementDetail,
        ARC_PT_Export,
    ]

else:
    # Headless - expose None so __init__.py can safely check
    ARC_Props = None
    _PANEL_CLASSES = []
