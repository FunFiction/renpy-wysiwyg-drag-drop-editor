# Color filters and flip round-trip: every filter is written into the
# saved line as a color-matrix/blur expression and parsed back by regex
# on the next import - the one place where the writer and the parser can
# silently drift apart. Run 1 applies filters + a horizontal flip and
# saves; run 2 re-imports from the rewritten file and compares values.
# (The run-2 marker below is assembled so no source line matches itself.)

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
        try:
            with io.open(os.path.join(config.gamedir, "script.rpy"), "r", encoding="utf-8") as f:
                body = f.read()
            second = ("matrix" + "color") in body
            wysiwyg_toggle()
            wysiwyg_import_scene()
            out.append("IMPORT: " + str(store.wysiwyg_status))
            store.wysiwyg_selected_tag = "eileen"
            c = wysiwyg_find_char("eileen")
            if second:
                # run 2: values parsed back from the rewritten line
                out.append("R2-BRIGHT: " + str(round(wysiwyg_float(c.get("filter_brightness"), 0.0), 3)))
                out.append("R2-SAT: " + str(round(wysiwyg_float(c.get("filter_saturation"), 1.0), 3)))
                out.append("R2-HUE: " + str(round(wysiwyg_float(c.get("filter_hue"), 0.0), 1)))
                out.append("R2-CONTRAST: " + str(round(wysiwyg_float(c.get("filter_contrast"), 1.0), 3)))
                out.append("R2-BLUR: " + str(round(wysiwyg_float(c.get("filter_blur"), 0.0), 1)))
                out.append("R2-SEPIA: " + str(bool(c.get("filter_sepia"))))
                out.append("R2-XZOOM: " + str(round(wysiwyg_float(c.get("xzoom"), 1.0), 3)))
                out.append("R2-LOCKED: " + repr(c.get("locked")))
                wysiwyg_toggle()
            else:
                # run 1: apply through the real UI functions and save
                wysiwyg_set_char_transform("eileen", "filter_brightness", 0.3)
                wysiwyg_set_char_transform("eileen", "filter_saturation", 0.5)
                wysiwyg_set_char_transform("eileen", "filter_hue", 90)
                wysiwyg_set_char_transform("eileen", "filter_contrast", 1.2)
                wysiwyg_set_char_transform("eileen", "filter_blur", 5)
                c["filter_sepia"] = True
                wysiwyg_flip_char("eileen", "xzoom")
                store.wysiwyg_saved_runtime = False
                wysiwyg_save_changes()
                out.append("SAVE: " + str(store.wysiwyg_status))
                wysiwyg_toggle()
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
    show eileen happy at Transform(xpos=400, ypos=300)
    pause 0.3
    $ selftest()
