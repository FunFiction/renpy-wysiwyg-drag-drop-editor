# Save layer: multi-line statements, static ATL blocks with comments,
# `with`, `show expression ... as`, `behind` - everything must survive a
# clean save untouched and round-trip exactly after an edit.

define config.developer = True

image eileen happy = Solid("#f00", xsize=300, ysize=600)
image lucy mad = Solid("#0f0", xsize=300, ysize=600)
image marc neutral = Solid("#ff0", xsize=300, ysize=600)
image bg room = Solid("#00f")

label main_menu:
    return

init python:
    def selftest():
        import io, os, traceback
        out = []
        try:
            wysiwyg_toggle()
            wysiwyg_import_scene()
            out.append("IMPORT: " + str(store.wysiwyg_status))
            wysiwyg_save_changes()
            out.append("CLEAN-SAVE: " + str(store.wysiwyg_status))
            for c in store.wysiwyg_chars:
                if not c.get("locked"):
                    c["x"] = wysiwyg_float(c.get("x", 0), 0) + 5
            store.wysiwyg_saved_runtime = False
            wysiwyg_save_changes()
            out.append("SAVE: " + str(store.wysiwyg_status))
            # second save of the SAME line in one session: the trailing
            # comment now lives on an editor-written (synthetic) line and
            # must still be carried over
            for c in store.wysiwyg_chars:
                if c.get("tag") == "eileen":
                    c["x"] = wysiwyg_float(c.get("x", 0), 0) + 5
            store.wysiwyg_saved_runtime = False
            wysiwyg_save_changes()
            out.append("SAVE-2: " + str(store.wysiwyg_status))
            wysiwyg_toggle()
        except Exception:
            out.append("EXC:\n" + traceback.format_exc())
        with io.open(os.path.join(config.gamedir, "selftest_result.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        renpy.quit()

label start:
    scene bg room
    show eileen happy at Transform(
        xpos=400,
        ypos=300)  # keep me
    show lucy mad:
        xpos 800
# stray block comment at column zero
        ypos 300
        # indented block comment
    show expression Solid("#ff0", xsize=120, ysize=240) as blob at Transform(xpos=600, ypos=300) with dissolve
    show marc neutral at Transform(xpos=1500, ypos=300) behind lucy
    pause 0.3
    $ selftest()
    return
