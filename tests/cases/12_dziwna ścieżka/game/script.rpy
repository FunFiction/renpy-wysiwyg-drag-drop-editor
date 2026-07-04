# The whole project lives under a directory with a space and Polish
# diacritics in its name (the case directory), like a typical Windows
# user's "D:\Moje Gry\Nowa Gra": every path the editor touches - source
# rewrite, backups tree, debug log, motion fx file - crosses it.

define config.developer = True

image eileen happy = Solid("#f00", xsize=300, ysize=600)
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
            for c in store.wysiwyg_chars:
                if c.get("tag") == "eileen" and not c.get("locked"):
                    c["x"] = wysiwyg_float(c.get("x", 0), 0) + 30
                    c["motion_fx"] = "float"
            store.wysiwyg_saved_runtime = False
            wysiwyg_save_changes()
            out.append("SAVE: " + str(store.wysiwyg_status))
            out.append("MOTION-FILE: " + str(os.path.exists(os.path.join(config.gamedir, "wysiwyg_motion_fx.rpy"))))
            bdir = os.path.join(config.gamedir, "wysiwyg_backups")
            count = 0
            for root, dirs, files in os.walk(bdir):
                count += len(files)
            out.append("BACKUPS: " + str(count))
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
    pause 0.3
    $ selftest()
