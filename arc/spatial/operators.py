# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender operators for Spatial ARC — Blender 4.2+ (tested on 4.5 LTS and 5.1).

Operators:
  arc.run_checks          — Modal operator: load elements, run rules in chunks
  arc.cancel_checks       — Cancel a running check
  arc.validate_model      — Pre-check IFC model quality
  arc.show_volumes        — Display compliance volumes in viewport
  arc.toggle_heatmap      — Colour-code model by compliance status
  arc.clear_visualization — Remove all ARC mesh objects and material overrides
  arc.export_bcf          — Write .bcfzip archive
  arc.export_pdf          — Write PDF audit report
  arc.export_html         — Write HTML audit report
  arc.export_json         — Write JSON results
  arc.zoom_to_element     — Focus viewport on a specific element by GUID
  arc.create_bcf_issue    — Create a single BCF issue for one element+rule
"""
from __future__ import annotations

import json
import time
from pathlib import Path

try:
    import bpy
    from bpy.types import Operator
    from bpy.props import StringProperty
    _BPY = True
except ImportError:
    _BPY = False


def _extract_results_and_metadata(payload):
    """Normalize saved ARC payloads into a result list plus file metadata."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)], {}
    if not isinstance(payload, dict):
        return [], {}

    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    if isinstance(payload.get("element_results"), list):
        results = [r for r in payload["element_results"] if isinstance(r, dict)]
        return results, metadata
    if payload.get("rule_id") and payload.get("status"):
        return [payload], metadata
    return [], metadata

if _BPY:
    from . import ui_panel as _ui  # results cache lives here
    _HEATMAP_COLLECTION_NAME = "ARC_Heatmap_Overlay"
    _ISOLATE_DIM_MAT_NAME = "ARC_Isolate_Dim"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_props(context):
        try:
            return context.scene.arc
        except Exception:
            return None

    def _default_output_dir() -> Path:
        try:
            if bpy.data.is_saved:
                base = Path(bpy.path.abspath("//"))
            else:
                # Prefer Blender's per-user config directory when the file isn't saved
                base = Path(bpy.utils.user_resource("CONFIG"))
        except Exception:
            base = Path.cwd()

        d = base / "arc_results"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _ts_filename(prefix: str, suffix: str) -> str:
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{prefix}_{ts}{suffix}"

    def _build_context_from_scene(scope: str):
        from .ifc_integration import (
            load_from_blender_scene, validate_model, blender_project_identity,
        )
        from ..core.context import Context
        elements = load_from_blender_scene(scope=scope)
        _ui._validation_warnings = validate_model(elements).get("warnings", [])
        ident = blender_project_identity()
        ctx = Context(
            elements,
            project_id=ident["project_id"],
            model_source=ident["model_source"],
        )
        return ctx, elements

    def _visualization_dir(props) -> Path | None:
        if props and getattr(props, "results_dir", ""):
            return Path(props.results_dir) / "volumes"
        return None

    def _clear_heatmap_materials(context) -> None:
        """Remove the heatmap overlay collection and all its objects."""
        coll = bpy.data.collections.get(_HEATMAP_COLLECTION_NAME)
        if coll:
            for ob in list(coll.objects):
                mesh_data = getattr(ob, "data", None)
                bpy.data.objects.remove(ob, do_unlink=True)
                try:
                    if mesh_data and hasattr(mesh_data, "users") and mesh_data.users == 0:
                        bpy.data.meshes.remove(mesh_data)
                except Exception:
                    pass
            try:
                bpy.data.collections.remove(coll)
            except Exception:
                pass

        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()

    def _get_or_create_heatmap_collection():
        coll = bpy.data.collections.get(_HEATMAP_COLLECTION_NAME)
        if coll is None:
            coll = bpy.data.collections.new(_HEATMAP_COLLECTION_NAME)
            bpy.context.scene.collection.children.link(coll)
        return coll

    def _toggle_heatmap_visibility(visible: bool) -> None:
        """Show or hide the heatmap overlay collection without creating/deleting."""
        coll = bpy.data.collections.get(_HEATMAP_COLLECTION_NAME)
        if coll is None:
            return
        # Hide or show via viewport visibility
        try:
            layer_coll = bpy.context.view_layer.layer_collection.children.get(_HEATMAP_COLLECTION_NAME)
            if layer_coll:
                layer_coll.exclude = not visible
        except Exception:
            # Fallback: hide each object individually
            for ob in coll.objects:
                ob.hide_viewport = not visible
                ob.hide_render = not visible

    def _heatmap_collection_exists() -> bool:
        return bpy.data.collections.get(_HEATMAP_COLLECTION_NAME) is not None

    def _heatmap_collection_is_visible() -> bool:
        try:
            layer_coll = bpy.context.view_layer.layer_collection.children.get(_HEATMAP_COLLECTION_NAME)
            if layer_coll:
                return not layer_coll.exclude
        except Exception:
            pass
        coll = bpy.data.collections.get(_HEATMAP_COLLECTION_NAME)
        if coll and coll.objects:
            return not coll.objects[0].hide_viewport
        return False

    _HEATMAP_COLOURS = {
        "PASS":           (0.18, 0.80, 0.44, 0.7),   # green
        "FAIL":           (0.91, 0.30, 0.24, 0.7),   # red
        "BLOCKER":        (0.95, 0.55, 0.12, 0.7),   # orange (not a status, used as a tag)
        "INCONCLUSIVE":   (0.95, 0.77, 0.06, 0.7),   # yellow
        "HUMAN_REQUIRED": (0.49, 0.23, 0.93, 0.7),   # purple
        "NOT_APPLICABLE": (0.61, 0.64, 0.69, 0.7),   # grey
        "UNSUPPORTED":    (0.92, 0.35, 0.05, 0.7),   # orange-red
    }
    _HEATMAP_MAT_PREFIX = "ARC_Heatmap_"

    def _get_or_create_heatmap_mat(status: str):
        name = f"{_HEATMAP_MAT_PREFIX}{status}"
        mat = bpy.data.materials.get(name)
        if mat is None:
            mat = bpy.data.materials.new(name=name)
            mat.use_nodes = True
            from .visualizer import _set_alpha_blend
            _set_alpha_blend(mat)
            r, g, b, a = _HEATMAP_COLOURS.get(status, (0.5, 0.5, 0.5, 0.5))
            mat.diffuse_color = (r, g, b, a)
            bsdf = mat.node_tree.nodes.get("Principled BSDF")
            if bsdf:
                bsdf.inputs["Base Color"].default_value = (r, g, b, 1.0)
                bsdf.inputs["Alpha"].default_value = a
                try:
                    bsdf.inputs["Emission Color"].default_value = (r, g, b, 1.0)
                    bsdf.inputs["Emission Strength"].default_value = 0.15
                except Exception:
                    pass
        return mat

    def _build_heatmap_generator(context):
        """Yielding generator to build the duplicate-mesh overlay collection.
        Original objects are never touched. The overlay collection can
        then be toggled on/off via visibility — no rebuild needed.
        """
        if not _ui._results_cache:
            return
        # Remove any stale overlay first
        _clear_heatmap_materials(context)

        # Build worst-status per GUID
        worst: dict = {}
        # Worst-status priority - higher wins when one element has multiple findings.
        # BLOCKER is a tag for blocking elements (not a status), kept above PASS so
        # a blocking element shows as orange rather than green in the heatmap.
        priority = {
            "FAIL": 6, "UNSUPPORTED": 5, "HUMAN_REQUIRED": 4,
            "INCONCLUSIVE": 3, "NOT_APPLICABLE": 2, "BLOCKER": 1, "PASS": 0,
        }
        blocker_counts: dict = {}
        for r in _ui._results_cache:
            guid = r.get("element_id", "")
            status = r.get("status", "INCONCLUSIVE")
            cur = worst.get(guid, "PASS")
            if priority.get(status, 0) > priority.get(cur, 0):
                worst[guid] = status
            details = r.get("details", {}) or {}
            blockers = details.get("blocking_elements")
            if isinstance(blockers, list):
                for blocker_guid in blockers:
                    blocker_counts[blocker_guid] = blocker_counts.get(blocker_guid, 0) + 1

        for blocker_guid in blocker_counts:
            if priority.get(worst.get(blocker_guid, "PASS"), 0) < priority["BLOCKER"]:
                worst[blocker_guid] = "BLOCKER"

        overlay_coll = _get_or_create_heatmap_collection()

        objs_to_process = [obj for obj in context.scene.objects if obj.type == "MESH" and not obj.get("ARC_heatmap_overlay") and not obj.get("ARC_managed")]

        start_time = time.monotonic()
        for idx, obj in enumerate(objs_to_process):
            from .ui_panel import _get_object_guid
            guid = _get_object_guid(obj)
            if not guid:
                continue
            status = worst.get(guid, "PASS")
            if status == "PASS":
                continue

            mat = _get_or_create_heatmap_mat(status)
            r, g, b, a = _HEATMAP_COLOURS.get(status, (0.5, 0.5, 0.5, 0.5))
            try:
                overlay = obj.copy()
                overlay.data = obj.data.copy()
                overlay.name = f"{_HEATMAP_MAT_PREFIX}{status}_{obj.name}"
                overlay.hide_select = True
                overlay.color = (r, g, b, a)
                overlay["ARC_heatmap_overlay"] = True
                overlay["ARC_source_guid"] = guid
                overlay["ARC_heatmap_status"] = status

                for col in list(overlay.users_collection):
                    col.objects.unlink(overlay)
                overlay_coll.objects.link(overlay)

                # Apply the world matrix after linking to the collection so the
                # unparented duplicate resolves to the correct world position.
                overlay.matrix_world = obj.matrix_world.copy()

                if overlay.data.materials:
                    for midx in range(len(overlay.data.materials)):
                        overlay.data.materials[midx] = mat
                else:
                    overlay.data.materials.append(mat)
                overlay.active_material = mat
                try:
                    overlay.show_in_front = True
                except Exception:
                    pass
            except Exception:
                pass

            # Yield every 16ms to avoid freezing the UI
            if (time.monotonic() - start_time) * 1000 > 16.0:
                yield
                start_time = time.monotonic()

        # Hide it cleanly once finished pre-building
        _toggle_heatmap_visibility(False)
        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()

    def _has_rule_content(directory: Path) -> bool:
        """Check whether a rules directory actually contains rule files."""
        if not directory.exists():
            return False
        json_dir = directory / "json_rules"
        py_dir = directory / "python_rules"
        has_json = json_dir.exists() and any(json_dir.glob("*.json"))
        has_py = py_dir.exists() and any(py_dir.glob("*.py"))
        return has_json or has_py

    def _load_active_rules(props):
        """Return rule list filtered by active categories."""
        from ..core.rule_loader import load_rules

        packaged_rules = Path(__file__).resolve().parent.parent / "core" / "rules"

        # Prefer a user-writable rules directory when available. The installer
        # can copy packaged rules into a per-user location so non-admin users
        # can edit and add rules without touching the add-on package.
        rules_base = None
        try:
            # If user explicitly set a rules dir in UI props, prefer that
            if props and getattr(props, "rules_dir", ""):
                cand = Path(props.rules_dir)
                if _has_rule_content(cand):
                    rules_base = cand
                else:
                    # Attempt to create and populate it
                    try:
                        from .install import ensure_user_rules
                        populated = ensure_user_rules(cand)
                        if _has_rule_content(populated):
                            rules_base = populated
                    except Exception:
                        pass

            # If no explicit setting, use default per-user rules location if present
            if rules_base is None:
                try:
                    from .install import get_default_user_rules_dir, ensure_user_rules

                    user_default = get_default_user_rules_dir()
                    if _has_rule_content(user_default):
                        rules_base = user_default
                    else:
                        # Try to initialise the default user rules dir by copying packaged rules
                        populated = ensure_user_rules(user_default)
                        if _has_rule_content(populated):
                            rules_base = populated
                except Exception:
                    pass

        except Exception:
            pass

        # Fallback to packaged rules inside the add-on
        if rules_base is None:
            rules_base = packaged_rules

        all_rules = list(load_rules(str(rules_base)))

        # If user/custom dir yielded nothing, always try packaged rules as last resort
        if not all_rules and rules_base != packaged_rules:
            print(f"ARC: No rules found in {rules_base}, falling back to packaged rules")
            rules_base = packaged_rules
            all_rules = list(load_rules(str(rules_base)))

        cat_map = {
            "accessibility": props.cat_accessibility,
            "fire_egress":   props.cat_fire,
            "fire":          props.cat_fire,
            "spatial":       props.cat_spatial,
            "parking":       props.cat_parking,
            "ventilation":   props.cat_ventilation,
            "safety":        props.cat_safety,
        }

        enabled_categories = {name for name, enabled in cat_map.items() if enabled}
        if not enabled_categories:
            return []
        disabled_rule_ids = _get_disabled_rule_ids(props)

        filtered = [
            r for r in all_rules
            if r.get("category", "") in enabled_categories and str(r.get("id", "")) not in disabled_rule_ids
        ]
        return filtered

    def _focus_object_by_guid(context, guid: str):
        from .ui_panel import _get_object_guid
        target = None
        for obj in context.scene.objects:
            if _get_object_guid(obj) == guid:
                target = obj
                break
        if target is None:
            return None
        bpy.ops.object.select_all(action="DESELECT")
        target.select_set(True)
        context.view_layer.objects.active = target
        try:
            bpy.ops.view3d.view_selected(use_all_regions=False)
        except Exception:
            pass
        return target

    def _get_disabled_rule_ids(props) -> set[str]:
        if props is None:
            return set()
        try:
            data = json.loads(props.disabled_rule_ids_json or "[]")
            return {str(item) for item in data if isinstance(item, str)}
        except Exception:
            return set()

    def _set_disabled_rule_ids(props, disabled_ids) -> None:
        if props is None:
            return
        props.disabled_rule_ids_json = json.dumps(sorted(str(item) for item in disabled_ids))

    def _get_all_rules_for_props(props) -> list[dict]:
        from ..core.rule_loader import load_rules

        packaged_rules = Path(__file__).resolve().parent.parent / "core" / "rules"
        rules_base = None
        if props and getattr(props, "rules_dir", ""):
            cand = Path(props.rules_dir)
            if _has_rule_content(cand):
                rules_base = cand
        if rules_base is None:
            rules_base = packaged_rules

        all_rules = list(load_rules(str(rules_base)))
        if not all_rules and rules_base != packaged_rules:
            all_rules = list(load_rules(str(packaged_rules)))
        return all_rules

    def _get_review_map(props) -> dict:
        if props is None:
            return {}
        try:
            data = json.loads(props.review_log_json or "{}")
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _set_review_map(props, review_map: dict) -> None:
        if props is None:
            return
        props.review_log_json = json.dumps(review_map, indent=2)

    def _load_review_fields(props, issue_key: str) -> None:
        review = _get_review_map(props).get(issue_key, {}) if props is not None else {}
        if props is None:
            return
        props.review_status = str(review.get("status", "open"))
        props.review_assignee = str(review.get("assignee", ""))
        props.review_note = str(review.get("note", ""))

    # ------------------------------------------------------------------
    # Run Checks - Modal operator with chunked execution
    # ------------------------------------------------------------------

    class ARC_OT_RunChecks(Operator):
        bl_idname = "arc.run_checks"
        bl_label = "Run Model Check"
        bl_description = "Execute compliance rules against the loaded IFC model"
        bl_options = {"REGISTER"}

        _timer = None
        _gen = None
        _all_results = []
        _total_work = 0
        _done_work = 0
        _cancelled = False

        def invoke(self, context, event):
            return self.execute(context)

        def execute(self, context):
            props = _get_props(context)
            if props is None:
                self.report({"ERROR"}, "ARC not initialised")
                return {"CANCELLED"}

            props.is_running = True
            props.progress = 0.0
            props.current_rule = "Loading elements…"
            _ui._results_cache.clear()
            _ui._summary_cache.clear()

            self._cancelled = False
            self._all_results = []
            self._done_work = 0
            self._gen = None

            # Start the generator in invoke to keep execute() fast
            try:
                self._gen = self._run_generator(context)
            except Exception as exc:
                props.is_running = False
                self.report({"ERROR"}, f"ARC failed to start: {exc}")
                return {"CANCELLED"}

            wm = context.window_manager
            self._timer = wm.event_timer_add(0.016, window=context.window)
            wm.modal_handler_add(self)
            return {"RUNNING_MODAL"}

        def modal(self, context, event):
            props = _get_props(context)

            if self._cancelled or event.type == "ESC":
                self._finish(context, cancelled=True)
                return {"CANCELLED"}

            if event.type == "TIMER":
                try:
                    next(self._gen)
                except StopIteration:
                    self._finish(context, cancelled=False)
                    return {"FINISHED"}
                except Exception as exc:
                    self._finish(context, cancelled=True)
                    self.report({"ERROR"}, f"ARC execution error: {exc}")
                    return {"CANCELLED"}

                # Redraw sidebar
                for area in context.screen.areas:
                    if area.type == "VIEW_3D":
                        area.tag_redraw()
                        break

            return {"PASS_THROUGH"}

        def _run_generator(self, context):
            """Generator that processes work in 16ms chunks, yielding between."""
            from ..core.rule_engine import RuleEngine
            from ..core.data_models import build_model_summary, ModelSummary
            from datetime import datetime, timezone

            props = _get_props(context)

            # --- Load elements ---
            scope = props.scope if props else "entire_model"
            ctx, elements = _build_context_from_scene(scope)
            props.elements_loaded = len(elements)
            props.current_rule = f"Loaded {len(elements)} elements"
            yield  # allow UI update

            if not elements:
                self.report({"WARNING"}, "No IFC elements found. Import an IFC file via Bonsai.")
                return

            # --- Load rules ---
            rules = _load_active_rules(props)
            if not rules:
                self.report({"WARNING"}, "No rules enabled.")
                return

            engine = RuleEngine(rules, execution_stage=props.exec_mode if props else None)
            self._total_work = len(elements) * len(rules)

            # --- Execute per rule with time-budgeted chunks ---
            CHUNK_MS = 16
            results = []
            all_coverage_gaps = []
            for rule_idx, rule in enumerate(rules):
                rid = rule.get("id", "<unnamed>")
                props.current_rule = rid
                yield  # UI update per rule

                rule_start = time.monotonic()
                # Execute single rule
                try:
                    single_engine = RuleEngine([rule], execution_stage=props.exec_mode if props else None)
                    rule_results = single_engine.execute(ctx)
                    results.extend(rule_results)
                    all_coverage_gaps.extend(single_engine.coverage_gaps)
                except Exception:
                    pass

                self._done_work += len(elements)
                props.progress = (rule_idx + 1) / len(rules)

                elapsed_ms = (time.monotonic() - rule_start) * 1000
                if elapsed_ms > CHUNK_MS:
                    yield  # yield after expensive rules

            # --- Store results ---
            _ui._results_cache[:] = [r.to_dict() for r in results]
            _ui._run_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            _ui._run_ifc_name = "Scene"

            # Update summary props
            from ..core.data_models import (
                STATUS_PASS, STATUS_FAIL, STATUS_INCONCLUSIVE,
                STATUS_HUMAN_REQUIRED, STATUS_NOT_APPLICABLE, STATUS_UNSUPPORTED,
            )
            # Pair-axis counts exclude class/model-scope results so the
            # percentages match the historical pair-axis denominator. Aggregate
            # rows are surfaced via the dedicated aggregate_count prop below.
            element_results = [r for r in results if r.scope == "element"]
            aggregate_results = [r for r in results if r.scope in ("class", "model")]
            props.pass_count          = sum(1 for r in element_results if r.status == STATUS_PASS)
            props.fail_count          = sum(1 for r in element_results if r.status == STATUS_FAIL)
            props.inconcl_count       = sum(1 for r in element_results if r.status == STATUS_INCONCLUSIVE)
            props.human_req_count     = sum(1 for r in element_results if r.status == STATUS_HUMAN_REQUIRED)
            props.not_applic_count    = sum(1 for r in element_results if r.status == STATUS_NOT_APPLICABLE)
            props.unsupported_count   = sum(1 for r in element_results if r.status == STATUS_UNSUPPORTED)
            props.total_checks        = len(results)

            # Routing / deviation summary
            props.waived_fail_count        = sum(1 for r in results if r.waiver_state == "applied")
            props.invalid_waiver_count     = sum(1 for r in results if r.waiver_state == "invalid")
            props.superseded_waiver_count  = sum(1 for r in results if r.waiver_state == "superseded")
            props.aggregate_count       = len(aggregate_results)
            evaluated_rule_ids = {r.rule_id for r in element_results}
            props.rules_evaluated_count = len(evaluated_rule_ids)
            props.rules_skipped_count   = max(len(rules) - len(evaluated_rule_ids), 0)

            props.last_run_ts   = _ui._run_timestamp

            # Persist a timestamped JSON report so saved runs can be reloaded later.
            try:
                from ..core.report_generator import generate_json_report

                out_dir = Path(props.results_dir) if getattr(props, "results_dir", "") else _default_output_dir()
                out_dir.mkdir(parents=True, exist_ok=True)
                if not getattr(props, "results_dir", ""):
                    props.results_dir = str(out_dir)

                current_ifc = None
                try:
                    current_ifc = bpy.context.scene.BIMProperties.ifc_file
                except Exception:
                    current_ifc = None

                generate_json_report(
                    _ui._results_cache,
                    str(out_dir / _ts_filename("arc_results", ".json")),
                    metadata={
                        "model_path": str(current_ifc) if current_ifc else None,
                        "model_name": Path(str(current_ifc)).name if current_ifc else "Scene",
                        "mode": props.exec_mode,
                        "scope": props.scope,
                        "rule_preset": props.rule_preset,
                    },
                    coverage_gaps=[g.to_dict() for g in all_coverage_gaps] if all_coverage_gaps else None,
                )
            except Exception:
                pass

            # --- Pre-build heatmap ---
            props.current_rule = "Building Heatmap..."
            props.progress = 0.95
            yield

            try:
                for _ in _build_heatmap_generator(context):
                    yield
            except Exception as e:
                print(f"ARC Heatmap prebuild failed: {e}")

        def _finish(self, context, cancelled: bool):
            wm = context.window_manager
            if self._timer:
                wm.event_timer_remove(self._timer)
                self._timer = None
            props = _get_props(context)
            if props:
                props.is_running = False
                props.progress = 0.0
                props.current_rule = ""
            if not cancelled:
                total = len(_ui._results_cache)
                fails = sum(1 for r in _ui._results_cache if r.get("status") == "FAIL")
                self.report(
                    {"INFO"},
                    f"Spatial ARC: {total} checks complete. {fails} failures. "
                    "See N-Panel for details.",
                )
            else:
                self.report({"WARNING"}, "Spatial ARC: check cancelled.")

        def cancel(self, context):
            self._cancelled = True
            self._finish(context, cancelled=True)

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    class ARC_OT_CancelChecks(Operator):
        bl_idname = "arc.cancel_checks"
        bl_label = "Cancel Checks"
        bl_description = "Cancel the currently running compliance check"

        def execute(self, context):
            # Signal cancel - the modal operator checks _cancelled on next TIMER
            for op in context.window_manager.operators:
                if op.bl_idname == "arc.run_checks":
                    op._cancelled = True
                    break
            return {"FINISHED"}

    class ARC_OT_EnableAllCategories(Operator):
        bl_idname = "arc.enable_all_categories"
        bl_label = "Enable All Categories"
        bl_description = "Turn on all rule categories"

        def execute(self, context):
            props = _get_props(context)
            if props is None:
                return {"CANCELLED"}
            props.cat_accessibility = True
            props.cat_fire = True
            props.cat_spatial = True
            props.cat_parking = True
            props.cat_ventilation = True
            props.cat_safety = True
            return {"FINISHED"}

    class ARC_OT_DisableAllCategories(Operator):
        bl_idname = "arc.disable_all_categories"
        bl_label = "Disable All Categories"
        bl_description = "Turn off all rule categories so no checks are run"

        def execute(self, context):
            props = _get_props(context)
            if props is None:
                return {"CANCELLED"}
            props.cat_accessibility = False
            props.cat_fire = False
            props.cat_spatial = False
            props.cat_parking = False
            props.cat_ventilation = False
            props.cat_safety = False
            return {"FINISHED"}

    class ARC_OT_ToggleRuleEnabled(Operator):
        bl_idname = "arc.toggle_rule_enabled"
        bl_label = "Toggle Rule"
        bl_description = "Enable or disable this individual rule"

        rule_id: StringProperty(default="")

        def execute(self, context):
            props = _get_props(context)
            if props is None or not self.rule_id:
                return {"CANCELLED"}
            disabled = _get_disabled_rule_ids(props)
            if self.rule_id in disabled:
                disabled.remove(self.rule_id)
            else:
                disabled.add(self.rule_id)
            _set_disabled_rule_ids(props, disabled)
            return {"FINISHED"}

    class ARC_OT_EnableAllRules(Operator):
        bl_idname = "arc.enable_all_rules"
        bl_label = "Enable All Rules"
        bl_description = "Enable every currently available rule"

        def execute(self, context):
            props = _get_props(context)
            if props is None:
                return {"CANCELLED"}
            _set_disabled_rule_ids(props, set())
            return {"FINISHED"}

    class ARC_OT_DisableAllRules(Operator):
        bl_idname = "arc.disable_all_rules"
        bl_label = "Disable All Rules"
        bl_description = "Disable every currently available rule"

        def execute(self, context):
            props = _get_props(context)
            if props is None:
                return {"CANCELLED"}
            rule_ids = {str(rule.get("id", "")) for rule in _get_all_rules_for_props(props) if rule.get("id")}
            _set_disabled_rule_ids(props, rule_ids)
            return {"FINISHED"}

    # ------------------------------------------------------------------
    # Validate Model
    # ------------------------------------------------------------------

    class ARC_OT_ValidateModel(Operator):
        bl_idname = "arc.validate_model"
        bl_label = "Validate Model"
        bl_description = "Check IFC model integrity before running compliance checks"

        def execute(self, context):
            from .ifc_integration import load_from_blender_scene, validate_model
            props = _get_props(context)
            scope = props.scope if props else "entire_model"
            elements = load_from_blender_scene(scope=scope)
            result = validate_model(elements)
            if props:
                props.elements_loaded = len(elements)
            _ui._validation_warnings[:] = result.get("warnings", [])
            status = "OK" if result["valid"] else "ISSUES FOUND"
            self.report(
                {"INFO"} if result["valid"] else {"WARNING"},
                f"ARC Validate: {status} — {len(elements)} elements, "
                f"{len(result['errors'])} errors, {len(result['warnings'])} warnings.",
            )
            return {"FINISHED"}

    # ------------------------------------------------------------------
    # Show Compliance Volumes
    # ------------------------------------------------------------------

    class ARC_OT_ShowVolumes(Operator):
        bl_idname = "arc.show_volumes"
        bl_label = "Show Compliance Volumes"
        bl_description = "Generate translucent compliance volumes for failing elements"

        def execute(self, context):
            if not _ui._results_cache:
                self.report({"WARNING"}, "No results to visualise. Run a check first.")
                return {"CANCELLED"}
            from .visualizer import show_compliance_volumes
            props = _get_props(context)
            out_dir = _visualization_dir(props)
            paths = show_compliance_volumes(_ui._results_cache, out_dir=out_dir)
            self.report({"INFO"}, f"ARC: {len(paths)} compliance volumes shown.")
            return {"FINISHED"}

    class ARC_OT_ShowRuleVolume(Operator):
        bl_idname = "arc.show_rule_volume"
        bl_label = "Show Rule Volume"
        bl_description = "Show the visualization for this specific failed rule"

        guid: StringProperty(default="")
        rule_id: StringProperty(default="")

        def execute(self, context):
            matches = [
                r for r in _ui._results_cache
                if r.get("element_id") == self.guid and r.get("rule_id") == self.rule_id
            ]
            if not matches:
                self.report({"WARNING"}, "No matching result found.")
                return {"CANCELLED"}
            from .visualizer import show_compliance_volumes
            props = _get_props(context)
            out_dir = _visualization_dir(props)
            if props is not None:
                props.selected_guid = self.guid
                props.selected_issue_key = f"{self.guid}|{self.rule_id}"
            paths = show_compliance_volumes(matches, out_dir=out_dir)
            self.report({"INFO"}, f"ARC: showing {len(paths)} volume(s) for {self.rule_id}.")
            return {"FINISHED"}

    # ------------------------------------------------------------------
    # Toggle Heatmap (optimised: visibility toggle on pre-built overlays)
    # ------------------------------------------------------------------

    class ARC_OT_ToggleHeatmap(Operator):
        bl_idname = "arc.toggle_heatmap"
        bl_label = "Toggle Compliance Heatmap"
        bl_description = (
            "Colour-code the model by compliance status. "
            "Creates translucent overlay copies (originals are never modified)"
        )

        def execute(self, context):
            # If overlay collection already exists, toggle its visibility
            if _heatmap_collection_exists():
                if _heatmap_collection_is_visible():
                    _toggle_heatmap_visibility(False)
                    self.report({"INFO"}, "ARC Heatmap: hidden (originals visible).")
                else:
                    _toggle_heatmap_visibility(True)
                    self.report({"INFO"}, "ARC Heatmap: shown.")
            else:
                # Fallback: if missing (e.g. deleted), build synchronously
                for _ in _build_heatmap_generator(context):
                    pass
                _toggle_heatmap_visibility(True)
                self.report({"INFO"}, "ARC Heatmap: built and shown.")
            return {"FINISHED"}

    # ------------------------------------------------------------------
    # Isolate Issue (Navisworks-style)
    # ------------------------------------------------------------------

    class ARC_OT_IsolateIssue(Operator):
        bl_idname = "arc.isolate_issue"
        bl_label = "Isolate Issue"
        bl_description = (
            "Dim the entire model and spotlight the failing element "
            "and its blockers in contrasting colours (like Navisworks)"
        )

        guid: StringProperty(default="")
        rule_id: StringProperty(default="")

        _FAIL_COLOR = (0.91, 0.25, 0.21, 0.9)
        _BLOCKER_COLOR = (0.97, 0.51, 0.15, 0.9)
        _DIM_COLOR = (0.35, 0.35, 0.35, 0.15)

        # Class-level state for undo
        _isolated = False
        _hidden_objects: list = []

        def execute(self, context):
            if ARC_OT_IsolateIssue._isolated:
                self._restore(context)
                ARC_OT_IsolateIssue._isolated = False
                self.report({"INFO"}, "ARC: isolation cleared, model restored.")
                return {"FINISHED"}

            if not self.guid:
                self.report({"WARNING"}, "No element specified.")
                return {"CANCELLED"}

            # Find blockers for this issue
            blocker_guids = set()
            for r in _ui._results_cache:
                if r.get("element_id") == self.guid and r.get("rule_id") == self.rule_id:
                    details = r.get("details", {}) or {}
                    blockers = details.get("blocking_elements", [])
                    if isinstance(blockers, list):
                        blocker_guids.update(blockers)

            from .ui_panel import _get_object_guid

            ARC_OT_IsolateIssue._hidden_objects = []

            for obj in context.scene.objects:
                if obj.type != "MESH":
                    continue
                # Skip ARC-managed overlay objects
                if obj.get("ARC_heatmap_overlay") or obj.get("ARC_managed"):
                    continue
                guid = _get_object_guid(obj)
                if guid == self.guid:
                    # Target element - make fully visible, red tint
                    obj.color = self._FAIL_COLOR
                    obj.show_in_front = True
                    try:
                        obj.display_type = "SOLID"
                    except Exception:
                        pass
                elif guid in blocker_guids:
                    # Blocker - orange tint, visible
                    obj.color = self._BLOCKER_COLOR
                    obj.show_in_front = True
                else:
                    # Everything else - dim (mostly transparent)
                    ARC_OT_IsolateIssue._hidden_objects.append(
                        (obj.name, tuple(obj.color), obj.hide_viewport)
                    )
                    obj.color = self._DIM_COLOR

            ARC_OT_IsolateIssue._isolated = True

            # Also show the compliance volume for this specific issue
            matches = [
                r for r in _ui._results_cache
                if r.get("element_id") == self.guid and r.get("rule_id") == self.rule_id
            ]
            if matches:
                from .visualizer import show_compliance_volumes
                props = _get_props(context)
                out_dir = _visualization_dir(props)
                show_compliance_volumes(matches, out_dir=out_dir)

            # Focus on the element
            _focus_object_by_guid(context, self.guid)

            for area in context.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()

            self.report({"INFO"}, f"ARC: isolated issue {self.rule_id} on {self.guid[:12]}…")
            return {"FINISHED"}

        def _restore(self, context):
            """Restore all objects to their pre-isolation state."""
            for obj_name, orig_color, was_hidden in ARC_OT_IsolateIssue._hidden_objects:
                obj = bpy.data.objects.get(obj_name)
                if obj:
                    obj.color = orig_color
                    obj.hide_viewport = was_hidden

            # Reset target/blocker objects
            for obj in context.scene.objects:
                if obj.type != "MESH":
                    continue
                if obj.get("ARC_heatmap_overlay") or obj.get("ARC_managed"):
                    continue
                try:
                    obj.show_in_front = False
                except Exception:
                    pass

            ARC_OT_IsolateIssue._hidden_objects = []

            # Clear the compliance volumes shown for the isolated issue
            from .visualizer import clear_visualization
            props = _get_props(context)
            out_dir = _visualization_dir(props)
            clear_visualization(out_dir=out_dir)

            for area in context.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()

    # ------------------------------------------------------------------
    # Clear All Visualization
    # ------------------------------------------------------------------

    class ARC_OT_ClearVisualization(Operator):
        bl_idname = "arc.clear_visualization"
        bl_label = "Clear All Visualization"
        bl_description = (
            "Remove all ARC compliance volumes, heatmap overlays, "
            "and isolation highlights. Original model is fully restored."
        )

        def execute(self, context):
            from .visualizer import clear_visualization
            props = _get_props(context)
            out_dir = _visualization_dir(props)

            # Restore any isolation state
            if ARC_OT_IsolateIssue._isolated:
                ARC_OT_IsolateIssue._isolated = False
                for obj_name, orig_color, was_hidden in ARC_OT_IsolateIssue._hidden_objects:
                    obj = bpy.data.objects.get(obj_name)
                    if obj:
                        obj.color = orig_color
                        obj.hide_viewport = was_hidden
                for obj in context.scene.objects:
                    if obj.type == "MESH" and not obj.get("ARC_heatmap_overlay") and not obj.get("ARC_managed"):
                        try:
                            obj.show_in_front = False
                        except Exception:
                            pass
                ARC_OT_IsolateIssue._hidden_objects = []

            # Remove heatmap overlays
            _clear_heatmap_materials(context)

            # Remove compliance volumes
            clear_visualization(out_dir=out_dir)

            self.report({"INFO"}, "ARC: all visualization cleared, original model restored.")
            return {"FINISHED"}

    # ------------------------------------------------------------------
    # Export operators
    # ------------------------------------------------------------------

    class ARC_OT_ExportBCF(Operator):
        bl_idname = "arc.export_bcf"
        bl_label = "Export BCF"
        bl_description = "Export compliance results as a BCF issue archive"

        filepath: StringProperty(subtype="FILE_PATH")

        def invoke(self, context, event):
            d = _default_output_dir()
            self.filepath = str(d / _ts_filename("arc_issues", ".bcfzip"))
            context.window_manager.fileselect_add(self)
            return {"RUNNING_MODAL"}

        def execute(self, context):
            if not _ui._results_cache:
                self.report({"WARNING"}, "No results to export.")
                return {"CANCELLED"}
            from ..core.bcf_exporter import export_bcf
            export_bcf(_ui._results_cache, self.filepath)
            self.report({"INFO"}, f"BCF exported: {self.filepath}")
            return {"FINISHED"}

    class ARC_OT_ExportPDF(Operator):
        bl_idname = "arc.export_pdf"
        bl_label = "Export PDF"
        bl_description = "Export compliance audit report as PDF"

        filepath: StringProperty(subtype="FILE_PATH")

        def invoke(self, context, event):
            d = _default_output_dir()
            self.filepath = str(d / _ts_filename("arc_report", ".pdf"))
            context.window_manager.fileselect_add(self)
            return {"RUNNING_MODAL"}

        def execute(self, context):
            if not _ui._results_cache:
                self.report({"WARNING"}, "No results to export.")
                return {"CANCELLED"}
            from ..core.report_generator import generate_pdf_report
            try:
                props = _get_props(context)
                current_ifc = None
                try:
                    current_ifc = bpy.context.scene.BIMProperties.ifc_file
                except Exception:
                    current_ifc = None
                generate_pdf_report(
                    _ui._results_cache,
                    self.filepath,
                    metadata={
                        "model_path": str(current_ifc) if current_ifc else None,
                        "model_name": Path(str(current_ifc)).name if current_ifc else "Scene",
                        "source": "Blender Scene",
                        "mode": props.exec_mode if props else None,
                        "scope": props.scope if props else None,
                        "rule_preset": props.rule_preset if props else None,
                    },
                )
                self.report({"INFO"}, f"PDF exported: {self.filepath}")
            except RuntimeError as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            return {"FINISHED"}

    class ARC_OT_ExportHTML(Operator):
        bl_idname = "arc.export_html"
        bl_label = "Export HTML"
        bl_description = "Export compliance audit report as HTML"

        filepath: StringProperty(subtype="FILE_PATH")

        def invoke(self, context, event):
            d = _default_output_dir()
            self.filepath = str(d / _ts_filename("arc_report", ".html"))
            context.window_manager.fileselect_add(self)
            return {"RUNNING_MODAL"}

        def execute(self, context):
            if not _ui._results_cache:
                self.report({"WARNING"}, "No results to export.")
                return {"CANCELLED"}
            from ..core.report_generator import generate_html_report
            props = _get_props(context)
            current_ifc = None
            try:
                current_ifc = bpy.context.scene.BIMProperties.ifc_file
            except Exception:
                current_ifc = None
            generate_html_report(
                _ui._results_cache,
                self.filepath,
                metadata={
                    "model_path": str(current_ifc) if current_ifc else None,
                    "model_name": Path(str(current_ifc)).name if current_ifc else "Scene",
                    "source": "Blender Scene",
                    "mode": props.exec_mode if props else None,
                    "scope": props.scope if props else None,
                    "rule_preset": props.rule_preset if props else None,
                },
            )
            self.report({"INFO"}, f"HTML exported: {self.filepath}")
            return {"FINISHED"}

    class ARC_OT_ExportJSON(Operator):
        bl_idname = "arc.export_json"
        bl_label = "Export JSON"
        bl_description = "Export machine-readable results as JSON"

        filepath: StringProperty(subtype="FILE_PATH")

        def invoke(self, context, event):
            d = _default_output_dir()
            self.filepath = str(d / _ts_filename("arc_results", ".json"))
            context.window_manager.fileselect_add(self)
            return {"RUNNING_MODAL"}

        def execute(self, context):
            if not _ui._results_cache:
                self.report({"WARNING"}, "No results to export.")
                return {"CANCELLED"}
            from ..core.report_generator import generate_json_report
            props = _get_props(context)
            current_ifc = None
            try:
                current_ifc = bpy.context.scene.BIMProperties.ifc_file
            except Exception:
                current_ifc = None
            generate_json_report(
                _ui._results_cache,
                self.filepath,
                metadata={
                    "model_path": str(current_ifc) if current_ifc else None,
                    "model_name": Path(str(current_ifc)).name if current_ifc else "Scene",
                    "source": "Blender Scene",
                    "mode": props.exec_mode if props else None,
                    "scope": props.scope if props else None,
                    "rule_preset": props.rule_preset if props else None,
                },
            )
            self.report({"INFO"}, f"JSON exported: {self.filepath}")
            return {"FINISHED"}

    class ARC_OT_ExportReviewLog(Operator):
        bl_idname = "arc.export_review_log"
        bl_label = "Export Review Log"
        bl_description = "Export issue review notes and planning data as JSON"

        filepath: StringProperty(subtype="FILE_PATH")

        def invoke(self, context, event):
            d = _default_output_dir()
            self.filepath = str(d / _ts_filename("arc_review_log", ".json"))
            context.window_manager.fileselect_add(self)
            return {"RUNNING_MODAL"}

        def execute(self, context):
            props = _get_props(context)
            if props is None:
                return {"CANCELLED"}
            payload = {
                "selected_issue_key": props.selected_issue_key,
                "review_log": _get_review_map(props),
            }
            out = Path(self.filepath)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(payload, indent=2), encoding="utf8")
            self.report({"INFO"}, f"Review log exported: {self.filepath}")
            return {"FINISHED"}

    # ------------------------------------------------------------------
    # Zoom to element
    # ------------------------------------------------------------------

    class ARC_OT_ZoomToElement(Operator):
        bl_idname = "arc.zoom_to_element"
        bl_label = "Zoom To Element"
        bl_description = "Focus the 3D viewport on this element"

        guid: StringProperty(default="")

        def execute(self, context):
            target = _focus_object_by_guid(context, self.guid)
            if target is None:
                self.report({"WARNING"}, "Object not found in scene.")
                return {"CANCELLED"}
            return {"FINISHED"}

    class ARC_OT_InspectIssue(Operator):
        bl_idname = "arc.inspect_issue"
        bl_label = "Inspect Issue"
        bl_description = "Select the issue element, focus it in the viewport, and pin its details"

        guid: StringProperty(default="")
        rule_id: StringProperty(default="")

        def execute(self, context):
            props = _get_props(context)
            if props is not None:
                props.selected_guid = self.guid
                props.selected_issue_key = f"{self.guid}|{self.rule_id}"
                _load_review_fields(props, props.selected_issue_key)
            target = _focus_object_by_guid(context, self.guid)
            if target is None:
                self.report({"WARNING"}, "Object not found in scene.")
                return {"CANCELLED"}
            return {"FINISHED"}

    class ARC_OT_SaveIssueReview(Operator):
        bl_idname = "arc.save_issue_review"
        bl_label = "Save Issue Review"
        bl_description = "Save note, status, and assignee for the selected issue"

        def execute(self, context):
            props = _get_props(context)
            if props is None or not props.selected_issue_key:
                self.report({"WARNING"}, "No issue is selected for review.")
                return {"CANCELLED"}
            review_map = _get_review_map(props)
            review_map[props.selected_issue_key] = {
                "status": props.review_status,
                "assignee": props.review_assignee,
                "note": props.review_note,
            }
            _set_review_map(props, review_map)
            self.report({"INFO"}, "Issue review saved.")
            return {"FINISHED"}

    class ARC_OT_ClearIssueReview(Operator):
        bl_idname = "arc.clear_issue_review"
        bl_label = "Clear Issue Review"
        bl_description = "Remove saved review data for the selected issue"

        def execute(self, context):
            props = _get_props(context)
            if props is None or not props.selected_issue_key:
                self.report({"WARNING"}, "No issue is selected for review.")
                return {"CANCELLED"}
            review_map = _get_review_map(props)
            review_map.pop(props.selected_issue_key, None)
            _set_review_map(props, review_map)
            props.review_status = "open"
            props.review_assignee = ""
            props.review_note = ""
            self.report({"INFO"}, "Issue review cleared.")
            return {"FINISHED"}

    # ------------------------------------------------------------------
    # Create BCF Issue for one element
    # ------------------------------------------------------------------

    class ARC_OT_CreateBCFIssue(Operator):
        bl_idname = "arc.create_bcf_issue"
        bl_label = "Create BCF Issue"
        bl_description = "Export a BCF issue for this specific element and rule"

        guid: StringProperty(default="")
        rule_id: StringProperty(default="")

        def execute(self, context):
            issue_results = [
                r for r in _ui._results_cache
                if r.get("element_id") == self.guid and r.get("rule_id") == self.rule_id
            ]
            if not issue_results:
                self.report({"WARNING"}, "No matching result found.")
                return {"CANCELLED"}
            props = _get_props(context)
            d = Path(props.results_dir) if props and getattr(props, "results_dir", "") else _default_output_dir()
            d.mkdir(parents=True, exist_ok=True)
            path = str(d / _ts_filename(f"issue_{self.rule_id}", ".bcfzip"))
            from ..core.bcf_exporter import export_bcf
            export_bcf(issue_results, path)
            self.report({"INFO"}, f"BCF issue exported: {path}")
            return {"FINISHED"}

    # ------------------------------------------------------------------
    # Browse / Load existing results
    # ------------------------------------------------------------------

    class ARC_OT_BrowseResultsDir(Operator):
        bl_idname = "arc.browse_results_dir"
        bl_label = "Choose Results Directory"

        directory: StringProperty(subtype="DIR_PATH", default="")

        def invoke(self, context, event):
            try:
                props = _get_props(context)
                if props and props.results_dir:
                    self.directory = props.results_dir
            except Exception:
                pass
            context.window_manager.fileselect_add(self)
            return {"RUNNING_MODAL"}

        def execute(self, context):
            props = _get_props(context)
            if props is not None:
                props.results_dir = self.directory
                self.report({"INFO"}, f"Results directory set: {self.directory}")
            return {"FINISHED"}


    class ARC_OT_LoadResults(Operator):
        bl_idname = "arc.load_results"
        bl_label = "Load Existing Results"

        directory: StringProperty(subtype="DIR_PATH", default="")

        def invoke(self, context, event):
            try:
                props = _get_props(context)
                if props and props.results_dir:
                    self.directory = props.results_dir
            except Exception:
                pass
            context.window_manager.fileselect_add(self)
            return {"RUNNING_MODAL"}

        def execute(self, context):
            import json
            from pathlib import Path

            props = _get_props(context)
            dirpath = Path(self.directory or (props.results_dir if props else ""))
            if not dirpath.exists() or not dirpath.is_dir():
                self.report({"ERROR"}, "Chosen path is not a directory")
                return {"CANCELLED"}

            json_files = sorted(dirpath.glob("*.json"))
            if not json_files:
                self.report({"WARNING"}, "No JSON result files found in the chosen directory")
                return {"CANCELLED"}

            loaded = []
            file_metadata = []
            for jf in json_files:
                try:
                    with jf.open("r", encoding="utf8") as fh:
                        data = json.load(fh)
                    results, metadata = _extract_results_and_metadata(data)
                    if results:
                        loaded.extend(results)
                    if metadata:
                        file_metadata.append(metadata)
                except Exception:
                    continue

            # Determine current IFC path if available
            current_ifc = None
            try:
                import bpy as _bpy
                try:
                    current_ifc = _bpy.context.scene.BIMProperties.ifc_file
                except Exception:
                    current_ifc = None
            except Exception:
                current_ifc = None

            # Validate mapping only when the saved report includes model metadata.
            mismatches = []
            for md in file_metadata:
                model_path = md.get("model_path") or md.get("ifc_file") or md.get("ifc_path")
                if current_ifc and model_path:
                    try:
                        if Path(model_path).name != Path(current_ifc).name and Path(model_path) != Path(current_ifc):
                            mismatches.append(("report", str(model_path)))
                    except Exception:
                        mismatches.append(("report", str(model_path)))

            if mismatches:
                msg = f"Loaded {len(loaded)} files — {len(mismatches)} did not match current IFC."
                self.report({"ERROR"}, msg)
                for r in mismatches[:10]:
                    self.report({"ERROR"}, f"Mismatch: model_path={r[1]} (rule {r[0]})")
                return {"CANCELLED"}

            # Populate results cache and update UI props
            from . import ui_panel as _ui

            _ui._results_cache[:] = [r for r in loaded]
            if props is not None:
                props.last_run_ts = f"Loaded from {dirpath.name}"
                props.total_checks = len(_ui._results_cache)
                props.pass_count = sum(1 for r in _ui._results_cache if r.get("status") == "PASS")
                props.fail_count = sum(1 for r in _ui._results_cache if r.get("status") == "FAIL")
                props.inconcl_count = sum(1 for r in _ui._results_cache if r.get("status") == "INCONCLUSIVE")

            self.report({"INFO"}, f"Loaded {len(_ui._results_cache)} results from {dirpath}")
            return {"FINISHED"}


    class ARC_OT_InitUserRules(Operator):
        bl_idname = "arc.init_user_rules"
        bl_label = "Initialize User Rules"
        bl_description = "Create or choose a user-writable rules folder and copy packaged rules into it"

        directory: StringProperty(subtype="DIR_PATH", default="")

        def invoke(self, context, event):
            try:
                props = _get_props(context)
                if props and props.rules_dir:
                    self.directory = props.rules_dir
            except Exception:
                pass
            context.window_manager.fileselect_add(self)
            return {"RUNNING_MODAL"}

        def execute(self, context):
            from pathlib import Path
            try:
                from .install import ensure_user_rules, get_default_user_rules_dir
            except Exception:
                ensure_user_rules = None

            dest = None
            if self.directory:
                dest = Path(self.directory)
            else:
                dest = get_default_user_rules_dir() if 'get_default_user_rules_dir' in globals() else None

            try:
                if dest is not None and ensure_user_rules is not None:
                    dest = ensure_user_rules(dest)
            except Exception as exc:
                self.report({"ERROR"}, f"Failed to initialise rules: {exc}")
                return {"CANCELLED"}

            props = _get_props(context)
            if props is not None and dest is not None:
                props.rules_dir = str(dest)

            self.report({"INFO"}, f"User rules initialised at: {dest}")
            return {"FINISHED"}


    class ARC_OT_ExportRulePack(Operator):
        bl_idname = "arc.export_rule_pack"
        bl_label = "Export Rule Pack"
        bl_description = "Export user-editable rules as a zip pack"

        filepath: StringProperty(subtype="FILE_PATH")

        def invoke(self, context, event):
            d = _default_output_dir()
            self.filepath = str(d / _ts_filename("arc_rule_pack", ".zip"))
            context.window_manager.fileselect_add(self)
            return {"RUNNING_MODAL"}

        def execute(self, context):
            from pathlib import Path
            import zipfile

            props = _get_props(context)

            # Determine rules source: prefer user rules dir, else packaged rules
            try:
                from .install import get_default_user_rules_dir
            except Exception:
                get_default_user_rules_dir = None

            packaged_rules = Path(__file__).resolve().parent.parent / "core" / "rules"
            rules_base = None
            if props and getattr(props, "rules_dir", ""):
                cand = Path(props.rules_dir)
                if _has_rule_content(cand):
                    rules_base = cand
            if rules_base is None and get_default_user_rules_dir is not None:
                cand = get_default_user_rules_dir()
                if _has_rule_content(cand):
                    rules_base = cand
            if rules_base is None:
                rules_base = packaged_rules

            exported = 0
            try:
                with zipfile.ZipFile(self.filepath, "w", zipfile.ZIP_DEFLATED) as zf:
                    for sub in ("json_rules", "python_rules"):
                        src = rules_base / sub
                        if not src.exists():
                            continue
                        for f in src.rglob("*"):
                            if f.is_file():
                                arcname = Path("rules") / sub / f.relative_to(src)
                                zf.write(str(f), arcname.as_posix())
                                exported += 1

                self.report({"INFO"}, f"Exported {exported} files to {self.filepath}")
                return {"FINISHED"}
            except Exception as exc:
                self.report({"ERROR"}, f"Export failed: {exc}")
                return {"CANCELLED"}


    class ARC_OT_ImportRulePack(Operator):
        bl_idname = "arc.import_rule_pack"
        bl_label = "Import Rule Pack"
        bl_description = "Import a zipped rule pack into your user rules folder (skips existing files)"

        filepath: StringProperty(subtype="FILE_PATH")

        def invoke(self, context, event):
            context.window_manager.fileselect_add(self)
            return {"RUNNING_MODAL"}

        def execute(self, context):
            from pathlib import Path
            import zipfile
            import shutil

            try:
                from .install import ensure_user_rules, get_default_user_rules_dir
            except Exception:
                ensure_user_rules = None
                get_default_user_rules_dir = None

            props = _get_props(context)

            # Resolve destination rules folder
            dest = None
            if props and getattr(props, "rules_dir", ""):
                dest = Path(props.rules_dir)
                if not dest.exists():
                    try:
                        if ensure_user_rules is not None:
                            dest = ensure_user_rules(dest)
                    except Exception:
                        pass

            if dest is None:
                try:
                    default = get_default_user_rules_dir() if get_default_user_rules_dir is not None else None
                except Exception:
                    default = None
                if default is None:
                    default = Path.home() / ".arc" / "rules"
                if ensure_user_rules is not None:
                    dest = ensure_user_rules(default)
                else:
                    dest = default

            dest.mkdir(parents=True, exist_ok=True)

            added = 0
            skipped = 0
            try:
                with zipfile.ZipFile(self.filepath, "r") as zf:
                    for member in zf.namelist():
                        if member.endswith("/"):
                            continue
                        nm = member.replace("\\", "/")
                        parts = nm.split("/")
                        if parts[0] == "rules":
                            rel = Path("/".join(parts[1:]))
                        else:
                            rel = Path(nm)
                        target = dest / rel
                        target.parent.mkdir(parents=True, exist_ok=True)
                        if target.exists():
                            skipped += 1
                            continue
                        with zf.open(member) as sf, open(target, "wb") as df:
                            shutil.copyfileobj(sf, df)
                        added += 1

                # Save chosen rules dir into props
                if props is not None:
                    props.rules_dir = str(dest)

                self.report({"INFO"}, f"Imported {added} files, {skipped} skipped. Destination: {dest}")
                return {"FINISHED"}
            except Exception as exc:
                self.report({"ERROR"}, f"Import failed: {exc}")
                return {"CANCELLED"}

    # ------------------------------------------------------------------
    # Install dependencies
    # ------------------------------------------------------------------

    class ARC_OT_InstallDeps(Operator):
        bl_idname = "arc.install_deps"
        bl_label = "Install ARC Dependencies"
        bl_description = "Install bundled Python packages (networkx, reportlab, etc.)"

        def execute(self, context):
            try:
                from . import install as arc_install
            except Exception:
                self.report({"ERROR"}, "Installer not available")
                return {"CANCELLED"}
            success, msg = arc_install.ensure_deps()
            if success:
                self.report({"INFO"}, msg)
                return {"FINISHED"}
            else:
                self.report({"ERROR"}, msg)
                return {"CANCELLED"}

    # ------------------------------------------------------------------
    # All operator classes
    # ------------------------------------------------------------------

    _OPERATOR_CLASSES = [
        ARC_OT_RunChecks,
        ARC_OT_CancelChecks,
        ARC_OT_EnableAllCategories,
        ARC_OT_DisableAllCategories,
        ARC_OT_ToggleRuleEnabled,
        ARC_OT_EnableAllRules,
        ARC_OT_DisableAllRules,
        ARC_OT_ValidateModel,
        ARC_OT_ShowVolumes,
        ARC_OT_ShowRuleVolume,
        ARC_OT_ToggleHeatmap,
        ARC_OT_IsolateIssue,
        ARC_OT_ClearVisualization,
        ARC_OT_ExportBCF,
        ARC_OT_ExportPDF,
        ARC_OT_ExportHTML,
        ARC_OT_ExportJSON,
        ARC_OT_ExportReviewLog,
        ARC_OT_InspectIssue,
        ARC_OT_SaveIssueReview,
        ARC_OT_ClearIssueReview,
        ARC_OT_ZoomToElement,
        ARC_OT_CreateBCFIssue,
        ARC_OT_BrowseResultsDir,
        ARC_OT_LoadResults,
        ARC_OT_InstallDeps,
        ARC_OT_InitUserRules,
        ARC_OT_ImportRulePack,
        ARC_OT_ExportRulePack,
    ]

else:
    # Headless stubs
    ARC_OT_RunChecks = None
    ARC_OT_InstallDeps = None
    _OPERATOR_CLASSES = []
