# The UI actions no other case calls directly (found by a coverage audit
# of Function(...) targets in the screens): the inline edit fields with
# their parsing and validation, the four reset flavors, the scale lock,
# slider drag/release undo batching, Clear Editor, and the close-gate
# buttons. Run 1 writes an editor-style line so run 2 has non-default
# "original" values to reset back to.

define config.developer = True

image eileen happy = Solid("#f00", xsize=300, ysize=600)
image bg room = Solid("#00f")

label main_menu:
    return

init python:
    def selftest():
        import io, os, traceback
        out = []
        second = False
        script_path = os.path.join(config.gamedir, "script.rpy")
        try:
            with io.open(script_path, "r", encoding="utf-8") as f:
                body = f.read()
            # assembled so no source line matches itself
            second = ("xanch" + "or=0.5") in body
            wysiwyg_toggle()
            wysiwyg_import_scene()
            out.append("IMPORT: " + str(store.wysiwyg_status))
            store.wysiwyg_selected_tag = "eileen"
            c = wysiwyg_find_char("eileen")
            f_ = wysiwyg_float

            if not second:
                wysiwyg_set_char_transform("eileen", "rotate", 30)
                wysiwyg_set_char_transform("eileen", "xzoom", 0.8)
                wysiwyg_set_char_transform("eileen", "alpha", 0.7)
                store.wysiwyg_saved_runtime = False
                wysiwyg_save_changes()
                out.append("SAVE: " + str(store.wysiwyg_status))
                wysiwyg_toggle()
            else:
                # A) inline edit fields: parsing, validation, cancel
                wysiwyg_begin_edit("pos")
                store.wysiwyg_edit_buffer = "500, 400"
                wysiwyg_commit_edit()
                out.append("POS-EDIT: " + str(int(c.get("parsed_center_x"))) + "," + str(int(c.get("parsed_center_y"))))
                wysiwyg_begin_edit("rot")
                store.wysiwyg_edit_buffer = "abc"
                wysiwyg_commit_edit()
                out.append("ROT-BAD: " + str(store.wysiwyg_status) + " rotate=" + str(f_(c.get("rotate"), 0.0)))
                wysiwyg_begin_edit("rot")
                store.wysiwyg_edit_buffer = "45,5"
                wysiwyg_commit_edit()
                out.append("ROT-COMMA: " + str(f_(c.get("rotate"), 0.0)))
                wysiwyg_begin_edit("scale")
                store.wysiwyg_edit_buffer = "0.6"
                wysiwyg_commit_edit()
                out.append("SCALE-EDIT: " + str(f_(c.get("xzoom"), 1.0)) + "/" + str(f_(c.get("yzoom"), 1.0)))
                wysiwyg_begin_edit("pos")
                _before = int(c.get("parsed_center_x"))
                store.wysiwyg_edit_buffer = "111, 222"
                wysiwyg_cancel_edit()
                out.append("POS-CANCEL: " + str(int(c.get("parsed_center_x")) == _before) + " field=" + repr(store.wysiwyg_edit_field))
                wysiwyg_begin_edit("withsec")
                store.wysiwyg_edit_buffer = "0.75"
                wysiwyg_commit_edit()
                out.append("WITHSEC: " + str(c.get("with_expr")))
                wysiwyg_begin_edit("withsec")
                store.wysiwyg_edit_buffer = "0"
                wysiwyg_commit_edit()
                out.append("WITHSEC-BAD: " + str(store.wysiwyg_status) + " expr=" + str(c.get("with_expr")))

                # B) the four reset flavors
                wysiwyg_reset_selected_transform()
                out.append("RESET-ORIG: rot=" + str(f_(c.get("rotate"), 0)) + " zoom=" + str(f_(c.get("xzoom"), 1)) + " alpha=" + str(f_(c.get("alpha"), 1)))
                wysiwyg_set_char_transform("eileen", "rotate", 90)
                wysiwyg_reset_selected_transform_to_defaults()
                out.append("RESET-DEF: rot=" + str(f_(c.get("rotate"), 0)) + " zoom=" + str(f_(c.get("xzoom"), 1)) + " alpha=" + str(f_(c.get("alpha"), 1)))
                wysiwyg_set_char_transform("eileen", "filter_brightness", 0.4)
                wysiwyg_set_char_transform("eileen", "filter_hue", 45)
                wysiwyg_reset_selected_color_filter_key("eileen", "filter_hue")
                out.append("FILTER-KEY: hue=" + str(f_(c.get("filter_hue"), 0)) + " bright=" + str(f_(c.get("filter_brightness"), 0)))
                wysiwyg_reset_selected_color_filters_to_defaults()
                out.append("FILTER-DEF: bright=" + str(f_(c.get("filter_brightness"), 0)))
                wysiwyg_set_motion_fx("eileen", "float")
                wysiwyg_reset_selected_motion_fx_to_defaults()
                out.append("MOTION-DEF: " + str(c.get("motion_fx")))

                # C) scale lock: unlinked leaves the other axis alone,
                # re-locking snaps them together
                wysiwyg_toggle_scale_lock()
                wysiwyg_set_char_transform("eileen", "xzoom", 0.5)
                out.append("UNLINKED: " + str(f_(c.get("xzoom"), 1)) + "/" + str(f_(c.get("yzoom"), 1)))
                wysiwyg_toggle_scale_lock()
                out.append("RELINKED: " + str(f_(c.get("xzoom"), 1)) + "/" + str(f_(c.get("yzoom"), 1)))

                # D) slider grab: many drag ticks, one undo entry on release
                wysiwyg_set_char_transform("eileen", "rotate", 10)
                _undo_before = len(store.wysiwyg_undo_stack)
                for _v in (20, 35, 50, 66, 77):
                    c["rotate"] = _v
                    wysiwyg_drag_transform_slider("eileen", "rotate")
                wysiwyg_release_transform_slider("eileen", "rotate")
                out.append("SLIDER-UNDO-ENTRIES: " + str(len(store.wysiwyg_undo_stack) - _undo_before))
                wysiwyg_undo_move()
                out.append("SLIDER-UNDONE: " + str(f_(c.get("rotate"), 0)))

                # E) Clear Editor: state wiped, file untouched
                with io.open(script_path, "r", encoding="utf-8") as f:
                    _before_clear = f.read()
                wysiwyg_nudge_selected(25, 0)
                wysiwyg_reset_editor()
                with io.open(script_path, "r", encoding="utf-8") as f:
                    _after_clear = f.read()
                out.append("CLEAR: chars=" + str(len(store.wysiwyg_chars)) + " file_same=" + str(_before_clear == _after_clear) + " status=" + str(store.wysiwyg_status))

                # F) close-gate buttons: save-from-gate, then discard path
                wysiwyg_import_scene()
                store.wysiwyg_selected_tag = "eileen"
                wysiwyg_nudge_selected(5, 0)
                wysiwyg_request_close()
                out.append("GATE-UP: " + str(store.wysiwyg_confirm_close is not None) + " active=" + str(store.wysiwyg_active))
                wysiwyg_confirm_close_save()
                out.append("GATE-SAVE: " + str(store.wysiwyg_status) + " active=" + str(store.wysiwyg_active) + " gate=" + repr(store.wysiwyg_confirm_close))
                wysiwyg_request_close()
                out.append("CLOSED-CLEAN: active=" + str(store.wysiwyg_active))
                with io.open(script_path, "r", encoding="utf-8") as f:
                    _after_save = f.read()
                wysiwyg_toggle()
                wysiwyg_import_scene()
                store.wysiwyg_selected_tag = "eileen"
                wysiwyg_nudge_selected(40, 0)
                wysiwyg_request_close()
                wysiwyg_confirm_close_discard()
                with io.open(script_path, "r", encoding="utf-8") as f:
                    _after_discard = f.read()
                out.append("GATE-DISCARD: active=" + str(store.wysiwyg_active) + " file_same=" + str(_after_save == _after_discard))
        except Exception:
            out.append("EXC:\n" + traceback.format_exc())
        target = "selftest_result.txt" if second else "selftest_run1.txt"
        with io.open(os.path.join(config.gamedir, target), "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        if not second:
            with io.open(os.path.join(config.gamedir, "selftest_result.txt"), "w", encoding="utf-8") as f:
                f.write("RUN1-DONE (details in selftest_run1.txt)")
        renpy.quit()

label start:
    scene bg room
    show eileen happy at Transform(xpos=200, ypos=300)
    pause 0.3
    $ selftest()
