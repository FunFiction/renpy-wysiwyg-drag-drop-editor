# A save with no edits must leave every source file byte-for-byte
# identical - the strongest form of the "clean save writes nothing" rule.

define config.developer = True

image eileen happy = Solid("#f00", xsize=300, ysize=600)
image lucy mad = Solid("#0f0", xsize=300, ysize=600)
image bg room = Solid("#00f")

transform wobble:
    xpos 300
    ypos 200
    linear 0.5 xpos 340
    repeat

label main_menu:
    return

init python:
    def selftest():
        import io, os, traceback
        out = []
        try:
            path = os.path.join(config.gamedir, "script.rpy")
            with io.open(path, "r", encoding="utf-8") as f:
                before = f.read()
            wysiwyg_toggle()
            wysiwyg_import_scene()
            out.append("IMPORT: " + str(store.wysiwyg_status))
            wysiwyg_save_changes()
            out.append("SAVE: " + str(store.wysiwyg_status))
            wysiwyg_toggle()
            with io.open(path, "r", encoding="utf-8") as f:
                after = f.read()
            out.append("IDENTICAL: " + repr(before == after))
            bdir = os.path.join(config.gamedir, "wysiwyg_backups")
            out.append("BACKUPS-CREATED: " + repr(os.path.isdir(bdir)))
        except Exception:
            out.append("EXC:\n" + traceback.format_exc())
        with io.open(os.path.join(config.gamedir, "selftest_result.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        renpy.quit()

label start:
    scene bg room
    show eileen happy at Transform(xpos=400, ypos=300)  # a comment to preserve
    show lucy mad at wobble
    pause 0.3
    $ selftest()
    return
