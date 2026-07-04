# The last 12 UI actions the coverage audit listed as untested: small
# state toggles, slider clamp callbacks, the uncertain-gate Show Code
# shortcut, and the master-layer hide. After this case, every
# Function(...) target in the screens is exercised by the suite.

define config.developer = True

image eileen happy = Solid("#f00", xsize=300, ysize=600)
image lucy mad = Solid("#0f0", xsize=300, ysize=600)
image bg room = Solid("#00f")

label main_menu:
    return

init python:
    def selftest():
        import io, os, traceback
        out = []
        try:
            f_ = wysiwyg_float
            wysiwyg_toggle()
            wysiwyg_import_scene()
            out.append("IMPORT: " + str(store.wysiwyg_status))
            c = wysiwyg_find_char("eileen")

            # 1) set_status
            wysiwyg_set_status("status probe")
            out.append("STATUS: " + str(store.wysiwyg_status))

            # 2) select_char cancels a half-typed edit field
            store.wysiwyg_selected_tag = "eileen"
            wysiwyg_begin_edit("rot")
            wysiwyg_select_char("lucy")
            out.append("SELECT: tag=" + str(store.wysiwyg_selected_tag) + " field=" + repr(store.wysiwyg_edit_field))
            wysiwyg_select_char("eileen")

            # 3) code panel toggle, both directions
            _p0 = store.wysiwyg_panel
            wysiwyg_toggle_code_panel()
            _p1 = store.wysiwyg_panel
            wysiwyg_toggle_code_panel()
            out.append("PANEL: " + str(_p0) + ">" + str(_p1) + ">" + str(store.wysiwyg_panel))

            # 4) the uncertain-gate "Show Code" shortcut
            store.wysiwyg_confirm_save = ["eileen"]
            wysiwyg_confirm_save_review()
            out.append("REVIEW: gate=" + repr(store.wysiwyg_confirm_save) + " panel=" + str(store.wysiwyg_panel))
            wysiwyg_toggle_code_panel()

            # 5) image browser open/close builds the rows and resets state
            wysiwyg_toggle_image_browser()
            _rows = WYSIWYG_RUNTIME.image_browser
            out.append("BROWSER: page=" + str(store.wysiwyg_char_page) + " rows=" + str(len(_rows) if _rows is not None else None))
            # 6) group fold/unfold
            wysiwyg_toggle_browser_group("zeta")
            _open1 = "zeta" in store.wysiwyg_browser_open_groups
            wysiwyg_toggle_browser_group("zeta")
            _open2 = "zeta" in store.wysiwyg_browser_open_groups
            out.append("GROUP: " + str(_open1) + ">" + str(_open2))
            wysiwyg_toggle_image_browser()
            out.append("BROWSER-CLOSED: page=" + str(store.wysiwyg_char_page))

            # 7) sepia bool toggle
            wysiwyg_toggle_char_bool("eileen", "filter_sepia")
            _s1 = bool(c.get("filter_sepia"))
            wysiwyg_toggle_char_bool("eileen", "filter_sepia")
            out.append("SEPIA: " + str(_s1) + ">" + str(bool(c.get("filter_sepia"))))

            # 8) preview hide toggle
            wysiwyg_toggle_preview_hidden("eileen")
            _h1 = bool(c.get("preview_hidden"))
            wysiwyg_toggle_preview_hidden("eileen")
            out.append("PREVIEW-HIDE: " + str(_h1) + ">" + str(bool(c.get("preview_hidden"))))

            # 9) color slider clamp callback
            c["filter_hue"] = 999.0
            c["filter_blur"] = -5.0
            wysiwyg_on_color_filter_change("eileen")
            out.append("FILTER-CLAMP: hue=" + str(f_(c.get("filter_hue"), 0)) + " blur=" + str(f_(c.get("filter_blur"), 0)))
            wysiwyg_reset_selected_color_filters_to_defaults()

            # 10) motion slider clamp callback
            c["motion_fx"] = " FLOAT "
            c["motion_fx_strength"] = 9.0
            wysiwyg_on_motion_fx_change("eileen")
            out.append("MOTION-CLAMP: fx=" + str(c.get("motion_fx")) + " strength=" + str(f_(c.get("motion_fx_strength"), 1)))
            wysiwyg_set_motion_fx("eileen", "none")

            # 11) reset_selected_position wrapper
            _cx0 = int(c.get("parsed_center_x"))
            wysiwyg_nudge_selected(30, 0)
            wysiwyg_reset_selected_position()
            out.append("RESET-POS: " + str(int(c.get("parsed_center_x")) == _cx0))

            # 12) hide_master_chars takes tracked tags off the master layer
            wysiwyg_hide_master_chars()
            try:
                _showing = set(renpy.get_showing_tags("master"))
            except Exception:
                _showing = set(["?"])
            out.append("MASTER-HIDDEN: " + str("eileen" not in _showing and "lucy" not in _showing))

            wysiwyg_toggle()
        except Exception:
            out.append("EXC:\n" + traceback.format_exc())
        with io.open(os.path.join(config.gamedir, "selftest_result.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        renpy.quit()

label start:
    scene bg room
    show eileen happy:
        xpos 100
        ypos 200
    show lucy mad at Transform(xpos=700, ypos=250)
    pause 0.3
    $ selftest()
