# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: GPL-3.0-or-later

"""ARC-Spatial — Blender 4.2+ extension for spatial compliance visualization.

Registers Blender operators, UI panels, and keyboard shortcuts.
Depends on arc.core for all domain logic.

When imported outside Blender (no bpy), this module is inert.
"""
from __future__ import annotations

_addon_keymaps: list = []


def register():
    try:
        import bpy
        from . import operators, ui_panel

        # --- Register classes ---
        all_classes = list(ui_panel._PANEL_CLASSES) + list(operators._OPERATOR_CLASSES)
        for cls in all_classes:
            if cls is not None:
                try:
                    bpy.utils.register_class(cls)
                except Exception as exc:
                    print(f"ARC: failed to register {cls}: {exc}")

        # --- Attach PropertyGroup to Scene ---
        if ui_panel.ARC_Props is not None:
            bpy.types.Scene.arc = bpy.props.PointerProperty(type=ui_panel.ARC_Props)

        # --- Keyboard shortcuts ---
        _register_keymaps()

        # --- Wheel deps fallback ---
        try:
            import reportlab
        except ImportError:
            try:
                import threading
                from . import install as _arc_install

                def _run_installer():
                    try:
                        success, msg = _arc_install.ensure_deps()
                        print(f"ARC wheel fallback install: {msg}")
                    except Exception as exc:
                        print(f"ARC wheel fallback error: {exc}")

                threading.Thread(target=_run_installer, daemon=True).start()
            except Exception:
                pass

        from .. import __version__
        print(f"Spatial ARC {__version__} registered successfully.")

    except ImportError:
        pass
    except Exception as exc:
        print(f"ARC register() failed: {exc}")


def unregister():
    try:
        import bpy
        from . import operators, ui_panel

        _unregister_keymaps()

        if hasattr(bpy.types.Scene, "arc"):
            del bpy.types.Scene.arc

        all_classes = list(ui_panel._PANEL_CLASSES) + list(operators._OPERATOR_CLASSES)
        for cls in reversed(all_classes):
            if cls is not None:
                try:
                    bpy.utils.unregister_class(cls)
                except Exception:
                    pass

    except ImportError:
        pass
    except Exception as exc:
        print(f"ARC unregister() failed: {exc}")


def _register_keymaps():
    try:
        import bpy
        wm = bpy.context.window_manager
        kc = wm.keyconfigs.addon
        if kc is None:
            return

        km = kc.keymaps.new(name="3D View", space_type="VIEW_3D")

        shortcuts = [
            ("arc.run_checks",          "R", "Ctrl+Shift+R — Run Model Check"),
            ("arc.clear_visualization", "C", "Ctrl+Shift+C — Clear Visualization"),
            ("arc.toggle_heatmap",      "H", "Ctrl+Shift+H — Toggle Heatmap"),
            ("arc.export_bcf",          "E", "Ctrl+Shift+E — Export BCF"),
        ]

        for idname, key, _ in shortcuts:
            kmi = km.keymap_items.new(
                idname,
                type=key,
                value="PRESS",
                ctrl=True,
                shift=True,
            )
            _addon_keymaps.append((km, kmi))

    except Exception as exc:
        print(f"ARC: keymaps not registered: {exc}")


def _unregister_keymaps():
    for km, kmi in _addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    _addon_keymaps.clear()
