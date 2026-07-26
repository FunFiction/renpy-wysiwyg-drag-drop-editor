# LF preservation: saving an all-LF file must not re-end every line with
# CRLF - only the edited line may change, and the backup must be an exact
# byte copy of the original.

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
            path = os.path.join(config.gamedir, "script.rpy")
            with io.open(path, "rb") as f:
                before = f.read()
            out.append("PRECOND-LF: " + repr(b"\r" not in before and b"\n" in before))
            wysiwyg_toggle()
            wysiwyg_import_scene()
            out.append("IMPORT: " + str(store.wysiwyg_status))
            for c in store.wysiwyg_chars:
                if not c.get("locked"):
                    c["x"] = wysiwyg_float(c.get("x", 0), 0) + 5
            store.wysiwyg_saved_runtime = False
            wysiwyg_save_changes()
            out.append("SAVE: " + str(store.wysiwyg_status))
            wysiwyg_toggle()
            with io.open(path, "rb") as f:
                after = f.read()
            out.append("EOL-PRESERVED: " + repr(b"\r" not in after))
            out.append("FILE-CHANGED: " + repr(after != before))
            # the pre-save backup must be byte-for-byte the original
            bdir = os.path.join(config.gamedir, "wysiwyg_backups")
            exact = False
            for root, dirs, files in os.walk(bdir):
                for name in files:
                    if not name.endswith(".bak"):
                        continue
                    with io.open(os.path.join(root, name), "rb") as f:
                        if f.read() == before:
                            exact = True
            out.append("BACKUP-EXACT: " + repr(exact))
        except Exception:
            out.append("EXC:\n" + traceback.format_exc())
        with io.open(os.path.join(config.gamedir, "selftest_result.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        renpy.quit()

label start:
    scene bg room
    show eileen happy at Transform(xpos=400, ypos=300)
    pause 0.3
    $ selftest()
    return
